# Local Docker Development Setup

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Your Machine                            │
│                                                             │
│  ┌──────────┐   ┌──────────┐    ┌──────────┐               │
│  │ Frontend │   │ Backend  │    │ Admin    │               │
│  │ npm dev  │   │ Docker   │    │ Docker   │               │
│  │ :3000    │──▶│ :8000    │◀──▶│ :3001    │               │
│  └──────────┘   └────┬─────┘    └──────────┘               │
│                      │                                      │
│              ┌───────▼────────┐                             │
│              │ Cloud SQL Proxy│                             │
│              │ Docker :5432   │────▶ GCP Cloud SQL (prod)  │
│              └────────────────┘                             │
└─────────────────────────────────────────────────────────────┘
```

## Prerequisites

1. **Docker Desktop** running
2. **GCP credentials** authenticated and copied into the project:
   ```bash
   gcloud auth application-default login
   mkdir -p .gcloud
   cp ~/.config/gcloud/application_default_credentials.json .gcloud/
   ```
   The `.gcloud/` directory is gitignored — credentials will never be committed.
3. Your production `.env` file at the project root (already exists)

## Quick Start

### 1. Make sure your `.env` has these variables

Your existing `.env` should already have most of these from production. Verify it includes:

| Variable | Required For | Source |
|----------|-------------|--------|
| `CLOUD_SQL_INSTANCE` | Cloud SQL Proxy | GCP Console → SQL → Instance connection name |
| `POSTGRES_USER` | DB auth | Your Cloud SQL credentials |
| `POSTGRES_PASSWORD` | DB auth | Your Cloud SQL credentials |
| `POSTGRES_DB` | DB name | Your Cloud SQL database name |
| `SECRET_KEY` | Backend signing | Same as production |
| `FIRST_SUPERUSER` | Admin account | Same as production |
| `FIRST_SUPERUSER_PASSWORD` | Admin password | Same as production |
| `ANTHROPIC_API_KEY` | LLM features | Same as production |
| `OPENROUTER_API_KEY` | LLM features | Same as production |

### 2. Start the Docker stack

```bash
docker compose -f docker-compose.dev.yml up --build
```

This will:
- Build backend & admin containers
- Start Cloud SQL Proxy (connects to your production DB)
- Run database migrations via `prestart`
- Start backend on port **8000**
- Start admin panel on port **3001**

### 3. Start the frontend locally

In a separate terminal:

```bash
cd frontend
npm run dev
```

This runs Next.js dev server on port **3000**.

## URLs

| Service | URL | Notes |
|---------|-----|-------|
| Frontend | http://localhost:3000 | `npm run dev` |
| Backend API | http://localhost:8000 | Docker container |
| API Docs (Swagger) | http://localhost:8000/docs | Auto-generated |
| Admin Panel | http://localhost:3001 | Docker container |
| DB (via proxy) | localhost:5433 | For DBeaver/pgAdmin connections |

## Stopping Everything

```bash
# Stop all containers (keep data)
docker compose -f docker-compose.dev.yml down

# Stop and remove volumes (WARNING: clears local cache)
docker compose -f docker-compose.dev.yml down -v
```

## Hot Reload

- **Backend**: Code changes in `./backend/` trigger automatic reload (volume mounted)
- **Frontend**: Next.js dev server handles hot reload automatically
- **Admin**: Rebuild with `docker compose -f docker-compose.dev.yml up --build admin` after code changes

## Troubleshooting

### Cloud SQL Proxy won't start
```bash
# Check if you're authenticated
gcloud auth application-default print-access-token

# If not, authenticate:
gcloud auth application-default login
```

### Backend can't connect to database
- Verify `CLOUD_SQL_INSTANCE` format: `project-id:region:instance-name`
- Check proxy logs: `docker compose -f docker-compose.dev.yml logs cloud-sql-proxy`
- Ensure your GCP service account has Cloud SQL Client role

### Frontend CORS errors
The backend is configured to allow `http://localhost:3000`. If you change the frontend port, update `BACKEND_CORS_ORIGINS` in `docker-compose.dev.yml`.

### Prestart fails on migrations
```bash
# Check prestart logs
docker compose -f docker-compose.dev.yml logs prestart

# Re-run just prestart (idempotent)
docker compose -f docker-compose.dev.yml up prestart
```

## Optional: Local Postgres Instead of Production DB

If you want a sandbox database instead of hitting production, swap the Cloud SQL Proxy for a local Postgres container. See `docker-compose.yml` for reference — it has a `db` service running PostgreSQL 17 locally.

To use that instead:
1. Comment out/remove the `cloud-sql-proxy` service in `docker-compose.dev.yml`
2. Add the `db` service from `docker-compose.yml`
3. Change all `POSTGRES_SERVER=cloud-sql-proxy` to `POSTGRES_SERVER=db`
4. Set local DB credentials in your `.env`
