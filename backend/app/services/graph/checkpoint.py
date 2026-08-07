"""LangGraph checkpointer backed by Valkey, with Postgres as the long backstop.

WHY THIS IS HAND-WRITTEN RATHER THAN `langgraph-checkpoint-redis`. That package
stores checkpoints as RedisJSON documents and finds them through a RediSearch
index queried by thread_id/checkpoint_ns/checkpoint_id. Memorystore for Valkey
blocks MODULE LOAD outright, so neither module can be present. Memorystore for
Redis Cluster 8.0+ looks like a way out — it has native JSON and exposes
FT.CREATE — but its search is a vector engine wearing RediSearch's command
names: a VECTOR field is required, only HASH keys can be indexed (not JSON), and
tag/numeric predicates are documented as unusable except alongside a vector
query. That is exactly the standalone metadata lookup the package performs, so
index creation would fail there too. Migrating buys nothing, which is why we
didn't.

What is left needs GET/SET/ZADD and nothing else, which every Valkey and Redis
variant supports identically. Writing it here also means it reuses the pool,
the key conventions and the degradation contract in app/core/valkey.py instead
of opening a second connection that respects none of them.

TWO TIERS, DIFFERENT JOBS.
  Valkey    every checkpoint and every pending write, for GRAPH_CHECKPOINT_TTL_S.
            This is what makes a turn resumable mid-node.
  Postgres  the latest checkpoint only, mirrored by app/services/turn_runner.py
            inside the same transaction that persists the turn. This is what
            makes a CONVERSATION resumable next week, after the TTL has expired
            or Memorystore has been flushed.
Hydration runs one way: a Valkey miss for a real conversation reads Postgres and
warms Valkey back up. A Postgres miss is simply a new conversation.

KEY LAYOUT mirrors app/services/turn_stream.py — `{thread_id}` is a cluster hash
tag, not formatting, so a conversation's checkpoints, buffer and event stream all
hash to one slot. Prod is Cluster Mode Disabled today and slots are irrelevant
there; the tags cost nothing and keep a later move to a clustered instance a
config flip rather than a key migration.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.valkey import get_client

logger = logging.getLogger(__name__)

# Bumped only if the on-disk shape below changes incompatibly. A row written by
# an older version is discarded rather than misread — losing a checkpoint costs
# one re-asked question, misreading one corrupts a build.
MIRROR_VERSION = 1


def _thread_prefix(thread_id: str) -> str:
    return f"chat:ckpt:{{{thread_id}}}"


def _checkpoint_key(thread_id: str, ns: str, checkpoint_id: str) -> str:
    return f"{_thread_prefix(thread_id)}:c:{ns}:{checkpoint_id}"


def _index_key(thread_id: str, ns: str) -> str:
    return f"{_thread_prefix(thread_id)}:i:{ns}"


def _writes_key(thread_id: str, ns: str, checkpoint_id: str) -> str:
    return f"{_thread_prefix(thread_id)}:w:{ns}:{checkpoint_id}"


def _is_conversation_id(thread_id: str) -> bool:
    """Whether this thread maps to a persisted conversation.

    Guest turns get a scratch thread id (`turn:<uuid4>`) that has no
    Conversation row and never should — so they must not trigger a Postgres
    lookup on every miss, and must not be mirrored on the way out.
    """
    try:
        uuid.UUID(thread_id)
    except ValueError:
        return False
    return True


class AsyncValkeySaver(BaseCheckpointSaver[int]):
    """Checkpointer over the shared Valkey client.

    Async-only by design: every call site is a coroutine, and providing sync
    methods that block an event loop thread on a network round trip would be a
    trap rather than a convenience.

    Never raises on a Valkey failure. A read that fails returns None (the graph
    starts the conversation over, which is recoverable) and a write that fails
    is logged and dropped (the turn still completes and still persists to
    Postgres). Losing the ability to resume must not cost the user their build.
    """

    def __init__(self, *, ttl_s: int | None = None) -> None:
        super().__init__()
        self.ttl_s = ttl_s if ttl_s is not None else settings.GRAPH_CHECKPOINT_TTL_S

    # -- serialization ----------------------------------------------------
    # serde.dumps_typed returns (type_tag, bytes). The Valkey client runs with
    # decode_responses=True, so raw bytes cannot survive a round trip — base64
    # and a JSON envelope keep every value a str.

    def _encode(self, value: Any) -> dict[str, str]:
        type_tag, blob = self.serde.dumps_typed(value)
        return {"t": type_tag, "d": base64.b64encode(blob).decode("ascii")}

    def _decode(self, payload: dict[str, str]) -> Any:
        return self.serde.loads_typed((payload["t"], base64.b64decode(payload["d"])))

    # -- writes -----------------------------------------------------------

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")

        # channel_values is stored inline rather than split into per-channel
        # blobs the way InMemorySaver does. That split exists to dedupe
        # unchanged channels across checkpoints; a chat turn writes a handful of
        # small checkpoints, so the dedupe saves nothing and the extra round
        # trips per read cost real latency.
        record = {
            "v": MIRROR_VERSION,
            "cp": self._encode(checkpoint),
            "md": self._encode(get_checkpoint_metadata(config, metadata)),
            "parent": parent_id,
        }

        next_config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint_id,
            }
        }

        client = await get_client()
        if client is None:
            return next_config

        try:
            pipe = client.pipeline(transaction=False)
            pipe.set(
                _checkpoint_key(thread_id, ns, checkpoint_id),
                json.dumps(record),
                ex=self.ttl_s,
            )
            # Score 0 across the board: checkpoint ids are time-ordered UUIDs,
            # so lexicographic order IS chronological order, and that is the
            # ordering the rest of LangGraph assumes (InMemorySaver takes
            # max() of the raw ids). Encoding a timestamp in the score instead
            # would introduce a second, subtly different ordering.
            pipe.zadd(_index_key(thread_id, ns), {checkpoint_id: 0})
            pipe.expire(_index_key(thread_id, ns), self.ttl_s)
            await pipe.execute()
        except (RedisError, OSError):
            logger.warning(
                "checkpoint write failed for thread %s; this turn will not be "
                "resumable but is otherwise unaffected",
                thread_id,
                exc_info=True,
            )

        return next_config

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        client = await get_client()
        if client is None:
            return

        key = _writes_key(thread_id, ns, checkpoint_id)
        try:
            existing = await client.hkeys(key)
            fields: dict[str, str] = {}
            for idx, (channel, value) in enumerate(writes):
                # Negative indices are the special channels in WRITES_IDX_MAP
                # (error, interrupt, resume). Those are upserts — a retry must
                # overwrite the previous attempt's error. Ordinary writes at a
                # non-negative index are append-once, so an already-present
                # field is left alone, which is what makes a redelivered task
                # idempotent.
                write_idx = WRITES_IDX_MAP.get(channel, idx)
                field = f"{task_id}:{write_idx}"
                if write_idx >= 0 and field in existing:
                    continue
                fields[field] = json.dumps(
                    {
                        "task_id": task_id,
                        "task_path": task_path,
                        "channel": channel,
                        "value": self._encode(value),
                    }
                )
            if not fields:
                return
            pipe = client.pipeline(transaction=False)
            pipe.hset(key, mapping=fields)
            pipe.expire(key, self.ttl_s)
            await pipe.execute()
        except (RedisError, OSError):
            logger.warning(
                "pending-write save failed for thread %s", thread_id, exc_info=True
            )

    # -- reads ------------------------------------------------------------

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        client = await get_client()
        if client is not None:
            try:
                if checkpoint_id is None:
                    checkpoint_id = await self._latest_id(client, thread_id, ns)
                if checkpoint_id is not None:
                    tuple_ = await self._load(client, thread_id, ns, checkpoint_id)
                    if tuple_ is not None:
                        return tuple_
            except (RedisError, OSError):
                logger.warning(
                    "checkpoint read failed for thread %s; falling back to " "Postgres",
                    thread_id,
                    exc_info=True,
                )

        # Valkey had nothing (TTL expired, instance flushed, or unreachable).
        # A real conversation may still have its last checkpoint in Postgres.
        # Only for the latest — an explicit checkpoint_id is asking for a
        # specific point in history, which the mirror does not keep.
        if get_checkpoint_id(config) is None and _is_conversation_id(thread_id):
            return await self._hydrate_from_postgres(thread_id, ns)
        return None

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            # Listing across every thread would mean a keyspace scan, and
            # nothing in this application asks for it.
            return
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")

        client = await get_client()
        if client is None:
            return

        try:
            ids = await client.zrevrangebylex(_index_key(thread_id, ns), "+", "-")
        except (RedisError, OSError):
            logger.warning("checkpoint list failed for thread %s", thread_id)
            return

        before_id = get_checkpoint_id(before) if before else None
        yielded = 0
        for checkpoint_id in ids:
            if before_id is not None and checkpoint_id >= before_id:
                continue
            try:
                tuple_ = await self._load(client, thread_id, ns, checkpoint_id)
            except (RedisError, OSError):
                return
            if tuple_ is None:
                continue
            if filter and not all(
                tuple_.metadata.get(k) == v for k, v in filter.items()
            ):
                continue
            yield tuple_
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    async def adelete_thread(self, thread_id: str) -> None:
        client = await get_client()
        if client is None:
            return
        try:
            # One SCAN over this thread's own prefix rather than a full
            # keyspace scan. Bounded by the number of checkpoints in one
            # conversation, which is small.
            pattern = f"{_thread_prefix(thread_id)}:*"
            keys = [key async for key in client.scan_iter(match=pattern, count=100)]
            if keys:
                await client.delete(*keys)
        except (RedisError, OSError):
            logger.warning("checkpoint delete failed for thread %s", thread_id)

    # -- internals --------------------------------------------------------

    async def _latest_id(self, client: Any, thread_id: str, ns: str) -> str | None:
        ids = await client.zrevrangebylex(_index_key(thread_id, ns), "+", "-")
        return ids[0] if ids else None

    async def _load(
        self, client: Any, thread_id: str, ns: str, checkpoint_id: str
    ) -> CheckpointTuple | None:
        raw = await client.get(_checkpoint_key(thread_id, ns, checkpoint_id))
        if raw is None:
            # The index outlives an individual checkpoint whose TTL was not
            # refreshed. Treated as absent rather than as an error.
            return None
        record = json.loads(raw)
        if record.get("v") != MIRROR_VERSION:
            logger.info(
                "discarding checkpoint %s for thread %s: format v%s, expected v%s",
                checkpoint_id,
                thread_id,
                record.get("v"),
                MIRROR_VERSION,
            )
            return None

        writes_raw = await client.hgetall(_writes_key(thread_id, ns, checkpoint_id))
        pending = []
        for field in sorted(writes_raw):
            entry = json.loads(writes_raw[field])
            pending.append(
                (entry["task_id"], entry["channel"], self._decode(entry["value"]))
            )

        parent_id = record.get("parent")
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=self._decode(record["cp"]),
            metadata=self._decode(record["md"]),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending_writes=pending,
        )

    async def _hydrate_from_postgres(
        self, thread_id: str, ns: str
    ) -> CheckpointTuple | None:
        """Rebuild the latest checkpoint from the conversations mirror.

        Pending writes are deliberately not mirrored, so what comes back is a
        completed checkpoint with none attached — enough to resume the
        conversation on a NEW turn, which is the case this tier exists for.
        Resuming mid-node is Valkey's job, and inside its TTL.
        """
        from app.core.db import AsyncSessionLocal
        from app.models.conversation import Conversation

        try:
            async with AsyncSessionLocal() as db:
                conversation = await db.get(Conversation, uuid.UUID(thread_id))
            mirror = conversation.graph_checkpoint if conversation else None
        except Exception:
            logger.warning(
                "checkpoint hydration from Postgres failed for %s",
                thread_id,
                exc_info=True,
            )
            return None

        if not mirror or mirror.get("v") != MIRROR_VERSION or mirror.get("ns") != ns:
            return None

        try:
            checkpoint = self._decode(mirror["cp"])
            metadata = self._decode(mirror["md"])
        except Exception:
            logger.warning(
                "mirrored checkpoint for %s failed to deserialize; ignoring it",
                thread_id,
                exc_info=True,
            )
            return None

        logger.info("hydrated checkpoint for conversation %s from Postgres", thread_id)

        # Warm Valkey so the next read on this conversation is local again.
        # Best-effort: a failure here costs one repeated Postgres read.
        await self.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}},
            checkpoint,
            metadata,
            {},
        )

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": checkpoint["id"],
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=None,
            pending_writes=[],
        )

    # -- the Postgres mirror ----------------------------------------------

    def to_mirror(self, tuple_: CheckpointTuple) -> dict[str, Any]:
        """Render a checkpoint tuple for the conversations.graph_checkpoint column.

        JSONB rather than BYTEA so the column stays readable in psql during a
        post-mortem — the serialized halves are base64 inside it, but the
        version, namespace and id are plain.
        """
        return {
            "v": MIRROR_VERSION,
            "ns": tuple_.config["configurable"].get("checkpoint_ns", ""),
            "cp": self._encode(tuple_.checkpoint),
            "md": self._encode(dict(tuple_.metadata or {})),
        }
