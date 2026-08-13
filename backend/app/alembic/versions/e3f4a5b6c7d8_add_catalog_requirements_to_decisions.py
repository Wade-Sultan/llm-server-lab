"""add catalog_requirements to module_decisions

Makes the appropriateness metrics' sufficiency term measurable.

THE GAP THIS CLOSES. services/recommender/appropriateness.py scores a part
choice as sufficiency * efficiency, where sufficiency asks whether the part
clears the hardware floors the user's named games / software / AI models imply.
Those floors were computed per build by catalog_match and folded into the
prompt as prose — and then discarded. So scoring a recorded decision could only
ever measure price efficiency, and reported sufficiency as an explicitly missing
signal rather than a number.

WHY A COLUMN RATHER THAN A KEY IN input_state. input_state is the verbatim
snapshot of the arguments the DSPy signature received, and it is what a GEPA
replay reads back to rebuild an Example. An extra key there would become a
phantom input field on replay — present in the recorded state, absent from the
signature — so the requirements get their own column instead.

WHY SNAPSHOT AT ALL RATHER THAN RECOMPUTE. The floors come from catalogs and
embeddings that keep moving: games get patched, tiers get edited, the embedding
model gets swapped. Recomputing months later would score a decision against a
yardstick the model never saw, which is the same reasoning that already makes
candidate_set a verbatim snapshot rather than a set of foreign keys.

BACKFILL IS IMPOSSIBLE and deliberately not attempted. The floors for a past
build depended on what the user named in that conversation and on the catalog as
it stood then; neither is recoverable. Existing rows stay NULL, and
scripts/score_appropriateness.py reports them as uninformative rather than
scoring them as if sufficiency had been checked and passed.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "module_decisions",
        sa.Column("catalog_requirements", JSONB, nullable=True),
    )
    # Partial index on the rows that have requirements at all. That is the
    # query the GEPA trainset builder runs — "give me decisions whose
    # sufficiency is measurable" — and for a long while it will select a small
    # minority of the table, which is exactly when a partial index earns its
    # keep over a full one.
    op.execute(
        "CREATE INDEX ix_module_decisions_has_requirements "
        "ON module_decisions (category, created_at) "
        "WHERE catalog_requirements IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_module_decisions_has_requirements")
    op.drop_column("module_decisions", "catalog_requirements")
