#!/usr/bin/env bash
# Logs for builder / commerce / admin on the prod GKE cluster.
#
# TWO SOURCES, because they answer different questions:
#
#   live (default)  kubectl, straight off the running pods. Instant, streams,
#                   but only sees pods that exist *right now*. A pod that
#                   crashed or was replaced takes its logs with it — and with
#                   maxUnavailable: 0 every rollout replaces every pod, so
#                   "the error I saw ten minutes ago" is routinely already gone.
#
#   -H / --history  Cloud Logging, which GKE feeds automatically. Survives pod
#                   death, restarts and rollouts, searchable server-side. This
#                   is the one to reach for when debugging something that
#                   already happened.
#
# Cloud Logging needs no setup: GKE ships container stdout/stderr there by
# default. It is deliberately NOT wired into Grafana Cloud — that datasource is
# an Enterprise plugin and authenticates with a service account JSON key, which
# this topology does not have anywhere (see patches/workload-identity.yaml).
# Metrics go to Grafana; logs stay here. See deploy/observability.md.
set -euo pipefail

NAMESPACE="${NAMESPACE:-palladium}"
CLUSTER="${CLUSTER:-palladium}"
SERVICES=(builder commerce admin)

FOLLOW=0
HISTORY=0
SINCE=1h
LINES=200
PATTERN=""

usage() {
  cat >&2 <<EOF
usage: scripts/logs.sh [options] <builder|commerce|admin|all>

  -f, --follow        stream new lines as they arrive (live mode only)
  -H, --history       read from Cloud Logging instead of the live pods
  -s, --since DUR     how far back to look: 30m, 1h, 2d  (default: ${SINCE})
  -n, --lines N       max lines per source                (default: ${LINES})
  -g, --grep TEXT     substring match, applied server-side (history only)
  -h, --help

examples:
  scripts/logs.sh -f builder              # tail the live builder pods
  scripts/logs.sh all                     # recent lines from all three
  scripts/logs.sh -H -s 6h -g Traceback builder
                                          # find a crash from this morning
EOF
  exit "${1:-2}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    -f|--follow)  FOLLOW=1; shift ;;
    -H|--history) HISTORY=1; shift ;;
    -s|--since)   SINCE="${2:?--since needs a duration}"; shift 2 ;;
    -n|--lines)   LINES="${2:?--lines needs a number}"; shift 2 ;;
    -g|--grep)    PATTERN="${2:?--grep needs a pattern}"; shift 2 ;;
    -h|--help)    usage 0 ;;
    -*)           echo "unknown option: $1" >&2; usage ;;
    *)            break ;;
  esac
done

[ $# -eq 1 ] || usage
TARGET="$1"

if [ "$TARGET" != all ] && ! printf '%s\n' "${SERVICES[@]}" | grep -qx "$TARGET"; then
  echo "unknown service: $TARGET (expected one of: ${SERVICES[*]}, all)" >&2
  exit 2
fi

# ------------------------------------------------------------------ history --
if [ "$HISTORY" -eq 1 ]; then
  [ "$FOLLOW" -eq 0 ] || echo "note: --follow does not apply to --history; ignoring" >&2

  if [ "$TARGET" = all ]; then
    CONTAINER_FILTER=$(printf '"%s" OR ' "${SERVICES[@]}")
    CONTAINER_FILTER="resource.labels.container_name=(${CONTAINER_FILTER% OR })"
  else
    CONTAINER_FILTER="resource.labels.container_name=\"${TARGET}\""
  fi

  FILTER="resource.type=\"k8s_container\"
    resource.labels.cluster_name=\"${CLUSTER}\"
    resource.labels.namespace_name=\"${NAMESPACE}\"
    ${CONTAINER_FILTER}"

  # Substring match runs in Cloud Logging, not locally, so --lines still bounds
  # the *matching* lines rather than being spent on lines that then get grepped
  # away.
  #
  # Emitted as a quoted bare term, which Logging treats as a full-text search
  # across the whole entry. That is deliberate over a field-scoped match like
  # textPayload:"x": the three services do not agree on payload shape (Go writes
  # jsonPayload, FastAPI and Next.js mostly textPayload), so any single field
  # would silently miss two of them. Quoting also keeps patterns containing
  # spaces from being parsed as separate filter terms.
  if [ -n "$PATTERN" ]; then
    FILTER="${FILTER}
    \"${PATTERN//\"/\\\"}\""
  fi

  # --order=desc, then reverse locally. Ordering ascending server-side would
  # combine with --limit to return the OLDEST N entries in the window, which is
  # the opposite of what anyone debugging wants; descending takes the newest N,
  # and the reverse below puts them back in reading order.
  gcloud logging read "$FILTER" \
    --freshness="$SINCE" \
    --limit="$LINES" \
    --order=desc \
    --format=json \
  | python3 -c '
import json, sys

entries = json.load(sys.stdin)
if not entries:
    sys.exit("no matching log entries in the window")

for e in reversed(entries):  # newest-first from the API -> chronological
    ts = e.get("timestamp", "")[:19].replace("T", " ")
    labels = e.get("resource", {}).get("labels", {})
    pod = labels.get("pod_name", "?")
    sev = e.get("severity", "DEFAULT")

    # Payload shape varies by service: FastAPI and Next.js mostly write plain
    # text, Go structured JSON. Fall through the possibilities rather than
    # printing blanks for whichever one this entry is not.
    if "textPayload" in e:
        msg = e["textPayload"]
    elif "jsonPayload" in e:
        p = e["jsonPayload"]
        msg = p.get("message") or p.get("msg") or json.dumps(p, separators=(",", ":"))
    else:
        msg = json.dumps(e.get("protoPayload", ""), separators=(",", ":"))

    print(f"{ts}  {sev:<7} {pod}  {msg.rstrip()}")
'
  exit 0
fi

# --------------------------------------------------------------------- live --
if [ "$TARGET" = all ]; then
  SELECTOR="app in ($(IFS=,; echo "${SERVICES[*]}"))"
else
  SELECTOR="app=${TARGET}"
fi

ARGS=(--namespace "$NAMESPACE" --selector "$SELECTOR"
      --prefix --timestamps --since "$SINCE" --tail "$LINES")

# --max-log-requests only bites with --follow, and its default of 5 is below
# what this cluster can produce: builder's HPA reaches 4 replicas, so `all` is
# 6 pods and kubectl aborts rather than truncating. Raised well past the
# ceiling so a scale-out never turns into a confusing tailing failure.
if [ "$FOLLOW" -eq 1 ]; then
  ARGS+=(--follow --max-log-requests 20)
fi

# No pods matched is silent success in kubectl, which reads as "the service is
# quiet" when it actually means the selector found nothing.
if [ -z "$(kubectl --namespace "$NAMESPACE" get pods --selector "$SELECTOR" \
             --output name 2>/dev/null)" ]; then
  echo "no running pods match '${SELECTOR}' in namespace ${NAMESPACE}." >&2
  echo "if the pods have crashed or rolled, their logs are only in Cloud Logging:" >&2
  echo "  scripts/logs.sh --history ${TARGET}" >&2
  exit 1
fi

exec kubectl logs "${ARGS[@]}"
