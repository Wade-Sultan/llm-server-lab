"""add the AI workloads catalog

Creates ai_models (catalog of models with a family discriminator: llm,
multimodal, image_gen, video_gen, speech, vision, embedding) and ai_workloads
(one row per model x task x precision cell, carrying VRAM/RAM/storage floors
and GPU backend/feature constraints). Eligible GPUs are computed by joining
against gpu_chipsets rather than stored, so there is no minimum-parts table.

Revision ID: b7c8d9e0f1a2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "b7c8d9e0f1a2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("family", sa.String(30), nullable=False, index=True),
        sa.Column("params_billions", sa.Float(), nullable=True),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("developer", sa.String(255), nullable=True),
        sa.Column("license", sa.String(100), nullable=True),
        sa.Column("huggingface_id", sa.String(255), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("spec", JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "ai_workloads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("model_id", UUID(as_uuid=True),
                  sa.ForeignKey("ai_models.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("task", sa.String(30), nullable=False),
        sa.Column("precision", sa.String(20), nullable=False),
        sa.Column("gpu_importance", sa.String(20), nullable=False,
                  server_default="required"),
        sa.Column("min_vram_gb", sa.Integer(), nullable=True),
        sa.Column("recommended_vram_gb", sa.Integer(), nullable=True),
        sa.Column("supports_multi_gpu", sa.Boolean(), nullable=False,
                  server_default="false"),
        sa.Column("cpu_offload_capable", sa.Boolean(), nullable=False,
                  server_default="false"),
        sa.Column("min_ram_gb", sa.Integer(), nullable=True),
        sa.Column("recommended_ram_gb", sa.Integer(), nullable=True),
        sa.Column("min_storage_gb", sa.Integer(), nullable=True),
        sa.Column("gpu_backends", ARRAY(sa.String()), nullable=False,
                  server_default="{cuda}"),
        sa.Column("required_gpu_features", ARRAY(sa.String()), nullable=True),
        sa.Column("assumptions", JSONB(), nullable=True),
        sa.Column("extra_requirements", JSONB(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("model_id", "task", "precision",
                            name="uq_ai_workloads_model_task_precision"),
    )


def downgrade():
    op.drop_table("ai_workloads")
    op.drop_table("ai_models")
