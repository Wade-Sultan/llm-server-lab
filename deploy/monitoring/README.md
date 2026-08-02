# Cloud Monitoring alert policies

Declarative alert policies for the chat turn pipeline, applied with
`gcloud monitoring policies`. Tracked in git — unlike `deploy/messaging.md` and
`deploy/observability.md`, these are resources rather than instructions for a
human, and they contain no project-specific identifiers.

| File | Fires when | Severity |
|---|---|---|
| [`alert-dead-lettered-turns.yaml`](alert-dead-lettered-turns.yaml) | A turn failed 5 delivery attempts | **Page.** The turn is never running. |
| [`alert-turns-not-persisted.yaml`](alert-turns-not-persisted.yaml) | Commits failing, or buffers accumulating | **Page.** Paid-for work is not in Postgres, and it expires in 24h. |
| [`alert-worker-backlog.yaml`](alert-worker-backlog.yaml) | Turns queue longer than 120s | Ticket. Users feel it as slowness. |
| [`alert-worker-metrics-absent.yaml`](alert-worker-metrics-absent.yaml) | No worker reported for 10 min | **Page.** Also means the two above are unreliable. |

The last one is the reason the set works. Threshold conditions do not fire on a
metric that stopped arriving — they go quiet, which is indistinguishable from
healthy. Without an absence check, "every worker is down" and "Valkey is
unreachable" would both silence their own alarms.

## Applying

Policies ship with `notificationChannels: []`, so applying them as-is creates
policies that fire into nothing. Create a channel first and patch it in.

```bash
export PROJECT_ID=<your-project-id>

# One-time: a channel to notify. Email shown; Slack/PagerDuty work the same way.
gcloud beta monitoring channels create \
  --project="$PROJECT_ID" \
  --display-name="Palladium oncall" \
  --type=email \
  --channel-labels=email_address=you@example.com

CHANNEL=$(gcloud beta monitoring channels list \
  --project="$PROJECT_ID" \
  --filter='displayName="Palladium oncall"' \
  --format='value(name)')

for f in deploy/monitoring/alert-*.yaml; do
  # yq is not assumed — sed the empty array in place into a temp copy, so the
  # committed file stays free of an account-specific channel id.
  tmp=$(mktemp)
  sed "s|^notificationChannels: \[\]|notificationChannels:\n  - ${CHANNEL}|" "$f" > "$tmp"
  gcloud monitoring policies create --project="$PROJECT_ID" --policy-from-file="$tmp"
  rm -f "$tmp"
done
```

> If `gcloud monitoring policies` is not recognised, the command is under
> `gcloud alpha monitoring policies` on older SDK versions. Both accept the same
> `--policy-from-file` payload.

**Updating an existing policy** — `create` makes a duplicate rather than
replacing, which is easy to do by accident and produces two of every alert:

```bash
NAME=$(gcloud monitoring policies list --project="$PROJECT_ID" \
  --filter='displayName="Palladium — chat turns dead-lettered"' \
  --format='value(name)')
gcloud monitoring policies update "$NAME" --project="$PROJECT_ID" \
  --policy-from-file=deploy/monitoring/alert-dead-lettered-turns.yaml
```

## Verifying they work

An alert that has never fired is an alert you do not know is wired up. Both of
these are safe to run against production.

**Dead-letter policy** — publish a message the worker cannot decode. `_decode`
in `app/worker.py` acks undecodable messages away deliberately, so use a
well-formed envelope with a `messages` array that fails validation instead:

```bash
gcloud pubsub topics publish chat-turns \
  --project="$PROJECT_ID" \
  --ordering-key=alert-test \
  --message='{"turn_id":"alert-test","messages":[{"role":"nonsense"}]}'
```

It should retry 5 times and land in `chat-turns-dead` within a few minutes.
Drain it afterwards:

```bash
gcloud pubsub subscriptions pull chat-turns-dead-sub \
  --project="$PROJECT_ID" --limit=10 --auto-ack
```

**Absence policy** — scale the workers to zero for 15 minutes:

```bash
kubectl -n palladium scale deploy/worker --replicas=0
# ... wait for the alert, then ...
kubectl -n palladium scale deploy/worker --replicas=2
```

Note that `/chat` keeps serving throughout, on the inline fallback. That is the
point of the alert: nothing user-visible breaks, so nothing else would tell you.

## Metrics these depend on

Defined in [`backend/app/core/turn_metrics.py`](../../backend/app/core/turn_metrics.py),
scraped by the `worker` PodMonitoring in
[`../overlays/prod/podmonitoring.yaml`](../overlays/prod/podmonitoring.yaml).

| Prometheus name | Cloud Monitoring metric type |
|---|---|
| `palladium_turn_commits_total{result}` | `prometheus.googleapis.com/palladium_turn_commits_total/counter` |
| `palladium_chat_buffers_retained` | `prometheus.googleapis.com/palladium_chat_buffers_retained/gauge` |
| `palladium_turn_duration_seconds` | `prometheus.googleapis.com/palladium_turn_duration_seconds/histogram` |
| `palladium_turns_inflight` | `prometheus.googleapis.com/palladium_turns_inflight/gauge` |

`turn_duration_seconds` and `turns_inflight` have no policy of their own — they
are dashboard metrics. Duration is the input to any future latency SLO, and
in-flight is the signal a backlog autoscaler (KEDA) would eventually read
instead of `num_undelivered_messages`.

## Not covered

- **Memorystore instance health.** `memorystore.googleapis.com/instance/*`
  exposes memory and connection metrics worth alerting on, but at 1 shard with a
  TTL'd working set, capacity is not currently a plausible failure. Worth adding
  when a replica or a second shard is.
- **Cost.** `total_cost_usd` accumulates per conversation on the `conversations`
  table, not as a metric, so a spend spike is a query rather than an alert
  today.
