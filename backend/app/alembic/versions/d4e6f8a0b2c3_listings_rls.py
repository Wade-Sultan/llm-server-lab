"""row-level security on listings — admin writes, commerce reads, builder neither

Makes the affiliate-data boundary the /about page describes an enforced one
rather than a convention. Three roles, three levels of access to `listings`
and its two subtype tables:

    palladium_admin      full read/write   (admin, via Prisma)
    palladium_commerce   read only         (commerce, via pgx)
    palladium_app        no access at all  (builder / worker / cron jobs)

The builder's exclusion is the point. eBay Partner Network's agreement bars
eBay data from reaching an AI system, and the builder is the service that runs
the DSPy pipeline and writes the GEPA telemetry dataset — so the guarantee is
worth more coming from the database than from a code review. Nothing in the
builder reads `listings` any more (the Amazon URL lookup in
crud/reference_builds.py was removed in the same change); the frontend resolves
marketplace links from commerce's live listings instead.

RLS is FORCEd so it applies to the tables' owner too, not just to other roles.
Note the residual hole that leaves: whichever role owns these tables can still
DROP POLICY or set NO FORCE. Ownership currently sits with the migration role,
so this stops accidental reads — a future query in the builder returns nothing
— rather than a determined one. Moving ownership to palladium_admin closes it,
at the cost of listings DDL no longer running under the builder's role; see
deploy/runbooks/listings-rls-roles.md.

Revision ID: d4e6f8a0b2c3
Revises: c1d3e5f7a9b2
Create Date: 2026-08-20 00:00:00.000000

"""

from alembic import op
from sqlalchemy import text

from app.db.rls import RLSPolicy, add_policy, disable_rls, drop_policy, enable_rls

revision = "d4e6f8a0b2c3"
down_revision = "c1d3e5f7a9b2"
branch_labels = None
depends_on = None

ADMIN_ROLE = "palladium_admin"
COMMERCE_ROLE = "palladium_commerce"
BUILDER_ROLE = "palladium_app"

# listings holds the eBay rows directly (an eBay listing is a base row with no
# subtype — see admin/src/lib/listings.ts), so the base table is the one that
# matters. The subtypes are covered too: amazon_listings today, ebay_listings
# because it exists in the schema and must not become a way around this.
TABLES = ("listings", "amazon_listings", "ebay_listings")


def _require_roles() -> None:
    """Fail with a usable message rather than a bare 'role does not exist'.

    The roles are created by the runbook, not here: creating them needs
    CREATEROLE, which the migration role does not have on Cloud SQL, and the
    passwords have to come from the secret store rather than a committed file.
    """
    missing = [
        role
        for role in (ADMIN_ROLE, COMMERCE_ROLE, BUILDER_ROLE)
        if op.get_bind()
        .execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
        .first()
        is None
    ]
    if missing:
        raise RuntimeError(
            f"missing database role(s): {', '.join(missing)}. Create them and "
            "point each service's credentials at the right one before running "
            "this migration — see deploy/runbooks/listings-rls-roles.md."
        )


def upgrade():
    _require_roles()

    for table in TABLES:
        # Table privileges first: RLS narrows what a role may see, it does not
        # grant access in the first place, and a role with no privileges gets a
        # hard permission error rather than an empty result — which is what we
        # want for the builder, so a future stray query fails loudly in tests
        # instead of silently returning nothing.
        op.execute(text(f"REVOKE ALL ON TABLE {table} FROM PUBLIC"))
        op.execute(text(f"REVOKE ALL ON TABLE {table} FROM {BUILDER_ROLE}"))
        op.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} "
                f"TO {ADMIN_ROLE}"
            )
        )
        op.execute(text(f"GRANT SELECT ON TABLE {table} TO {COMMERCE_ROLE}"))

        enable_rls(op, table, force=True)

        add_policy(
            op,
            RLSPolicy(
                name=f"{table}_admin_all",
                table=table,
                command="ALL",
                using="true",
                role=ADMIN_ROLE,
            ),
        )
        add_policy(
            op,
            RLSPolicy(
                name=f"{table}_commerce_select",
                table=table,
                command="SELECT",
                using="true",
                role=COMMERCE_ROLE,
            ),
        )
        # No policy for BUILDER_ROLE, deliberately. With RLS on and no policy
        # naming it, that role sees zero rows even if its table privileges are
        # ever granted back by mistake.


def downgrade():
    for table in TABLES:
        drop_policy(op, f"{table}_commerce_select", table)
        drop_policy(op, f"{table}_admin_all", table)
        disable_rls(op, table)
        op.execute(text(f"REVOKE ALL ON TABLE {table} FROM {ADMIN_ROLE}"))
        op.execute(text(f"REVOKE ALL ON TABLE {table} FROM {COMMERCE_ROLE}"))
        op.execute(
            text(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} "
                f"TO {BUILDER_ROLE}"
            )
        )
