"""normalize_reference_build_pricing_cents

reference_builds.total_approx / reference_build_parts.approx_price are
supposed to be cents (same convention as every other *_cents column, see
chat_pipeline.py's _assemble_dspy_build docstring and build-card.tsx's
unconditional `total_approx / 100`). seed_reference_builds.py used to write
them un-multiplied (dollars) while the admin UI (reference-builds/actions.ts)
has always written them correctly in cents. So today this table has a mix:
rows never touched since seeding are dollar-scale, rows edited via admin are
cents-scale, in the same columns.

This normalizes existing rows still sitting at dollar-scale up to cents.
Classification happens at the build level (not per-part): observed
dollar-scale build totals top out around $12,000, so even the cheapest
dollar-scale build misread as cents (110,000+) is nowhere near a real
cents-scale total — there's no overlap risk at that granularity, unlike
individual part prices which can plausibly land in either range. Each
build's parts are updated using the SAME classification as their parent
build (before the build row itself is updated), since a single admin edit
writes a build's total and all its parts' prices together in one cents-scale
mutation.

Revision ID: d1e2f3a4b5c6
Revises: e2f3a4b5c6d7
Create Date: 2026-07-16

"""
from alembic import op

revision = 'd1e2f3a4b5c6'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None

# An order of magnitude above the real dollar-scale build-total max ($12,000)
# and far below any plausible cents-scale build total ($1,100+ * 100).
_DOLLAR_SCALE_THRESHOLD = 50000


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE reference_build_parts rbp
        SET approx_price = approx_price * 100
        FROM reference_builds rb
        WHERE rbp.build_id = rb.id AND rb.total_approx < {_DOLLAR_SCALE_THRESHOLD}
        """
    )
    op.execute(
        f"""
        UPDATE reference_builds
        SET total_approx = total_approx * 100
        WHERE total_approx < {_DOLLAR_SCALE_THRESHOLD}
        """
    )


def downgrade() -> None:
    # Not reversible in general — a row's original dollar-vs-cents scale
    # cannot be recovered once normalized, since post-upgrade every row is
    # legitimately cents-scale (indistinguishable from a build that was
    # always cents-scale).
    raise NotImplementedError(
        "normalize_reference_build_pricing_cents cannot be downgraded: "
        "the pre-migration dollars/cents split per row is not recoverable."
    )
