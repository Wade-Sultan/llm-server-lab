# Listings RLS — database roles

Run this **before** `alembic upgrade head` picks up
`d4e6f8a0b2c3_listings_rls.py`. That migration refuses to run until the three
roles exist, because creating them needs `CREATEROLE` (which the migration role
does not have on Cloud SQL) and their passwords belong in Secret Manager rather
than in a committed file.

## What the boundary is

| Role                 | Service                        | `listings` access |
| -------------------- | ------------------------------ | ----------------- |
| `palladium_admin`    | admin (Prisma)                 | read + write      |
| `palladium_commerce` | commerce (pgx)                 | read only         |
| `palladium_app`      | builder, worker, cron jobs     | none              |

The builder's exclusion is the one that matters: it runs the DSPy pipeline and
writes the GEPA telemetry dataset, and eBay Partner Network's agreement bars
eBay data from reaching an AI system. Enforcing that in the database is worth
more than enforcing it in code review.

## 1. Generate the two passwords

Nothing hands these to you — you invent them here. Secret Manager is where they
end up in step 6, not where they come from. Keep them in the shell for the
length of this runbook: step 3 needs them for `CREATE ROLE`, and step 6 needs
them again to build each DSN.

```bash
ADMIN_PW=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40)
COMMERCE_PW=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40)
```

Alphanumerics only, deliberately: these get embedded in a `postgresql://`
connection string, and a `/`, `@`, `:` or `#` in a password has to be
percent-encoded there. Postgres does not care; the URL parser does, and the
failure it produces — an auth error against a host that is really a fragment of
the password — is a genuinely confusing hour.

Do not echo them. Step 6 writes them into the DSN secrets, which is the only
copy you need and the copy the services actually read.

## 2. Get a `postgres` login, and open the proxy

Only this step needs `postgres`; everything after it runs as `palladium_app`.
Cloud SQL does not store that password anywhere readable, so if it was not
recorded when the instance was created, the only way to get one is to set a new
one.

That is safe here because nothing authenticates as `postgres` — the builder
uses `palladium_app` (`deploy/base/builder/configmap.yaml`), and admin and
commerce use their own `DATABASE_URL`. Confirm that before resetting:

```bash
NS=palladium
for svc in admin commerce; do
  kubectl -n "$NS" get secret "palladium-secrets-$svc" \
    -o jsonpath='{.data.DATABASE_URL}' | base64 -d | grep -o 'postgresql://[^:]*'
done
```

Neither should say `postgres`. If one does, stop — resetting the password would
break that service, and it needs moving to its own role first.

It may already be stored:

```bash
PROJECT=project-b8abf13d-d1ce-43f1-837
INSTANCE=$(grep -E '^CLOUD_SQL_INSTANCE=' .env | cut -d= -f2-)   # project:region:palladium-db

gcloud secrets list --project=$PROJECT | grep -iE 'postgres|superuser|db-root'
```

If not, set a new one. This neither restarts the instance nor disturbs existing
connections:

```bash
gcloud sql users set-password postgres \
  --instance="${INSTANCE##*:}" --project=$PROJECT --prompt-for-password
```

`--prompt-for-password` keeps it out of shell history. Store it while you have
it, so the next person is not repeating this:

```bash
printf '%s' '<the-password>' \
  | gcloud secrets create palladium-db-postgres --data-file=- --project=$PROJECT
```

Then open the proxy the next step connects through, and leave it running for
the rest of the runbook — unless one is already up, which it often is:

```bash
ss -ltnp | grep ':5433' || ./cloud-sql-proxy --port 5433 "$INSTANCE" &
```

`address already in use` on that port is usually the proxy you started earlier,
not something in the way. Check what holds it before killing anything — if it
already points at this instance, use it and move on:

```bash
ps -o pid,etime,args -p "$(ss -ltnp | grep -oP ':5433.*pid=\K[0-9]+' | head -1)"
```

Do not solve this by granting `CREATEROLE` to `palladium_app`. That would let
the builder's own role create a role that bypasses the boundary this runbook
exists to build.

## 3. Create the roles

As a role with `CREATEROLE` — on Cloud SQL that is the `postgres` user, not
`palladium_app`. The password psql prompts for is that `postgres` password, the
one from step 2; the two generated passwords are never typed at a prompt, they
ride in on the `-v` flags below.

Which makes this worth checking first — the generated passwords have to still
be set in *this* shell. In a fresh terminal they are empty, and `-v
admin_pw=""` produces `CREATE ROLE ... PASSWORD ''` without complaint: roles
with blank passwords, which step 6 then writes into the DSNs, failing only much
later as an authentication error.

```bash
[ -n "$ADMIN_PW" ] && [ -n "$COMMERCE_PW" ] && echo "both set" || echo "EMPTY — rerun step 1"
```

Regenerating is free as long as it happens before `CREATE ROLE`. Then connect
through the proxy and let psql interpolate the variables rather than retyping
them:

```bash
psql "postgresql://postgres@127.0.0.1:5433/palladium" \
  -v admin_pw="$ADMIN_PW" -v commerce_pw="$COMMERCE_PW" <<'SQL'
CREATE ROLE palladium_admin    LOGIN PASSWORD :'admin_pw';
CREATE ROLE palladium_commerce LOGIN PASSWORD :'commerce_pw';

GRANT CONNECT ON DATABASE palladium TO palladium_admin, palladium_commerce;
GRANT USAGE   ON SCHEMA public      TO palladium_admin, palladium_commerce;
SQL
```

`:'admin_pw'` is psql's quoted-variable form: it escapes the value as a SQL
literal, so the password is never typed into a file and never needs hand
quoting.

## 4. Grant each role the rest of its working set

These are plain SQL, so Cloud SQL Studio is a fine place to run them if the
proxy is giving you trouble — as is step 7. Only step 5 needs a shell, because
Alembic makes its own connection. Step 3 is better kept in psql: run it in
Studio and the two generated passwords are typed into a browser query editor
rather than staying in shell variables, which puts them in the query history
that Studio keeps.

If you run them as `postgres`, take the owner's identity first. On Cloud SQL
`postgres` is a member of `cloudsqlsuperuser`, not a true superuser, and
granting privileges on a table requires owning it or holding grant option —
`palladium_app` owns these. Without this, the grants below error or quietly
skip the tables `postgres` does not own, and `ALTER DEFAULT PRIVILEGES FOR ROLE
palladium_app` fails outright for want of membership:

```sql
SELECT pg_has_role('postgres', 'palladium_app', 'member') AS can_act_as_owner;

-- If false — postgres has CREATEROLE, so it can grant itself the membership:
GRANT palladium_app TO postgres;
SET ROLE palladium_app;
-- ... run the grants below, then:
RESET ROLE;
```

**Do this before the migration, not after.** These are broad grants, and the
migration is what narrows `listings` back down afterwards. Running a blanket
`GRANT ... ON ALL TABLES` again later silently reopens the boundary — if you
ever need to, re-run the migration's grant block after it.

Admin needs everything; it is the operator UI:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO palladium_admin;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO palladium_admin;
ALTER DEFAULT PRIVILEGES FOR ROLE palladium_app IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO palladium_admin;
```

Commerce touches five tables (derived from `commerce/internal/store/*.go`, not
assumed) — `listings` and `amazon_listings` are in the list because the
migration grants it `SELECT` on them; do not widen those two here:

```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON users, listing_lookup_failures TO palladium_commerce;
GRANT SELECT                          ON pc_parts                      TO palladium_commerce;
```

If commerce absorbs another slice of the FastAPI service, this grant is the
thing to extend — it will fail loudly with `permission denied for table`, which
is the intended failure mode.

## 5. Run the migration

### Pre-flight

Five checks. Each one catches a failure that is quiet if you skip it.

**Both new roles can actually log in.** This is the real test of step 3 — it
covers the role existing, the password being what you think it is, and the
`GRANT CONNECT` all at once. An empty password from a fresh-terminal
`$ADMIN_PW` shows up here rather than after the cutover:

```bash
PGPASSWORD="$ADMIN_PW"    psql "postgresql://palladium_admin@127.0.0.1:5433/palladium"    -tAc 'SELECT current_user'
PGPASSWORD="$COMMERCE_PW" psql "postgresql://palladium_commerce@127.0.0.1:5433/palladium" -tAc 'SELECT current_user'
```

Each should print its own role name. Anything else — stop; the migration will
succeed and the services will fail later.

**Step 4's grants landed.** The migration narrows `listings`; it does not give
either role its working set:

```sql
SELECT has_table_privilege('palladium_admin','pc_parts','SELECT')                  AS admin_reads_parts,
       has_table_privilege('palladium_admin','listings','INSERT')                  AS admin_writes_listings,
       has_table_privilege('palladium_commerce','users','SELECT')                  AS commerce_reads_users,
       has_table_privilege('palladium_commerce','pc_parts','SELECT')               AS commerce_reads_parts,
       has_table_privilege('palladium_commerce','listing_lookup_failures','UPDATE') AS commerce_writes_failures;
```

All five `t`.

**RLS is not on yet**, so you know the migration is what turns it on and you
are not re-running over a half-applied state:

```sql
SELECT relname, relrowsecurity FROM pg_class
WHERE relname IN ('listings','amazon_listings','ebay_listings');
```

Three rows, all `f`.

**The migration role still owns the three tables**, or its DDL will fail
halfway through, after the revokes and before the policies:

```sql
SELECT c.relname, pg_get_userbyid(c.relowner) AS owner
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('listings','amazon_listings','ebay_listings');
```

All three owned by `palladium_app`. (If you have already done the ownership
transfer from the last section of this runbook, they will say
`palladium_admin`, and the migration needs to run as that role instead.)

**Alembic is where you expect:**

```bash
cd backend && .venv/bin/alembic current   # c1d3e5f7a9b2
.venv/bin/alembic heads                   # d4e6f8a0b2c3 (head)
```

Note that `alembic upgrade head --sql` does **not** work for this migration —
it queries `pg_roles` to check the roles exist, and offline mode has no
connection to query. Read the file instead if you want the SQL in advance.

### Take a backup

This migration revokes privileges, so a mistake is felt by every service at
once rather than by one query:

```bash
gcloud sql backups create --instance="${INSTANCE##*:}" --project=$PROJECT
```

### Then run it

```bash
cd backend && .venv/bin/alembic upgrade head
```

It enables `FORCE` RLS on `listings`, `amazon_listings`, and `ebay_listings`,
adds an ALL policy for `palladium_admin` and a SELECT policy for
`palladium_commerce`, and revokes the builder's privileges outright. No policy
names `palladium_app`, so even a mistaken future `GRANT` leaves it seeing zero
rows.

## 6. Point the services at the new roles

Each service reads its `DATABASE_URL` from its own scoped Secret — see
`deploy/overlays/prod/patches/secrets-scoped.yaml` for which Secret feeds which
workload. Only the credentials in the DSN change; the host, database and any
query parameters stay exactly as they are, so derive the new value from the old
one rather than retyping it.

The `palladium-secrets-*` Secrets in the cluster are created out-of-band from
Secret Manager (see the note in `deploy/overlays/prod/kustomization.yaml`), so
there are two copies to keep in step: the cluster's, which the pods read, and
Secret Manager's, which is the system of record and what a cluster rebuild
restores from. Update both, or the next rebuild silently reinstates the old
role.

```bash
NS=palladium

for pair in "admin:palladium_admin:$ADMIN_PW" "commerce:palladium_commerce:$COMMERCE_PW"; do
  svc=${pair%%:*}; rest=${pair#*:}; role=${rest%%:*}; pw=${rest#*:}

  old=$(kubectl -n "$NS" get secret "palladium-secrets-$svc" \
          -o jsonpath='{.data.DATABASE_URL}' | base64 -d)
  # Swap only the credentials: everything between "://" and the "@" ending them.
  new=$(printf '%s' "$old" | sed -E "s#^(postgresql://)[^@]*@#\1$role:$pw@#")

  kubectl -n "$NS" patch secret "palladium-secrets-$svc" \
    -p "{\"data\":{\"DATABASE_URL\":\"$(printf '%s' "$new" | base64 -w0)\"}}"
done
```

Check the result before restarting anything — the DSN should differ from the
old one in the username and password and in nothing else:

```bash
kubectl -n $NS get secret palladium-secrets-admin \
  -o jsonpath='{.data.DATABASE_URL}' | base64 -d | grep -o 'postgresql://[^:]*'
```

Then mirror the same two values into Secret Manager, under whatever names your
out-of-band process reads. Confirm the names first rather than assuming they
match the Kubernetes ones:

```bash
gcloud secrets list --project=project-b8abf13d-d1ce-43f1-837 | grep -i palladium
```

The builder keeps `palladium_app` and needs no change. Restart the two
deployments once both copies are updated — a running pod holds its existing
connection pool and will not pick up the new role on its own:

```bash
kubectl -n $NS rollout restart deployment/admin deployment/commerce
kubectl -n $NS rollout status  deployment/admin deployment/commerce
```

## 7. Verify

```sql
-- Policies are in place (expect 6 rows: 2 per table)
SELECT tablename, policyname, roles, cmd FROM pg_policies
WHERE schemaname = 'public' AND tablename LIKE '%listings%' ORDER BY tablename;

-- RLS is on AND forced (expect t/t for all three)
SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
WHERE relname IN ('listings','amazon_listings','ebay_listings');

-- The builder is locked out (expect: permission denied for table listings)
SET ROLE palladium_app;   SELECT count(*) FROM listings;
RESET ROLE;

-- Commerce reads but cannot write (first succeeds, second is denied)
SET ROLE palladium_commerce; SELECT count(*) FROM listings;
                             DELETE FROM listings WHERE false;
RESET ROLE;
```

Run the same four against the dev database through the cloud-sql-proxy first.

## Residual hole

`FORCE` makes RLS apply to the tables' owner as well, so the policies bind
`palladium_app` even though it owns the tables. What it cannot stop is that
same role running `DROP POLICY` or `ALTER TABLE ... NO FORCE ROW LEVEL
SECURITY`. This setup therefore prevents accidental access — a future query
added to the builder fails — not a determined bypass.

Closing it means transferring ownership:

```sql
ALTER TABLE listings, amazon_listings, ebay_listings OWNER TO palladium_admin;
```

The cost is that any later DDL on those three tables no longer runs under
`palladium_app`, so an Alembic migration touching them needs to connect as
`palladium_admin`. Worth doing if the boundary is ever something you have to
demonstrate rather than describe.
