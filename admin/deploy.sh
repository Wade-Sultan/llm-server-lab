#!/usr/bin/env bash
# admin/deploy.sh — deploy Palladium admin (Next.js) to Compute Engine
set -euo pipefail

# ---- Config ----
INSTANCE="palladium-admin"
ZONE="us-central1-a"
PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
BRANCH="${BRANCH:-main}"

# ---- Colors ----
G='\033[0;32m'; Y='\033[0;33m'; R='\033[0;31m'; N='\033[0m'
log()  { echo -e "${G}==>${N} $*"; }
warn() { echo -e "${Y}!! ${N} $*"; }
die()  { echo -e "${R}xx ${N} $*" >&2; exit 1; }

# ---- Preflight ----
command -v gcloud >/dev/null || die "gcloud not installed"
[[ -n "$PROJECT" ]] || die "GCP_PROJECT not set and no default project configured"
gcloud config set project "$PROJECT" >/dev/null

if [[ -n "$(git status --porcelain)" ]]; then
  warn "You have uncommitted changes. They will NOT be deployed."
  read -rp "Continue anyway? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || die "Aborted."
fi

LOCAL_SHA=$(git rev-parse "$BRANCH")
REMOTE_SHA=$(git ls-remote origin "$BRANCH" | awk '{print $1}')
if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
  die "Local $BRANCH ($LOCAL_SHA) doesn't match origin ($REMOTE_SHA). Push first."
fi

log "Deploying $BRANCH @ ${LOCAL_SHA:0:8} to $INSTANCE"

# ---- Remote build & restart ----
gcloud compute ssh "$INSTANCE" \
  --zone="$ZONE" \
  --tunnel-through-iap \
  --command="bash -s" <<'REMOTE'
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:$HOME/.nvm/versions/node/$(node --version 2>/dev/null || echo 'v22')/bin:$PATH"
source /etc/profile 2>/dev/null || true

cd ~/palladium
echo "==> Fetching latest code"
git fetch origin
git reset --hard origin/main

cd admin

echo "==> Loading secrets"
DB_PASS=$(gcloud secrets versions access latest --secret=palladium-db-password-prod)
ADMIN_PASS=$(gcloud secrets versions access latest --secret=palladium-admin-password 2>/dev/null || echo "admin")

export DATABASE_URL="postgresql://palladium_app:${DB_PASS}@localhost:5432/palladium"
export ADMIN_PASSWORD="${ADMIN_PASS}"
export NODE_ENV=production

echo "==> Installing dependencies"
npm ci --production=false

echo "==> Generating Prisma client"
npx prisma generate

echo "==> Building"
npm run build

echo "==> Restarting service"
sudo systemctl restart palladium-admin
sudo systemctl is-active palladium-admin
REMOTE

log "Deploy complete. Checking health..."
sleep 3
gcloud compute ssh "$INSTANCE" --zone="$ZONE" --tunnel-through-iap \
  --command="sudo systemctl status palladium-admin --no-pager | head -20"

log "Done ✓"
