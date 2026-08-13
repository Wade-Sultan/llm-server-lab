"""
Unit tests for app.services.embeddings.text.

The property that matters most here is what is NOT in the source text. Every
builder feeds a SHA-256 that decides whether a row gets re-embedded, so any
volatile field leaking into one turns the nightly pricing ETL into a recurring
embedding bill — every part re-embedded every night, for vectors that come back
semantically identical.

The rest of these check that nulls are dropped rather than rendered as the
string "None", which would otherwise become a token the model has to interpret
and a source of spurious similarity between two rows that are merely both
incomplete.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.models.embeddings import EmbeddedEntity
from app.services.embeddings import text as t


def _cpu(**overrides):
    base = dict(
        name="AMD Ryzen 7 9800X3D",
        brand="amd",
        socket="AM5",
        cores=8,
        threads=16,
        ddr_generation=["ddr5"],
        has_igpu=True,
        supports_ecc=False,
        # Volatile fields a builder must never read.
        street_price_cents=47900,
        last_price_checked_at="2026-08-10",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _chipset(**overrides):
    base = dict(
        name="RTX 5080",
        vram_gb=16,
        vram_type="GDDR7",
        tdp_watts=360,
        has_ray_tracing=True,
        supported_features=["dlss4", "fp8"],
        street_price_cents=119900,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _game(**overrides):
    base = dict(
        title="Cyberpunk 2077",
        genre="aaa_open_world",
        hard_requirements=["ray tracing"],
        requirements_notes="SSD strongly recommended.",
        min_storage_gb=70,
        aliases=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# The price-exclusion invariant
# ---------------------------------------------------------------------------


def test_cpu_text_excludes_price():
    out = t.cpu_text(_cpu())
    assert "479" not in out
    assert "price" not in out.lower()


def test_gpu_chipset_text_excludes_price():
    out = t.gpu_chipset_text(_chipset())
    assert "1199" not in out
    assert "price" not in out.lower()


def test_price_change_does_not_change_the_hash():
    """The whole point: a pricing ETL run must dirty zero hashes."""
    cheap = t.content_hash(t.cpu_text(_cpu(street_price_cents=19900)))
    dear = t.content_hash(t.cpu_text(_cpu(street_price_cents=99900)))
    assert cheap == dear


def test_spec_change_does_change_the_hash():
    """The converse — a real edit must trigger a re-embed."""
    before = t.content_hash(t.cpu_text(_cpu(cores=8)))
    after = t.content_hash(t.cpu_text(_cpu(cores=16)))
    assert before != after


def test_hash_is_stable_across_calls():
    assert t.content_hash(t.cpu_text(_cpu())) == t.content_hash(t.cpu_text(_cpu()))


# ---------------------------------------------------------------------------
# Null handling and content
# ---------------------------------------------------------------------------


def test_nulls_are_dropped_not_rendered():
    out = t.cpu_text(_cpu(brand=None, ddr_generation=None, has_igpu=False))
    assert "None" not in out
    assert out.strip()


def test_empty_lists_are_dropped():
    out = t.gpu_chipset_text(_chipset(supported_features=[]))
    assert "Features" not in out


def test_cpu_text_reads_as_natural_language():
    """Matched against user prose, so it must be prose — not key=value pairs."""
    out = t.cpu_text(_cpu())
    assert "AMD Ryzen 7 9800X3D" in out
    assert "8 cores and 16 threads" in out
    assert "=" not in out
    assert "|" not in out


def test_game_text_includes_title_genre_and_notes():
    out = t.game_text(_game())
    assert "Cyberpunk 2077" in out
    # Underscores are expanded — "aaa_open_world" is not how anyone speaks.
    assert "aaa open world" in out
    assert "_" not in out.split(".")[1]


def test_ai_model_text_includes_the_hub_id():
    """The Hub id is how users most often name a model they actually run."""
    model = SimpleNamespace(
        name="Llama 3.1 70B",
        family="llm",
        params_billions=70.0,
        developer="Meta",
        huggingface_id="meta-llama/Llama-3.1-70B-Instruct",
        notes=None,
        aliases=[],
    )
    out = t.ai_model_text(model)
    assert "meta-llama/Llama-3.1-70B-Instruct" in out
    assert "70B parameter model" in out


def test_case_text_includes_colour_and_glass():
    """Both are attributes users name unprompted when describing a build."""
    case = SimpleNamespace(
        name="Fractal North",
        size="mid_tower",
        supported_mobo_form_factors=["atx", "matx"],
        max_gpu_length_mm=355,
        color="white",
        has_glass_panel=True,
    )
    out = t.case_text(case)
    assert "white" in out
    assert "glass" in out.lower()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_every_entity_type_has_a_builder():
    """A type without a builder embeds as empty string and is silently skipped
    by the reconcile sweep — so the mapping must stay exhaustive."""
    missing = [e for e in EmbeddedEntity if e not in t.BUILDERS]
    assert missing == []


def test_build_text_dispatches_by_entity_type():
    assert t.build_text(EmbeddedEntity.CPU, _cpu()).startswith("AMD Ryzen")
    assert t.build_text(EmbeddedEntity.GAME, _game()).startswith("Cyberpunk")


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------


def test_game_text_includes_aliases_right_after_the_title():
    """Position matters: a two-character query has to compete with the rest of
    the prose, so the alias sits adjacent to the canonical name."""
    out = t.game_text(_game(title="Rainbow Six Siege", aliases=["R6", "Siege"]))
    assert "R6" in out
    assert "Siege" in out
    # Title first, aliases immediately after, before the genre clause.
    assert out.index("Rainbow Six Siege") < out.index("R6") < out.index("video game")


def test_empty_aliases_leave_the_text_unchanged():
    """Rows with no aliases must hash identically to before the column existed,
    so the sweep skips them instead of re-embedding the whole catalog."""
    assert "Also known as" not in t.game_text(_game(aliases=[]))


def test_adding_an_alias_changes_the_hash():
    """That is what makes the reconcile sweep pick the row up."""
    before = t.content_hash(t.game_text(_game(aliases=[])))
    after = t.content_hash(t.game_text(_game(aliases=["CP77"])))
    assert before != after
