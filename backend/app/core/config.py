from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    HttpUrl,
    PostgresDsn,
    computed_field,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENROUTER_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class LLMEndpoint:
    """One OpenAI-compatible endpoint, and what may be sent to it.

    Exists so that "am I talking to OpenRouter?" is asked one way rather than
    once per client-construction site, and so that the answer carries the right
    key with it. Several things hang off that question beyond the URL: the
    credential, litellm's model prefix, whether OpenRouter's `usage`/`session_id`
    extensions are safe to put in the request body, and whether a completion id
    means anything to the /generation cost endpoint.
    """

    # Empty means "unset", which resolves to OpenRouter — see `url`.
    base_url: str
    local_key: str
    openrouter_key: str

    @property
    def is_openrouter(self) -> bool:
        # An explicitly configured openrouter.ai URL counts, not just the empty
        # default: DISCOVERY_LLM_BASE_URL names it outright to hold discovery
        # there while chat moves to a local server, and that must not read as
        # "somewhere else" or the call goes out with the wrong key and loses its
        # cost accounting.
        return not self.base_url or "openrouter.ai" in self.base_url

    @property
    def url(self) -> str:
        """The base URL to hand an OpenAI-compatible client."""
        return self.base_url or OPENROUTER_URL

    @property
    def api_key(self) -> str:
        return self.openrouter_key if self.is_openrouter else self.local_key


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None

    # Shared secret enabling the Locust load-test path (app/core/loadtest.py):
    # requests presenting it in X-Palladium-Load-Test are served by stub LMs and
    # never reach OpenRouter. Empty — the default — disables the header
    # entirely, so a normal deployment cannot be put into stub mode at all.
    LOAD_TEST_SECRET: str = ""
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    CLOUD_SQL_INSTANCE: str | None = None

    POSTGRES_DB_URL: PostgresDsn | None = None

    # Which of the two above to use, when both are set. Normally unset: every
    # environment configures exactly one (GKE and docker-compose.dev pass
    # POSTGRES_DB_URL, a bare-metal checkout has CLOUD_SQL_INSTANCE), and
    # app.core.db refuses to guess when both are present.
    #
    # This exists because env_ignore_empty=True above means a blank override
    # (CLOUD_SQL_INSTANCE=) is discarded rather than applied, so there is
    # otherwise no way to aim one command at a scratch database without
    # editing .env. Set DB_TARGET=url to do that:
    #   DB_TARGET=url POSTGRES_DB_URL="postgresql://..." alembic upgrade head
    DB_TARGET: Literal["cloud_sql", "url"] | None = None

    @computed_field
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        if not self.POSTGRES_DB_URL:
            # Production uses CLOUD_SQL_INSTANCE via the connector library,
            # so SQLALCHEMY_DATABASE_URI is only needed for local dev
            return ""
        return str(self.POSTGRES_DB_URL).replace(
            "postgresql://", "postgresql+psycopg://"
        )

    OPENROUTER_API_KEY: str

    # --- Chat-completion endpoint ---------------------------------------------
    # Empty (the default, and what production runs) means every chat completion
    # goes to OpenRouter. Setting it points them at any other OpenAI-compatible
    # server instead — the local overlay uses it to reach LM Studio on the host
    # GPU. Include the `/v1` suffix: the OpenAI SDK appends paths to this, it
    # does not append a version.
    #
    # This switches the ENDPOINT, not the model. LM Studio serves whatever is
    # loaded under its own ids, so an environment that sets this must also set
    # the CHAT_*_MODEL / RECOMMEND_MODEL / DISCOVERY_EXTRACT_MODEL slugs to
    # match — the OpenRouter defaults (`google/gemma-4-31b-it` and friends) mean
    # nothing to it and come back as 404 model_not_found.
    #
    # Cost accounting goes quiet when this is set, by design: dollar figures
    # come from OpenRouter's own `cost` field and its /generation endpoint,
    # neither of which a local server has. Token counts still arrive, and a turn
    # against local hardware costs nothing to under-report.
    LLM_BASE_URL: str = ""

    # Credential for LLM_BASE_URL. LM Studio accepts anything but the OpenAI
    # client refuses to construct without a key, hence the placeholder. Ignored
    # entirely when LLM_BASE_URL is empty — OpenRouter uses OPENROUTER_API_KEY.
    LLM_API_KEY: str = "not-needed"

    # Lets parts discovery go somewhere other than LLM_BASE_URL. Empty (prod)
    # means it follows LLM_BASE_URL like everything else; setting it to
    # OpenRouter's URL keeps discovery there while chat runs locally.
    #
    # This exists because discovery is not substitutable the way chat is. It
    # sends rasterized PDF pages and a per-category JSON schema, so it needs a
    # multimodal model that honours `response_format` — and unlike chat it is a
    # CronJob, so when it cannot do that the failure is a recurring background
    # 404 nobody is watching rather than a broken reply somebody reports.
    DISCOVERY_LLM_BASE_URL: str = ""

    @property
    def chat_endpoint(self) -> LLMEndpoint:
        """Where chat completions and the DSPy Decide* steps go."""
        return LLMEndpoint(self.LLM_BASE_URL, self.LLM_API_KEY, self.OPENROUTER_API_KEY)

    @property
    def discovery_endpoint(self) -> LLMEndpoint:
        """Where parts discovery goes — its own override, else the chat endpoint."""
        return LLMEndpoint(
            self.DISCOVERY_LLM_BASE_URL or self.LLM_BASE_URL,
            self.LLM_API_KEY,
            self.OPENROUTER_API_KEY,
        )

    # --- Embeddings (pgvector) -----------------------------------------------
    # Separate from OPENROUTER_API_KEY because OpenRouter has no embeddings
    # endpoint — it proxies chat completions only. Optional at boot: without it
    # the embedding service refuses to run and catalog matching degrades to
    # "no matches", which the recommender already handles as its normal
    # cold-start state. Nothing else in the app is affected.
    OPENAI_API_KEY: str | None = None
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    # Must match app.models.embeddings.EMBEDDING_DIMS and the vector(N) column
    # width in migration c1d2e3f4a5b6. Overriding this alone will not resize the
    # column — changing model width is a migration plus a full re-embed.
    EMBEDDING_DIMS: int = 1536

    # Hugging Face Hub token for the AI-model discovery job. Public model
    # listing works without it, but a token raises rate limits and returns
    # metadata for gated models. Create at https://huggingface.co/settings/tokens
    HF_TOKEN: str | None = None

    # Tavily search API key for parts-discovery enrichment (finding spec pages
    # for novel SKUs). Optional at boot; the discovery job fails fast with a
    # clear error if it runs without one. https://app.tavily.com
    TAVILY_API_KEY: str | None = None

    # Shared secret for the admin-triggered discovery endpoints (X-Admin-Key
    # header). The admin panel's server actions hold this key server-side.
    # Unset = discovery endpoints return 503.
    DISCOVERY_API_KEY: str | None = None

    SERPAPI_KEY: str

    # --- Price alerts ---------------------------------------------------------
    # The master switch on whether a price drop actually mails anyone. Off means
    # the pricing ETL still evaluates every subscription and logs what it would
    # have sent, but nothing leaves the cluster and no subscription is retired —
    # so turning it on later alerts exactly the people it would have alerted
    # while it was off. Subscribing is unaffected either way.
    PRICE_ALERTS_ENABLED: bool = False

    # Where the commerce (Go) service lives from inside the cluster, and the
    # shared secret its /internal/* routes require. Commerce holds
    # RESEND_API_KEY and the email templates, so it is what actually sends;
    # see app/services/commerce_client.py. Either being empty makes dispatch
    # fail as "not configured" rather than silently succeeding.
    COMMERCE_INTERNAL_URL: str = ""
    COMMERCE_INTERNAL_KEY: str = ""

    # --- Valkey (Memorystore) -------------------------------------------------
    # Empty host disables both the turn stream and the chat buffer, and the API
    # falls back to running the pipeline inline in the request (see
    # app/api/routes/chat.py). That fallback is what keeps local development and
    # the test suite working without a Valkey to talk to; it is NOT a production
    # mode, because an inline turn dies with its client connection.
    VALKEY_HOST: str = ""
    VALKEY_PORT: int = 6379
    # Which redis-py client to build: RedisCluster when True, Redis when False.
    # It must match how the Memorystore instance was created, and the two fail
    # in opposite, equally confusing ways — a plain Redis client against a
    # clustered instance dies on the first MOVED redirect mid-turn, and a
    # RedisCluster client against a Cluster Mode Disabled instance fails at
    # connect because there is no CLUSTER SLOTS to read.
    #
    # False is the default because it matches both real deployments: prod runs a
    # `custom-pico` node, which Google only offers on Cluster Mode Disabled
    # instances, and local dev runs a standalone valkey container. Set True only
    # if the instance is recreated as Cluster Mode Enabled — the key layout is
    # already cluster-safe (see app/services/turn_stream.py), so that is a
    # config change rather than a code change.
    VALKEY_CLUSTER: bool = False
    # AUTH is off by default on Memorystore. When enabled, the string lands here
    # from Secret Manager. TLS ("in-transit encryption") is a per-instance
    # setting chosen at creation and cannot be toggled later.
    VALKEY_PASSWORD: str = ""
    VALKEY_TLS: bool = False

    # How long a turn's event stream outlives its last write. This is the window
    # in which a browser that lost its connection can still resume mid-build and
    # replay what it missed; past it, XREAD finds nothing and the client starts a
    # fresh turn. 30 minutes covers a phone whose screen locked partway through.
    TURN_STREAM_TTL_S: int = 1800
    # Hard ceiling on entries per turn stream, enforced by XADD MAXLEN. A long
    # recommendation is a few thousand token events; 20k leaves headroom without
    # letting a pathological turn grow unbounded in memory.
    TURN_STREAM_MAXLEN: int = 20000

    # Fallback expiry on the chat buffer. The buffer is normally deleted the
    # moment its turn is committed to Postgres, so this only fires for turns that
    # never commit — a worker OOM-killed mid-run, or a poison message that
    # exhausted its retries. Deliberately longer than TURN_STREAM_TTL_S so the
    # buffer outlives the stream it describes and a post-mortem can still read it.
    CHAT_BUFFER_TTL_S: int = 86400

    # How long a conversation's LangGraph checkpoints live in Valkey. Matched to
    # CHAT_BUFFER_TTL_S because they answer the same question for the same
    # window — what was this conversation in the middle of. Past it, the latest
    # checkpoint is still in Postgres (conversations.graph_checkpoint), so
    # expiry costs resumability mid-turn, not the accumulated build profile.
    GRAPH_CHECKPOINT_TTL_S: int = 86400

    # --- Tracing --------------------------------------------------------------
    # Empty key disables the LangSmith span exporter entirely; the Google
    # Cloud Observability side is driven separately by the standard
    # OTEL_EXPORTER_OTLP_ENDPOINT, which Managed OpenTelemetry for GKE injects
    # into the pod. Both are independently optional — see app/core/tracing.py.
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "palladium"
    LANGSMITH_OTEL_ENDPOINT: str = "https://api.smith.langchain.com/otel"

    # --- Pub/Sub --------------------------------------------------------------
    # Unset topic disables publishing, which is what selects the inline fallback
    # described above. GOOGLE_CLOUD_PROJECT is set automatically on GKE via the
    # metadata server; it is only needed explicitly for local emulator runs.
    PUBSUB_TOPIC: str = ""
    PUBSUB_SUBSCRIPTION: str = ""
    GOOGLE_CLOUD_PROJECT: str = ""
    # Ceiling on turns one worker pod runs at once. Each turn is mostly blocked
    # on OpenRouter rather than on CPU, so this is about memory and OpenRouter
    # rate limits, not cores — the same reason builder's CPU-based HPA
    # understates its load (deploy/overlays/prod/hpa.yaml).
    PUBSUB_MAX_CONCURRENCY: int = 8
    # Must exceed the longest possible turn. The subscriber extends the ack
    # deadline while a turn runs, but Pub/Sub caps total extension at 60 minutes,
    # after which the message is redelivered even though the first attempt is
    # still going.
    PUBSUB_ACK_EXTENSION_S: int = 600


settings = Settings()  # type: ignore
