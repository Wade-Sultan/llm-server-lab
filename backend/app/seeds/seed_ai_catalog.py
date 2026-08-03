"""Seed a curated starter set of AI models and workload requirement rows.

Idempotent: models are matched by slug and workloads by (model, task,
precision); existing rows are updated in place, so re-running syncs the
curated numbers.

VRAM figures follow the standard arithmetic, with assumptions recorded on
each row so future entries stay comparable:
  - inference:        weights (params x bytes/param) x ~1.2 + KV cache
  - LoRA fine-tune:   frozen base at native precision + adapter grads/optimizer
  - QLoRA fine-tune:  4-bit frozen base + adapter states
  - full fine-tune:   ~16-20 bytes/param (weights + grads + AdamW states)
"""

from app.core.db import SessionLocal
from app.models.ai_catalog import AIModel, AIWorkload

_CATALOG: list[dict] = [
    {
        "model": {
            "name": "Llama 3.1 8B",
            "slug": "llama-3-1-8b",
            "family": "llm",
            "params_billions": 8.0,
            "context_length": 131072,
            "developer": "Meta",
            "license": "llama-3.1-community",
            "huggingface_id": "meta-llama/Llama-3.1-8B-Instruct",
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "q4",
                "min_vram_gb": 6,
                "recommended_vram_gb": 8,
                "cpu_offload_capable": True,
                "gpu_importance": "accelerated",
                "min_ram_gb": 16,
                "recommended_ram_gb": 32,
                "min_storage_gb": 10,
                "gpu_backends": ["cuda", "rocm", "metal"],
                "assumptions": {"context_tokens": 8192, "batch_size": 1},
                "notes": "Q4_K_M GGUF ~4.9GB; runs CPU-only via llama.cpp at reduced speed.",
            },
            {
                "task": "inference",
                "precision": "fp16",
                "min_vram_gb": 20,
                "recommended_vram_gb": 24,
                "min_ram_gb": 32,
                "recommended_ram_gb": 32,
                "min_storage_gb": 20,
                "gpu_backends": ["cuda", "rocm"],
                "assumptions": {"context_tokens": 8192, "batch_size": 1},
            },
            {
                "task": "fine_tune_qlora",
                "precision": "q4",
                "min_vram_gb": 12,
                "recommended_vram_gb": 16,
                "min_ram_gb": 32,
                "recommended_ram_gb": 64,
                "min_storage_gb": 40,
                "gpu_backends": ["cuda"],
                "assumptions": {
                    "batch_size": 2,
                    "seq_len": 2048,
                    "gradient_checkpointing": True,
                },
                "notes": "bitsandbytes 4-bit base; fits a single 16GB card comfortably.",
            },
            {
                "task": "fine_tune_lora",
                "precision": "bf16",
                "min_vram_gb": 20,
                "recommended_vram_gb": 24,
                "min_ram_gb": 32,
                "recommended_ram_gb": 64,
                "min_storage_gb": 40,
                "gpu_backends": ["cuda"],
                "required_gpu_features": ["bf16"],
                "assumptions": {
                    "batch_size": 2,
                    "seq_len": 2048,
                    "gradient_checkpointing": True,
                },
            },
            {
                "task": "post_train",
                "precision": "bf16",
                "min_vram_gb": 24,
                "recommended_vram_gb": 48,
                "min_ram_gb": 64,
                "recommended_ram_gb": 128,
                "min_storage_gb": 60,
                "gpu_backends": ["cuda"],
                "required_gpu_features": ["bf16"],
                "assumptions": {
                    "method": "dpo",
                    "adapter": "lora",
                    "batch_size": 1,
                    "seq_len": 2048,
                    "gradient_checkpointing": True,
                },
                "notes": "DPO holds policy + frozen reference model in memory; "
                "LoRA adapters keep it single-card, full DPO does not.",
            },
            {
                "task": "fine_tune_full",
                "precision": "bf16",
                "min_vram_gb": 160,
                "recommended_vram_gb": 320,
                "supports_multi_gpu": True,
                "min_ram_gb": 128,
                "recommended_ram_gb": 256,
                "min_storage_gb": 200,
                "gpu_backends": ["cuda"],
                "required_gpu_features": ["bf16"],
                "assumptions": {
                    "batch_size": 4,
                    "seq_len": 2048,
                    "optimizer": "adamw",
                    "sharding": "fsdp",
                },
                "notes": "Weights + grads + AdamW states ≈ 16-20 bytes/param; "
                "datacenter-class even at 8B.",
            },
        ],
    },
    {
        "model": {
            "name": "Qwen2.5 32B",
            "slug": "qwen2-5-32b",
            "family": "llm",
            "params_billions": 32.8,
            "context_length": 131072,
            "developer": "Alibaba",
            "license": "apache-2.0",
            "huggingface_id": "Qwen/Qwen2.5-32B-Instruct",
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "q4",
                "min_vram_gb": 24,
                "recommended_vram_gb": 24,
                "cpu_offload_capable": True,
                "gpu_importance": "accelerated",
                "min_ram_gb": 32,
                "recommended_ram_gb": 64,
                "min_storage_gb": 25,
                "gpu_backends": ["cuda", "rocm", "metal"],
                "assumptions": {"context_tokens": 8192, "batch_size": 1},
                "notes": "The single-24GB-card sweet spot for a mid-scale local LLM.",
            },
            {
                "task": "fine_tune_qlora",
                "precision": "q4",
                "min_vram_gb": 24,
                "recommended_vram_gb": 48,
                "min_ram_gb": 64,
                "recommended_ram_gb": 128,
                "min_storage_gb": 100,
                "gpu_backends": ["cuda"],
                "assumptions": {
                    "batch_size": 1,
                    "seq_len": 2048,
                    "gradient_checkpointing": True,
                },
            },
        ],
    },
    {
        "model": {
            "name": "Llama 3.1 70B",
            "slug": "llama-3-1-70b",
            "family": "llm",
            "params_billions": 70.6,
            "context_length": 131072,
            "developer": "Meta",
            "license": "llama-3.1-community",
            "huggingface_id": "meta-llama/Llama-3.1-70B-Instruct",
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "q4",
                "min_vram_gb": 48,
                "recommended_vram_gb": 48,
                "supports_multi_gpu": True,
                "cpu_offload_capable": True,
                "gpu_importance": "accelerated",
                "min_ram_gb": 64,
                "recommended_ram_gb": 128,
                "min_storage_gb": 50,
                "gpu_backends": ["cuda", "rocm", "metal"],
                "assumptions": {"context_tokens": 8192, "batch_size": 1},
                "notes": "Q4 ~40GB: 2x24GB consumer cards, or partial CPU offload "
                "with 128GB RAM at reduced speed.",
            },
            {
                "task": "inference",
                "precision": "fp16",
                "min_vram_gb": 160,
                "recommended_vram_gb": 160,
                "supports_multi_gpu": True,
                "min_ram_gb": 128,
                "recommended_ram_gb": 256,
                "min_storage_gb": 150,
                "gpu_backends": ["cuda", "rocm"],
                "assumptions": {"context_tokens": 8192, "batch_size": 1},
            },
            {
                "task": "fine_tune_qlora",
                "precision": "q4",
                "min_vram_gb": 48,
                "recommended_vram_gb": 80,
                "supports_multi_gpu": True,
                "min_ram_gb": 128,
                "recommended_ram_gb": 256,
                "min_storage_gb": 200,
                "gpu_backends": ["cuda"],
                "assumptions": {
                    "batch_size": 1,
                    "seq_len": 2048,
                    "gradient_checkpointing": True,
                },
            },
        ],
    },
    {
        "model": {
            "name": "Qwen2.5-VL 7B",
            "slug": "qwen2-5-vl-7b",
            "family": "multimodal",
            "params_billions": 8.3,
            "context_length": 32768,
            "developer": "Alibaba",
            "license": "apache-2.0",
            "huggingface_id": "Qwen/Qwen2.5-VL-7B-Instruct",
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "q4",
                "min_vram_gb": 8,
                "recommended_vram_gb": 12,
                "cpu_offload_capable": True,
                "gpu_importance": "accelerated",
                "min_ram_gb": 16,
                "recommended_ram_gb": 32,
                "min_storage_gb": 12,
                "gpu_backends": ["cuda", "rocm", "metal"],
                "assumptions": {"context_tokens": 8192, "batch_size": 1},
                "notes": "Vision tower adds ~1-2GB over the text-only equivalent.",
            },
        ],
    },
    {
        "model": {
            "name": "Stable Diffusion XL",
            "slug": "sdxl",
            "family": "image_gen",
            "params_billions": 3.5,
            "developer": "Stability AI",
            "license": "openrail++",
            "huggingface_id": "stabilityai/stable-diffusion-xl-base-1.0",
            "spec": {"base_resolution": 1024},
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "fp16",
                "min_vram_gb": 8,
                "recommended_vram_gb": 12,
                "min_ram_gb": 16,
                "recommended_ram_gb": 32,
                "min_storage_gb": 15,
                "gpu_backends": ["cuda", "rocm", "metal"],
                "assumptions": {"resolution": 1024, "batch_size": 1},
            },
            {
                "task": "fine_tune_lora",
                "precision": "fp16",
                "min_vram_gb": 12,
                "recommended_vram_gb": 16,
                "min_ram_gb": 32,
                "recommended_ram_gb": 64,
                "min_storage_gb": 40,
                "gpu_backends": ["cuda"],
                "assumptions": {
                    "resolution": 1024,
                    "batch_size": 1,
                    "gradient_checkpointing": True,
                },
            },
            {
                "task": "fine_tune_full",
                "precision": "fp16",
                "min_vram_gb": 24,
                "recommended_vram_gb": 32,
                "min_ram_gb": 64,
                "recommended_ram_gb": 128,
                "min_storage_gb": 100,
                "gpu_backends": ["cuda"],
                "assumptions": {
                    "resolution": 1024,
                    "batch_size": 1,
                    "gradient_checkpointing": True,
                },
            },
        ],
    },
    {
        "model": {
            "name": "FLUX.1-dev",
            "slug": "flux-1-dev",
            "family": "image_gen",
            "params_billions": 12.0,
            "developer": "Black Forest Labs",
            "license": "flux-1-dev-non-commercial",
            "huggingface_id": "black-forest-labs/FLUX.1-dev",
            "spec": {"base_resolution": 1024},
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "bf16",
                "min_vram_gb": 24,
                "recommended_vram_gb": 32,
                "min_ram_gb": 32,
                "recommended_ram_gb": 64,
                "min_storage_gb": 35,
                "gpu_backends": ["cuda"],
                "required_gpu_features": ["bf16"],
                "assumptions": {"resolution": 1024, "batch_size": 1},
            },
            {
                "task": "inference",
                "precision": "fp8",
                "min_vram_gb": 12,
                "recommended_vram_gb": 16,
                "min_ram_gb": 32,
                "recommended_ram_gb": 64,
                "min_storage_gb": 20,
                "gpu_backends": ["cuda"],
                "required_gpu_features": ["fp8"],
                "assumptions": {"resolution": 1024, "batch_size": 1},
                "notes": "fp8 weights halve VRAM with minor quality cost; needs Ada or newer.",
            },
            {
                "task": "fine_tune_lora",
                "precision": "bf16",
                "min_vram_gb": 24,
                "recommended_vram_gb": 48,
                "min_ram_gb": 64,
                "recommended_ram_gb": 128,
                "min_storage_gb": 80,
                "gpu_backends": ["cuda"],
                "required_gpu_features": ["bf16"],
                "assumptions": {
                    "resolution": 1024,
                    "batch_size": 1,
                    "gradient_checkpointing": True,
                },
            },
        ],
    },
    {
        "model": {
            "name": "Wan 2.1 14B",
            "slug": "wan-2-1-14b",
            "family": "video_gen",
            "params_billions": 14.0,
            "developer": "Alibaba",
            "license": "apache-2.0",
            "huggingface_id": "Wan-AI/Wan2.1-T2V-14B",
            "spec": {"base_resolution": 720, "max_frames": 81},
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "fp8",
                "min_vram_gb": 16,
                "recommended_vram_gb": 24,
                "min_ram_gb": 64,
                "recommended_ram_gb": 128,
                "min_storage_gb": 60,
                "gpu_backends": ["cuda"],
                "required_gpu_features": ["fp8"],
                "assumptions": {"resolution": 720, "frames": 81, "batch_size": 1},
                "notes": "Video diffusion is VRAM- and patience-bound; 24GB strongly advised.",
            },
        ],
    },
    {
        "model": {
            "name": "Whisper large-v3",
            "slug": "whisper-large-v3",
            "family": "speech",
            "params_billions": 1.55,
            "developer": "OpenAI",
            "license": "apache-2.0",
            "huggingface_id": "openai/whisper-large-v3",
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "fp16",
                "min_vram_gb": 6,
                "recommended_vram_gb": 10,
                "cpu_offload_capable": True,
                "gpu_importance": "accelerated",
                "min_ram_gb": 8,
                "recommended_ram_gb": 16,
                "min_storage_gb": 5,
                "gpu_backends": ["cuda", "rocm", "metal"],
                "assumptions": {"batch_size": 1},
                "notes": "whisper.cpp runs CPU-only well below realtime; GPU for realtime/batch.",
            },
        ],
    },
    {
        "model": {
            "name": "YOLO11",
            "slug": "yolo11",
            "family": "vision",
            "params_billions": 0.057,
            "developer": "Ultralytics",
            "license": "agpl-3.0",
            "huggingface_id": "Ultralytics/YOLO11",
            "spec": {"input_size": 640},
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "fp16",
                "min_vram_gb": 4,
                "recommended_vram_gb": 8,
                "cpu_offload_capable": True,
                "gpu_importance": "accelerated",
                "min_ram_gb": 8,
                "recommended_ram_gb": 16,
                "min_storage_gb": 5,
                "gpu_backends": ["cuda", "rocm", "metal"],
                "assumptions": {"input_size": 640, "batch_size": 1},
                "notes": "CPU inference works for low frame rates; GPU for realtime video.",
            },
            {
                "task": "fine_tune_full",
                "precision": "fp16",
                "min_vram_gb": 8,
                "recommended_vram_gb": 16,
                "min_ram_gb": 16,
                "recommended_ram_gb": 32,
                "min_storage_gb": 60,
                "gpu_backends": ["cuda"],
                "assumptions": {"input_size": 640, "batch_size": 16},
                "notes": "Custom-dataset training; storage floor is mostly the dataset.",
            },
        ],
    },
    {
        "model": {
            "name": "MusicGen Medium",
            "slug": "musicgen-medium",
            "family": "audio_gen",
            "params_billions": 1.5,
            "developer": "Meta",
            "license": "cc-by-nc-4.0",
            "huggingface_id": "facebook/musicgen-medium",
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "fp16",
                "min_vram_gb": 8,
                "recommended_vram_gb": 16,
                "min_ram_gb": 16,
                "recommended_ram_gb": 32,
                "min_storage_gb": 10,
                "gpu_backends": ["cuda"],
                "assumptions": {"clip_seconds": 30, "batch_size": 1},
                "notes": "Generation is autoregressive over audio tokens — far "
                "heavier than ASR at similar parameter counts.",
            },
        ],
    },
    {
        "model": {
            "name": "XGBoost",
            "slug": "xgboost",
            "family": "classical",
            "developer": "DMLC",
            "license": "apache-2.0",
            "website_url": "https://xgboost.readthedocs.io",
            "notes": "Catalog entry for gradient-boosted trees on tabular data; "
            "stands in for the sklearn/LightGBM class of workloads.",
        },
        "workloads": [
            {
                "task": "train_scratch",
                "precision": "fp32",
                "min_vram_gb": None,
                "recommended_vram_gb": 8,
                "cpu_offload_capable": True,
                "gpu_importance": "optional",
                "min_ram_gb": 32,
                "recommended_ram_gb": 64,
                "min_storage_gb": 50,
                "gpu_backends": ["cuda"],
                "assumptions": {"dataset_rows": "10M-scale tabular"},
                "notes": "CPU cores + RAM are the whole build; gpu_hist helps only "
                "on very large datasets. The inverted profile vs deep learning.",
            },
        ],
    },
    {
        "model": {
            "name": "PPO (Gymnasium)",
            "slug": "ppo-gymnasium",
            "family": "rl",
            "developer": "—",
            "license": "mit",
            "website_url": "https://gymnasium.farama.org",
            "notes": "Stands in for on-policy RL training loops (Stable-Baselines3, "
            "CleanRL) on standard simulated environments.",
        },
        "workloads": [
            {
                "task": "train_scratch",
                "precision": "fp32",
                "min_vram_gb": 4,
                "recommended_vram_gb": 8,
                "gpu_importance": "accelerated",
                "min_ram_gb": 32,
                "recommended_ram_gb": 64,
                "min_storage_gb": 20,
                "gpu_backends": ["cuda"],
                "assumptions": {"vectorized_envs": 16},
                "notes": "Throughput scales with CPU cores running vectorized "
                "environments; the policy network itself is tiny.",
            },
        ],
    },
    {
        "model": {
            "name": "BGE-M3",
            "slug": "bge-m3",
            "family": "embedding",
            "params_billions": 0.57,
            "context_length": 8192,
            "developer": "BAAI",
            "license": "mit",
            "huggingface_id": "BAAI/bge-m3",
        },
        "workloads": [
            {
                "task": "inference",
                "precision": "fp16",
                "min_vram_gb": None,
                "recommended_vram_gb": 4,
                "cpu_offload_capable": True,
                "gpu_importance": "optional",
                "min_ram_gb": 8,
                "recommended_ram_gb": 16,
                "min_storage_gb": 3,
                "gpu_backends": ["cuda", "rocm", "metal"],
                "assumptions": {"batch_size": 32},
                "notes": "Fine on CPU for small corpora; GPU only matters for bulk indexing.",
            },
        ],
    },
]


def _upsert_model(db, fields: dict) -> AIModel:
    model = db.query(AIModel).filter_by(slug=fields["slug"]).first()
    if model is None:
        model = AIModel(**fields)
        db.add(model)
    else:
        for key, value in fields.items():
            setattr(model, key, value)
    db.flush()
    return model


def _upsert_workload(db, model: AIModel, sort_order: int, fields: dict) -> None:
    workload = (
        db.query(AIWorkload)
        .filter_by(
            model_id=model.id, task=fields["task"], precision=fields["precision"]
        )
        .first()
    )
    if workload is None:
        workload = AIWorkload(model_id=model.id, **fields)
        db.add(workload)
    else:
        for key, value in fields.items():
            setattr(workload, key, value)
    workload.sort_order = sort_order


def seed_ai_catalog() -> None:
    db = SessionLocal()
    try:
        for entry in _CATALOG:
            model = _upsert_model(db, entry["model"])
            for i, workload_fields in enumerate(entry["workloads"]):
                _upsert_workload(db, model, i, workload_fields)
        db.commit()
        print(
            f"Seeded {len(_CATALOG)} AI models "
            f"({sum(len(e['workloads']) for e in _CATALOG)} workload rows)."
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_ai_catalog()
