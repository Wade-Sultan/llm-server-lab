#!/usr/bin/env bash
#
# Watch the worker autoscaler react while a load test runs.
#
# There is no integration between Locust and KEDA, and there should not be:
# Locust drives /chat, builder publishes a turn per request, and the backlog on
# the Pub/Sub subscription is what KEDA reads. The chain is already complete.
# What is missing is a way to see all of it at once — the four things below live
# in four different commands, and the interesting moments are the ones where
# they disagree.
#
#   watch: scripts/watch-scaling.sh
#   drive: the Locust web UI, ChatUser only (see deploy/loadtest/)
#
# BACKLOG is read from the HPA rather than from Cloud Monitoring directly. That
# is deliberate: it is the number KEDA actually acted on, so if it disagrees
# with what the monitoring API reports, the gap between them IS the bug — and
# polling it here costs no API quota and needs no access token.
#
#   NAMESPACE  default palladium
#   INTERVAL   seconds between samples, default 15 (KEDA polls every 30, so
#              anything faster just prints the same row twice)
set -euo pipefail

NAMESPACE="${NAMESPACE:-palladium}"
INTERVAL="${INTERVAL:-15}"
HPA="keda-hpa-worker"

# `kubectl get` writes "not found" to stderr and exits non-zero; under `set -e`
# that would kill the loop the moment KEDA is mid-reconcile. Every read below
# goes through this so a transient gap prints as "-" and the watch continues.
get() {
  kubectl -n "$NAMESPACE" get "$@" 2>/dev/null || true
}

printf '%-8s  %8s  %8s  %9s  %9s  %-13s  %s\n' \
  TIME BACKLOG TARGET REPLICAS DESIRED READY/ACTIVE WORKERS
printf '%s\n' "--------------------------------------------------------------------------------"

while true; do
  backlog="$(get hpa "$HPA" -o jsonpath='{.status.currentMetrics[0].external.current.averageValue}')"
  target="$(get hpa "$HPA" -o jsonpath='{.spec.metrics[0].external.target.averageValue}')"
  current="$(get hpa "$HPA" -o jsonpath='{.status.currentReplicas}')"
  desired="$(get hpa "$HPA" -o jsonpath='{.status.desiredReplicas}')"

  # Two separate conditions on the ScaledObject. Ready says the trigger
  # authenticates and the query resolves; Active says the metric is above
  # activationThreshold. Ready=True/Active=False is a healthy idle scaler, and
  # is easy to mistake for a broken one.
  ready="$(get scaledobject worker -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
  active="$(get scaledobject worker -o jsonpath='{.status.conditions[?(@.type=="Active")].status}')"

  # Pods by phase. Pending is the one to watch: on Autopilot it means a node is
  # being provisioned (60-120s), but a Pending that never clears is the
  # CPUS_ALL_REGIONS quota refusing the scale-out, which otherwise looks
  # identical to KEDA simply not reacting.
  phases="$(get pods -l app=worker -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}')"
  running="$(printf '%s' "$phases" | grep -c Running || true)"
  pending="$(printf '%s' "$phases" | grep -c Pending || true)"

  printf '%-8s  %8s  %8s  %9s  %9s  %-13s  %s\n' \
    "$(date +%H:%M:%S)" \
    "${backlog:--}" "${target:--}" "${current:--}" "${desired:--}" \
    "${ready:--}/${active:--}" \
    "${running}R ${pending}P"

  sleep "$INTERVAL"
done
