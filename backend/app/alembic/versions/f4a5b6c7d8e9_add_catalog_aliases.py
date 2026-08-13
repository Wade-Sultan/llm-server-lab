"""add aliases to the games, software and ai_models catalogs

Users do not type published titles. They type "R6", "Siege", "Val", "Tarkov",
"Resolve", "SD" — community names that appear nowhere in the catalog row, and
therefore nowhere in the text app/services/embeddings/text.py embeds. A vector
search for "R6" against "Rainbow Six Siege. competitive fps video game." only
resolves if the embedding model happens to have learned that association, and
for anything narrower than the most famous abbreviations it will not have.

The failure is silent and expensive: catalog_match finds nothing above its
distance cutoff, the build proceeds with no hardware floors attached, and
nothing anywhere reports that a title the user named went unrecognised.

WHY ALL THREE TABLES IN ONE MIGRATION. The problem is identical across them —
"Resolve" for DaVinci Resolve, "SD" for Stable Diffusion — the column is the
same, and the alternative is running this migration twice more later.

NOT NULL WITH AN EMPTY DEFAULT, matching software.use_case_tags rather than
games.hard_requirements. An empty array and NULL would mean the same thing here
("no aliases recorded"), and having one representation instead of two removes a
null check from every builder and query that touches it.

NO INDEX, DELIBERATELY. The lookup in catalog_match normalizes both sides
(lowercase, trimmed) before comparing, so a GIN index on the raw array would not
be usable by that query — it would be an index that looks like a guarantee and
delivers a sequential scan anyway. These catalogs hold hundreds to low thousands
of rows, where that scan is genuinely free. Add a real index when the query plan
says to, not before.

THIS CHANGES EVERY EMBEDDING FOR THESE TYPES. The text builders now include
aliases, so the content hashes move and the next reconcile sweep re-embeds every
game, software and AI model row that has one — by design, that is exactly how a
source-text change is meant to propagate (see the note at the top of
embeddings/text.py). Rows with no aliases hash identically to before and are
skipped, so the cost is proportional to how many rows actually get filled in.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-08-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None

_TABLES = ("games", "software", "ai_models")


def upgrade():
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "aliases",
                ARRAY(sa.String()),
                nullable=False,
                server_default="{}",
            ),
        )


def downgrade():
    for table in _TABLES:
        op.drop_column(table, "aliases")
