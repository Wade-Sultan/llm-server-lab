import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.software_catalog import (
    GpuImportance,  # noqa: F401 — reused by ai_workloads.gpu_importance
)

# Unlike games (one title → fixed per-tier specs), an AI workload's hardware
# needs are a function of three orthogonal axes:
#
#   model     × task                    × precision
#   Llama 70B   inference / fine-tune     fp16 / fp8 / int8 / q4 ...
#
# The same model spans a ~20x VRAM range across that matrix (70B: ~42GB at q4
# inference, ~140GB at fp16, ~48GB for QLoRA, ~1TB+ for a full fine-tune), so
# tasks and model families are NOT parallel subtables — ai_models holds the
# catalog entity with a family discriminator, and ai_workloads holds one row
# per (model, task, precision) cell that has been curated.
#
# There is deliberately no ai_minimum_parts table. For games the "minimum GPU"
# is an opaque publisher claim that must be stored; for AI the requirement IS
# the VRAM/feature floor, so the set of eligible GPUs is a computed join
# against gpu_chipsets (vram_gb >= min, brand matches gpu_backends,
# supported_features ⊇ required_gpu_features) that stays correct as the parts
# catalog grows.


class AIModelFamily(str, enum.Enum):
    LLM = "llm"  # text-only language models
    MULTIMODAL = "multimodal"  # VLMs — text + vision (LLaVA, Qwen-VL)
    IMAGE_GEN = "image_gen"  # diffusion / flow-matching (SDXL, Flux)
    VIDEO_GEN = "video_gen"  # video diffusion (Wan, HunyuanVideo)
    SPEECH = "speech"  # ASR / TTS (Whisper, Piper)
    AUDIO_GEN = (
        "audio_gen"  # music / audio generation (MusicGen, Bark) — far heavier than ASR
    )
    VISION = "vision"  # CNNs / ViTs (YOLO, ResNet, SAM)
    EMBEDDING = "embedding"  # sentence/embedding models
    CLASSICAL = (
        "classical"  # gradient boosting / sklearn — CPU cores + RAM, GPU irrelevant
    )
    RL = "rl"  # reinforcement learning — CPU-heavy env simulation, modest GPU


class AITask(str, enum.Enum):
    INFERENCE = "inference"
    FINE_TUNE_FULL = (
        "fine_tune_full"  # all weights updated — optimizer states dominate VRAM
    )
    FINE_TUNE_LORA = (
        "fine_tune_lora"  # frozen base at native precision + low-rank adapters
    )
    FINE_TUNE_QLORA = "fine_tune_qlora"  # 4-bit-quantized frozen base + adapters
    POST_TRAIN = "post_train"  # alignment passes on a tuned model (DPO / RLHF-style)
    TRAIN_SCRATCH = (
        "train_scratch"  # from random init (realistic for vision/classical/RL scale)
    )


class AIModel(Base):
    __tablename__ = "ai_models"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    name = Column(String(255), nullable=False, unique=True)  # e.g. "Llama 3.1 70B"
    slug = Column(String(255), nullable=False, unique=True)  # e.g. "llama-3-1-70b"

    family = Column(String(30), nullable=False, index=True)  # AIModelFamily value

    # Parameter count in billions (float so vision models fit: ResNet-50 = 0.026).
    # Drives the profile's small (≤8B) / medium (9–34B) / large (70B+) scale buckets.
    params_billions = Column(Float, nullable=True)

    # LLM / multimodal: maximum context window in tokens.
    context_length = Column(Integer, nullable=True)

    developer = Column(String(255), nullable=True)
    license = Column(
        String(100), nullable=True
    )  # e.g. "apache-2.0", "llama-3.1-community"
    huggingface_id = Column(
        String(255), nullable=True
    )  # e.g. "meta-llama/Llama-3.1-70B-Instruct"
    website_url = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)

    # Family-specific attributes that don't warrant columns across all families:
    # image_gen → {"base_resolution": 1024}, vision → {"input_size": 640}, etc.
    spec = Column(JSONB, nullable=True)

    notes = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    workloads = relationship(
        "AIWorkload",
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="AIWorkload.sort_order",
    )

    @property
    def model_scale(self) -> str | None:
        """Bucket matching BuildProfile.ai_model_scale (small / medium / large)."""
        if self.params_billions is None:
            return None
        if self.params_billions <= 8:
            return "small"
        if self.params_billions < 70:
            return "medium"
        return "large"


class AIWorkload(Base):
    __tablename__ = "ai_workloads"

    __table_args__ = (
        UniqueConstraint(
            "model_id",
            "task",
            "precision",
            name="uq_ai_workloads_model_task_precision",
        ),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task = Column(String(30), nullable=False)  # AITask value

    # Weight precision / quantization this row's numbers assume. Kept a free
    # string (not an enum) because quant formats churn: fp32, bf16, fp16, fp8,
    # int8, q8, q6, q5, q4, q3 — extend as formats appear (AWQ, GPTQ, MXFP4...).
    precision = Column(String(20), nullable=False)

    # How much a discrete GPU matters (reuses software_catalog.GpuImportance):
    # most rows are "required"; CPU-offload LLM inference and small embedding
    # models are "accelerated"/"optional".
    gpu_importance = Column(
        String(20),
        nullable=False,
        default=GpuImportance.REQUIRED.value,
        server_default=GpuImportance.REQUIRED.value,
    )

    # VRAM figures are TOTAL across all GPUs. When min_vram_gb exceeds any
    # single card in the catalog and supports_multi_gpu is true, the workload
    # calls for a multi-GPU build; if supports_multi_gpu is false it is simply
    # out of reach for that catalog.
    min_vram_gb = Column(Integer, nullable=True)  # NULL = runs fine with no GPU
    recommended_vram_gb = Column(Integer, nullable=True)
    supports_multi_gpu = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Workload shards/splits across GPUs (tensor/pipeline parallel, "
        "llama.cpp row split); false = must fit on one card",
    )

    # True when weights can spill to system RAM at usable speed (llama.cpp /
    # GGUF layer offload, whisper.cpp) — makes recommended_ram_gb the lever
    # when VRAM is short instead of a hard wall.
    cpu_offload_capable = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # System RAM: data pipelines, offload headroom. Rule of thumb: >= total VRAM.
    min_ram_gb = Column(Integer, nullable=True)
    recommended_ram_gb = Column(Integer, nullable=True)

    # Weights + datasets + checkpoints (training checkpoints multiply weight size).
    min_storage_gb = Column(Integer, nullable=True)

    # Which GPU stacks can run this: subset of {"cuda", "rocm", "metal"}.
    # Maps to GPU brand in the parts catalog (cuda → nvidia, rocm → amd).
    gpu_backends = Column(ARRAY(String), nullable=False, server_default="{cuda}")

    # Feature floor matched against gpu_chipsets.supported_features,
    # e.g. {"bf16"} (Ampere+), {"fp8"} (Ada/Hopper+), {"tensor_cores"}.
    required_gpu_features = Column(ARRAY(String), nullable=True)

    # Workload assumptions behind the numbers, so rows are comparable:
    # inference → {"context_tokens": 8192, "batch_size": 1}
    # fine-tune → {"batch_size": 4, "seq_len": 2048, "gradient_checkpointing": true}
    assumptions = Column(JSONB, nullable=True)

    extra_requirements = Column(JSONB, nullable=True)

    sort_order = Column(Integer, nullable=False, default=0)

    notes = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    model = relationship("AIModel", back_populates="workloads")
