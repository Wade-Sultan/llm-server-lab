"""
text.py
=======
Builds the canonical source text for each embeddable entity, and hashes it.

THE GUIDING RULE: write the text the way a user would describe the thing, not
the way the database stores it. The query side of this system is unedited human
prose — "I play Arc Raiders and Tarkov", "I edit 4K footage in Resolve", "I want
to run Llama 70B locally". A vector built from `slug=arc-raiders|genre=fps` sits
in a different region of the embedding space than that prose does, and the match
quality collapses. So these builders emit short natural-language descriptions
with the words a person would actually use.

WHAT IS DELIBERATELY LEFT OUT: price, stock, and anything else that changes
without the entity changing. Including price would mean every pricing ETL run
dirties every hash and triggers a full re-embed, turning a nightly job into a
recurring bill for vectors that would come back nearly identical. Semantics
only; the numeric filters already live in SQL where they belong.

Changing any builder here changes the hashes it produces, which is exactly how a
re-embed is triggered — the reconcile sweep will pick up every affected row on
its next pass. That is intended, but it is not free, so treat edits to these
functions as a migration of the vector set rather than a cosmetic change.
"""

from __future__ import annotations

import hashlib

from app.models.embeddings import EmbeddedEntity


def content_hash(text: str) -> str:
    """SHA-256 of the source text — the staleness key for the whole system."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _join(*parts: object) -> str:
    """Join non-empty fragments into one sentence-ish line.

    Nulls are dropped rather than rendered as "None", which would otherwise
    become a token the model has to interpret and a source of spurious
    similarity between two rows that are merely both incomplete.
    """
    return ". ".join(str(p).strip() for p in parts if p is not None and str(p).strip())


def _list(values: list[str] | None) -> str:
    return ", ".join(v.strip() for v in (values or []) if v and v.strip())


# --- Catalog — the query side -------------------------------------------------


def game_text(game) -> str:
    """e.g. 'Cyberpunk 2077. Also known as CP77. aaa open world video game. …'

    Aliases come second, right after the title: they are the words a user is
    most likely to type, and putting them adjacent to the canonical name is what
    pulls a short query like "R6" toward this row rather than leaving it to
    compete with fifteen words of genre and requirements prose.
    """
    genre = (game.genre or "").replace("_", " ")
    return _join(
        game.title,
        f"Also known as {_list(game.aliases)}" if game.aliases else None,
        f"{genre} video game" if genre else "video game",
        f"Requires {_list(game.hard_requirements)}" if game.hard_requirements else None,
        game.requirements_notes,
    )


def software_text(software) -> str:
    category = (software.category or "").replace("_", " ")
    tags = _list(software.use_case_tags)
    return _join(
        software.name,
        f"Also known as {_list(software.aliases)}" if software.aliases else None,
        f"{category} software" if category else "software",
        f"Used for {tags}" if tags else None,
        f"Developed by {software.developer}" if software.developer else None,
        software.notes,
    )


def ai_model_text(model) -> str:
    family = (model.family or "").replace("_", " ")
    size = (
        f"{model.params_billions:g}B parameter model" if model.params_billions else None
    )
    return _join(
        model.name,
        f"Also known as {_list(model.aliases)}" if model.aliases else None,
        f"{family} AI model" if family else "AI model",
        size,
        f"Released by {model.developer}" if model.developer else None,
        # The Hub id is how a user is most likely to name a model they actually
        # run ("meta-llama/Llama-3.1-70B"), so it belongs in the vector.
        model.huggingface_id,
        model.notes,
    )


# --- Parts --------------------------------------------------------------------


def cpu_text(cpu) -> str:
    return _join(
        cpu.name,
        f"{cpu.brand} desktop processor" if cpu.brand else "desktop processor",
        f"{cpu.cores} cores and {cpu.threads} threads",
        f"{cpu.socket} socket",
        f"Supports {_list(cpu.ddr_generation)}" if cpu.ddr_generation else None,
        "Includes integrated graphics" if cpu.has_igpu else None,
        "Supports ECC memory" if cpu.supports_ecc else None,
    )


def gpu_chipset_text(chipset) -> str:
    return _join(
        chipset.name,
        "graphics card",
        f"{chipset.vram_gb}GB {chipset.vram_type or ''} video memory".strip(),
        "Supports hardware ray tracing" if chipset.has_ray_tracing else None,
        f"{chipset.tdp_watts}W power draw" if chipset.tdp_watts else None,
        f"Features {_list(chipset.supported_features)}"
        if chipset.supported_features
        else None,
    )


def motherboard_text(board) -> str:
    return _join(
        board.name,
        f"{board.form_factor or ''} motherboard".strip(),
        f"{board.socket} socket" if board.socket else None,
        f"{board.chipset} chipset" if board.chipset else None,
        f"Supports {board.ddr_generation}" if board.ddr_generation else None,
        "Built-in WiFi" if board.has_wifi else None,
        "Supports ECC memory" if board.supports_ecc else None,
        "Has IPMI remote management" if board.has_ipmi else None,
    )


def cooler_text(cooler) -> str:
    return _join(
        cooler.name,
        f"{cooler.cooler_type or ''} CPU cooler".strip(),
        f"Rated for {cooler.max_tdp_watts}W" if cooler.max_tdp_watts else None,
        f"{cooler.noise_dba} dBA noise level" if cooler.noise_dba else None,
    )


def case_text(case) -> str:
    return _join(
        case.name,
        f"{case.size or ''} PC case".strip(),
        f"Fits {_list(case.supported_mobo_form_factors)} motherboards"
        if case.supported_mobo_form_factors
        else None,
        f"Supports GPUs up to {case.max_gpu_length_mm}mm"
        if case.max_gpu_length_mm
        else None,
        # Colour and glass are the two case attributes users name unprompted
        # ("a white case with a glass side"), so they carry real query weight
        # here despite being cosmetic everywhere else in the schema.
        f"{case.color} colour" if case.color else None,
        "Tempered glass side panel" if case.has_glass_panel else None,
    )


def fan_text(fan) -> str:
    return _join(
        fan.name,
        f"{fan.size_mm}mm case fan" if fan.size_mm else "case fan",
        f"{fan.airflow_cfm} CFM airflow" if fan.airflow_cfm else None,
        f"{fan.noise_dba} dBA noise level" if fan.noise_dba else None,
    )


def ram_group_text(group) -> str:
    return _join(
        group.name,
        f"{group.ddr_generation or ''} memory kit".strip(),
        f"{group.capacity_gb}GB total across {group.modules} modules"
        if group.capacity_gb
        else None,
        f"{group.speed_mhz} MHz" if group.speed_mhz else None,
        f"CL{group.cas_latency}" if group.cas_latency else None,
        "ECC memory" if group.is_ecc else None,
        group.module_type,
    )


def psu_group_text(group) -> str:
    return _join(
        group.name,
        "power supply unit",
        f"{group.wattage}W" if group.wattage else None,
        f"{group.efficiency_rating} efficiency" if group.efficiency_rating else None,
        f"{group.form_factor} form factor" if group.form_factor else None,
        f"{group.modular} cabling" if group.modular else None,
    )


def storage_group_text(group) -> str:
    return _join(
        group.name,
        f"{group.storage_type or ''} storage drive".strip(),
        f"{group.capacity_gb}GB capacity" if group.capacity_gb else None,
        f"{group.interface} interface" if group.interface else None,
        f"{group.read_speed_mbps} MB/s sequential read"
        if group.read_speed_mbps
        else None,
    )


# Entity type -> builder. Keeping this a single dispatch table means the
# reconcile sweep can walk every embeddable type generically instead of
# carrying a branch per type.
BUILDERS = {
    EmbeddedEntity.GAME: game_text,
    EmbeddedEntity.SOFTWARE: software_text,
    EmbeddedEntity.AI_MODEL: ai_model_text,
    EmbeddedEntity.CPU: cpu_text,
    EmbeddedEntity.GPU_CHIPSET: gpu_chipset_text,
    EmbeddedEntity.MOTHERBOARD: motherboard_text,
    EmbeddedEntity.CPU_COOLER: cooler_text,
    EmbeddedEntity.CASE: case_text,
    EmbeddedEntity.FAN: fan_text,
    EmbeddedEntity.RAM_GROUP: ram_group_text,
    EmbeddedEntity.PSU_GROUP: psu_group_text,
    EmbeddedEntity.STORAGE_GROUP: storage_group_text,
}


def build_text(entity_type: EmbeddedEntity, entity) -> str:
    """Source text for one entity. Empty string if the type has no builder."""
    builder = BUILDERS.get(entity_type)
    return builder(entity) if builder else ""
