#!/usr/bin/env bash
# 127.0.0.1:8081 → the Cilium Gateway. Replaces the old ingress-nginx forward.
#
# Why not `kubectl port-forward svc/cilium-gateway-palladium`: that Service is
# selector-less — Cilium terminates it in eBPF, there are no backing pods to
# attach to. Instead, relay to the Service's NodePort on the minikube node,
# which also exercises the kube-proxy-replacement path end to end.
#
# 8081 rather than 80: under WSL2 mirrored networking, :80 is held by a
# Windows-side listener that WSL cannot bind. Hostname matching in the Gateway
# ignores the port in the Host header, so routes work unchanged.
set -euo pipefail

LISTEN_PORT="${LISTEN_PORT:-8081}"

until NODEPORT=$(kubectl get svc cilium-gateway-palladium \
    -o jsonpath='{.spec.ports[?(@.port==80)].nodePort}' 2>/dev/null) \
    && [ -n "${NODEPORT}" ]; do
  echo "waiting for cilium-gateway-palladium Service (Gateway not applied yet?)..."
  sleep 2
done

NODE_IP="$(minikube ip)"
echo "forwarding 127.0.0.1:${LISTEN_PORT} -> ${NODE_IP}:${NODEPORT}"

if command -v socat >/dev/null; then
  exec socat "TCP-LISTEN:${LISTEN_PORT},fork,reuseaddr,bind=127.0.0.1" "TCP:${NODE_IP}:${NODEPORT}"
fi

# Dependency-free fallback so a fresh machine works before `apt install socat`.
exec python3 - "${LISTEN_PORT}" "${NODE_IP}" "${NODEPORT}" <<'PY'
import asyncio, sys

listen_port, node_ip, node_port = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])

async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()

async def handle(client_r, client_w):
    try:
        upstream_r, upstream_w = await asyncio.open_connection(node_ip, node_port)
    except OSError:
        client_w.close()
        return
    await asyncio.gather(pipe(client_r, upstream_w), pipe(upstream_r, client_w))

async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", listen_port)
    async with server:
        await server.serve_forever()

asyncio.run(main())
PY
