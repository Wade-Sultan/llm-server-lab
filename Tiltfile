# Local dev against Minikube. Frontend is NOT here — it keeps running on the
# host via `npm run dev` and talks to the cluster through the gateway hosts.
#
#   ./scripts/minikube-cilium-up.sh   # (re)creates the cluster: Cilium CNI,
#                                     # kube-proxy replacement, Gateway API
#   tilt up
#
# No `minikube tunnel` needed anymore — gateway-forward relays to the
# gateway Service's NodePort directly.

# Refuse to run against anything but the local cluster. Without this a stray
# kubectl context makes `tilt up` deploy to whatever it happens to be pointing
# at — including prod.
allow_k8s_contexts('minikube')

# The ADC secret can't come from secretGenerator: kustomize won't read files
# above the kustomization root without --load-restrictor LoadRestrictionsNone.
# Created here instead so a cold `minikube delete && tilt up` still works.
# commerce fails to *start* without it (firebase.NewApp runs before bind).
local_resource(
    'gcp-adc-secret',
    cmd='kubectl create secret generic gcp-adc ' +
        '--from-file=application_default_credentials.json=.gcloud/application_default_credentials.json ' +
        '--dry-run=client -o yaml | kubectl apply -f -',
    deps=['.gcloud/application_default_credentials.json'],
    labels=['setup'],
)

k8s_yaml(kustomize('deploy/overlays/local'))

docker_build(
    'palladium/builder',
    context='./backend',
    live_update=[
        # Paired with the local overlay's `fastapi dev` command, which reloads
        # on change. Syncing without that patch would copy files in and change
        # nothing.
        sync('./backend/app', '/app/app'),
        # A dependency change needs a real resync, not just a file copy.
        run('uv sync', trigger=['./backend/pyproject.toml', './backend/uv.lock']),
    ],
)
docker_build('palladium/commerce', context='./commerce')
docker_build('palladium/admin', context='./admin')

# Host-based routing entrypoint: 127.0.0.1:8081 → Cilium Gateway. The gateway
# Service is created by Cilium (not this Tiltfile) and is selector-less, so
# neither k8s_resource port_forwards nor `kubectl port-forward` can target it —
# the script relays to its NodePort instead. See the script header for the
# 8081-vs-80 WSL2 story.
local_resource(
    'gateway-forward',
    serve_cmd='./scripts/gateway-forward.sh',
    labels=['setup'],
)

k8s_resource('postgres', port_forwards='5433:5432', labels=['data'])

# Alembic is the only migration authority; everything else waits on it so no
# service ever starts against an un-migrated schema.
k8s_resource('migrate', resource_deps=['postgres', 'gcp-adc-secret'], labels=['data'])

# Restores .local-seed/palladium.dump into the cluster's Postgres, so a cold
# `minikube delete && tilt up` comes back with a real catalog instead of an
# empty schema. Skips itself when pc_parts already has rows, so it's nearly
# free on every run after the first. No dump present = no-op.
local_resource(
    'seed-db',
    cmd='./scripts/seed-local-db.sh',
    resource_deps=['migrate'],
    deps=['scripts/seed-local-db.sh'],
    labels=['data'],
)

# Services depend on seed-db so their pools open against a populated database
# rather than connecting first and seeing rows appear underneath them.
k8s_resource('builder', resource_deps=['seed-db'], port_forwards='8000:8000', labels=['services'])
k8s_resource('commerce', resource_deps=['seed-db'], port_forwards='8080:8080', labels=['services'])
k8s_resource('admin', resource_deps=['seed-db'], port_forwards='3001:3000', labels=['services'])

# CronJobs are deployed so their manifests stay exercised, but must not fire on
# a laptop — the pricing ETL burns SerpAPI quota. Trigger by hand from the Tilt
# UI, or: kubectl create job --from=cronjob/pricing-etl etl-manual-1
k8s_resource('pricing-etl', trigger_mode=TRIGGER_MODE_MANUAL, auto_init=False, labels=['jobs'])
k8s_resource('discovery', trigger_mode=TRIGGER_MODE_MANUAL, auto_init=False, labels=['jobs'])
