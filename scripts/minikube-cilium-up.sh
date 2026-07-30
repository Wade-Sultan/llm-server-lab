#!/usr/bin/env bash
# Cold-boot the local cluster: Minikube with no bundled CNI and no kube-proxy,
# then Cilium as CNI with kube-proxy replacement and Gateway API enabled.
#
# DESTRUCTIVE: deletes the existing minikube profile. The catalog comes back
# via seed-db on the next `tilt up` (scripts/seed-local-db.sh), so the only
# real loss is anything written to local Postgres since the last dump.
#
# Version pairing matters: the Gateway API CRD version must be the one this
# Cilium release passes conformance against — see
# https://docs.cilium.io/en/stable/network/servicemesh/gateway-api/gateway-api/
set -euo pipefail

CILIUM_VERSION="${CILIUM_VERSION:-1.20.0}"
GATEWAY_API_VERSION="${GATEWAY_API_VERSION:-v1.6.1}"

command -v cilium >/dev/null || {
  echo "cilium CLI not found — https://docs.cilium.io/en/stable/gettingstarted/k8s-install-default/#install-the-cilium-cli" >&2
  exit 1
}

# Tilt snapshots the kubeconfig at startup and never re-reads it, so a Tilt
# that outlives this script keeps talking to the deleted cluster's API server
# endpoint. The failure is deeply misleading: stale API discovery surfaces as
# `no matches for kind "HTTPRoute"`, and any local_resource running kubectl
# inherits the dead endpoint. Stop it first, restart it after.
if pgrep -x tilt >/dev/null; then
  echo "tilt is running — stop it first (Ctrl-C), then re-run this script and 'tilt up' after." >&2
  exit 1
fi

minikube delete

# --cni=false / --network-plugin=cni: hand pod networking entirely to Cilium.
# skip-phases=addon/kube-proxy: no kube-proxy at all — Cilium's eBPF
# kube-proxy replacement handles Services, same as GKE Dataplane V2.
#
# --docker-opt dns: image builds run in bridge containers on the node's Docker
# daemon, which inherit the node's nameserver. Under WSL2 that is the host's
# mirrored-networking resolver, reachable from the node itself but NOT from a
# nested bridge container — so `uv sync` dies with "dns error: failed to lookup
# address information: Try again" on a different package every run. Public
# resolvers are reachable from the build netns; stored in the profile, so this
# survives `minikube stop && minikube start`.
minikube start --driver=docker --cpus=6 --memory=16g \
  --kubernetes-version=v1.33.0 \
  --addons=metrics-server \
  --network-plugin=cni --cni=false \
  --extra-config=kubeadm.skip-phases=addon/kube-proxy \
  --docker-opt dns=8.8.8.8 --docker-opt dns=1.1.1.1

# Verify the above actually took, rather than trusting the flag: a silent
# regression here surfaces much later as a confusing mid-build failure.
if ! (eval "$(minikube docker-env)" && \
      docker run --rm busybox timeout 8 nslookup files.pythonhosted.org >/dev/null 2>&1); then
  echo "build-container DNS still broken — falling back to node resolv.conf" >&2
  docker exec minikube sh -c \
    'printf "nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions ndots:0\n" > /etc/resolv.conf'
fi

# Gateway API CRDs must exist BEFORE the operator starts, or Cilium disables
# its Gateway controller and the GatewayClass sits at "Waiting for controller".
for crd in gatewayclasses gateways httproutes referencegrants grpcroutes backendtlspolicies; do
  kubectl apply -f "https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/${GATEWAY_API_VERSION}/config/crd/standard/gateway.networking.k8s.io_${crd}.yaml"
done
# TLSRoute ships in the experimental channel but is on Cilium's *required*
# list — without it the operator logs "Required GatewayAPI resources are not
# found" and the whole controller stays off.
kubectl apply -f "https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/${GATEWAY_API_VERSION}/config/crd/experimental/gateway.networking.k8s.io_tlsroutes.yaml"

# With kube-proxy skipped there is no ClusterIP path to the API server yet, so
# Cilium is pointed at it directly. On the docker driver the API server
# listens on the node IP at 8443.
cilium install --version "${CILIUM_VERSION}" \
  --set kubeProxyReplacement=true \
  --set k8sServiceHost="$(minikube ip)" \
  --set k8sServicePort=8443 \
  --set gatewayAPI.enabled=true \
  --set hubble.relay.enabled=true \
  --set hubble.ui.enabled=true

cilium status --wait

# The GatewayClass existing is not enough — it is created regardless, and sits
# at Accepted=Unknown "Waiting for controller" whenever a required CRD is
# missing. Assert the condition that actually matters, so a broken controller
# fails here instead of as a mystery 404 later.
if ! kubectl wait --for=condition=Accepted=True gatewayclass/cilium --timeout=120s; then
  echo "GatewayClass not accepted — check: kubectl -n kube-system logs deploy/cilium-operator | grep -i gateway" >&2
  exit 1
fi

echo "Cluster ready. Next: tilt up"
