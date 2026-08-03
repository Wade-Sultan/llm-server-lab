# Deployment

Production runs on **GKE Autopilot** in `us-central1`, deployed by a single
Cloud Build pipeline: [`deploy/cloudbuild.yaml`](deploy/cloudbuild.yaml).

There is one pipeline and one trigger. The three per-service pipelines that
predated it — two deploying to Cloud Run, one to a GCE VM — are gone, along with
the services themselves. If you find a reference to `backend/cloudbuild.yaml`,
`commerce/cloudbuild.yaml` or `admin/cloudbuild.yaml` anywhere, it is stale.

## What runs where

| Component | Where | Reached by |
|---|---|---|
| `builder` (FastAPI) | GKE Deployment, 2–4 replicas (HPA) | `https://api.palladiumtech.ai` |
| `worker` (Pub/Sub subscriber) | GKE Deployment, 2 replicas | nothing — it pulls its own work |
| `commerce` (Go) | GKE Deployment, 1 replica | `https://commerce.palladiumtech.ai` |
| `admin` (Next.js) | GKE Deployment, 1 replica | `kubectl port-forward svc/admin 3000:3000` only |
| `frontend` (Next.js) | **Vercel**, not GKE | `https://palladiumtech.ai` |

Both public hostnames resolve to one reserved static IP fronting a single GKE
Gateway. The frontend is deployed by Vercel on its own push, and is **not** part
of this pipeline — which is why the trigger below filters it out.

Postgres is Cloud SQL, reached over the VPC by private IP. Valkey is Memorystore,
also private IP. Neither is in the cluster.

## The trigger

One trigger, on pushes to `main`:

```bash
export PROJECT_ID=project-b8abf13d-d1ce-43f1-837

gcloud builds triggers create github \
  --project="$PROJECT_ID" \
  --region=us-central1 \
  --name=palladium-gke-deploy \
  --repo-owner=Wade-Sultan \
  --repo-name=palladium-pc \
  --branch-pattern='^main$' \
  --build-config=deploy/cloudbuild.yaml \
  --included-files='backend/**,commerce/**,admin/**,deploy/**' \
  --service-account="projects/${PROJECT_ID}/serviceAccounts/<BUILD_SA>@${PROJECT_ID}.iam.gserviceaccount.com"
```

Three details that are easy to get wrong:

- **No `SHORT_SHA` substitution.** Cloud Build populates `$SHORT_SHA` for
  trigger-driven builds automatically. Only manual runs need it passed (see
  below) — and the `render` step fails loudly rather than shipping `:latest` if
  it is ever missing.
- **`--included-files` is not an optimisation, it is correctness.** The frontend
  deploys on Vercel. Without this filter, a frontend-only push would rebuild
  three images, run a migration Job and roll production for a change that cannot
  affect any of them.
- **The build service account needs three roles**, and the first is the one
  people miss — `deploy/cloudbuild.yaml` sets `options.logging:
  CLOUD_LOGGING_ONLY` precisely because a user-specified SA requires it, and
  without the role the build fails before its first step:

  ```
  roles/logging.logWriter        mandatory with CLOUD_LOGGING_ONLY
  roles/artifactregistry.writer  push images
  roles/container.developer      get-credentials + kubectl apply
  ```

### Manual run

Same pipeline, but `SHORT_SHA` must be supplied:

```bash
gcloud builds submit --config=deploy/cloudbuild.yaml \
  --substitutions=SHORT_SHA=$(git rev-parse --short HEAD)
```

## Pipeline stages

```
build-builder ─┐
build-commerce ─┼─► push-* ─┐
build-admin    ─┘           │
                            ├─► migrate ──► deploy ──► rollout
render ──► credentials ─────┘
```

| Stage | What it does | Fails the build when |
|---|---|---|
| `build-*` | Three images in parallel, `--cache-from :latest` | a Dockerfile breaks |
| `push-*` | Pushes `:$SHORT_SHA` and `:latest` | registry auth |
| `render` | `kustomize build` the prod overlay, substitutes `PROJECT_ID` and the SHA, splits the migration Job out | substitution incomplete, or `POSTGRES_SERVER` leaked into the ConfigMap |
| `credentials` | Cluster auth, then preflights the Secrets | a `palladium-secrets-*` Secret is missing, or `POSTGRES_SERVER` is not an RFC1918 address |
| `migrate` | `alembic upgrade head` as Job `migrate-$SHORT_SHA` | the migration raises |
| `deploy` | `kubectl apply` everything else | invalid manifests |
| `rollout` | waits on all four Deployments | a Deployment does not become ready in 600s |

Two design points worth knowing:

**The migration is a gate, not a step.** It runs as its own Job and must complete
before any Deployment rolls. Re-running the same commit is safe: a Job that
already succeeded is skipped, one that failed *before alembic started*
(`ImagePullBackOff`, eviction) is recreated, and one where alembic actually ran
and raised blocks the build with its logs. That last case does not auto-retry on
purpose — the failure is deterministic, and re-running would bury the real error
under a second identical one.

**`rollout` includes `worker` even though nothing routes to it.** A worker that
cannot start is invisible from outside — `/chat` keeps answering by running turns
inline — so without that check the build would go green while turns silently
stopped surviving client disconnects.

### Warnings that do not fail the build

`credentials` warns rather than fails when `VALKEY_HOST` is missing from
`palladium-secrets-builder`. That is a valid, degraded configuration: `/chat`
falls back to inline streaming and the site works. But the `worker` Deployment
can do nothing useful in that state, so **read the build log** — a green build
with that warning means durability is off.

## Rollback

**Check whether the bad deploy included a migration first.** This is the only
question that changes the answer:

```bash
kubectl -n palladium get jobs -l app.kubernetes.io/part-of=palladium | grep migrate
```

### No schema change — roll the pods back

Fastest path, no rebuild. `maxUnavailable: 0` means the old pods keep serving
throughout:

```bash
kubectl -n palladium rollout undo deployment/builder
kubectl -n palladium rollout undo deployment/worker
kubectl -n palladium rollout status deployment/builder --timeout=300s
```

Or pin an explicit known-good SHA — legible in `kubectl describe`, unlike
`undo`, which silently means "whatever was there before":

```bash
SHA=<known-good-short-sha>
REG=us-central1-docker.pkg.dev/project-b8abf13d-d1ce-43f1-837/palladium
kubectl -n palladium set image deployment/builder builder=$REG/backend:$SHA
kubectl -n palladium set image deployment/worker  worker=$REG/backend:$SHA
```

This works because every build pushes an immutable `:$SHORT_SHA` tag alongside
`:latest`. Never roll back to `:latest` — it moves.

### The deploy included a migration

**Do not just roll the pods back.** Old code against a migrated schema fails in
whatever way the migration happened to change, which is usually worse and
always less obvious than the bug you are rolling back from.

Write and apply a down-migration, or roll forward with a fix. Alembic's
`downgrade` exists but is only as good as the specific revision's `downgrade()`
— check that it is actually implemented before relying on it.

### The Gateway is not serving

Deployments are healthy but requests fail:

```bash
kubectl -n palladium get gateway palladium \
  -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}{"\n"}'
```

`False` means a route or listener is invalid — and **one bad route invalidates
the entire Gateway**, taking every hostname down, not just the broken one. The
usual cause is an HTTPRoute referencing a listener `sectionName` that does not
exist, or an `httpRoute.timeouts` field, which the GKE controller rejects
outright (use `GCPBackendPolicy` instead — see
[`deploy/overlays/prod/backend-policies.yaml`](deploy/overlays/prod/backend-policies.yaml)).

## Prerequisites the pipeline cannot create

These exist outside the repo and are asserted, not provisioned, by `credentials`:

- **`palladium-secrets-builder` / `-commerce` / `-admin`** — created out of band
  from Secret Manager so no production credential is ever on a developer's disk
  or in a `kustomize build` output.
- **Workload Identity bindings** — the KSA annotation is in the overlay; the
  matching `roles/iam.workloadIdentityUser` binding is not.
- **The reserved IP `palladium-ingress-ip`** and the `palladium-certs`
  certificate map. Both are referenced by the Gateway and must pre-exist.
- **Pub/Sub topic, subscription and Memorystore instance** — see
  `deploy/messaging.md`, which is deliberately untracked (operator runbook, not
  a build dependency).

## Local and non-production

`deploy/overlays/local/` targets minikube + Cilium and is not deployed by this
pipeline. The `worker` is scaled to 0 there — it needs Pub/Sub, which local does
not have — so `/chat` takes the inline path. That fallback therefore has to keep
working: it is the only chat path exercised in development.

See [`development.md`](development.md) for the local loop.
