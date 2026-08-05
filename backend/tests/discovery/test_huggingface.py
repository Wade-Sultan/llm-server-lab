"""
Unit tests for the Hugging Face Hub discovery source
(app.services.discovery.huggingface). Pure-logic: the network layer is not
exercised, only the mapping from a Hub API payload onto ai_models columns.

That mapping is the whole value of this path — it exists so the ai_model
category never pays an LLM to read a model card, so the fields it derives have
to be right without one.
"""

from __future__ import annotations

from app.services.discovery.huggingface import _to_hub_model


def _payload(**overrides) -> dict:
    base = {
        "id": "meta-llama/Llama-3.1-70B-Instruct",
        "author": "meta-llama",
        "pipeline_tag": "text-generation",
        "tags": ["text-generation", "license:llama3.1"],
        "cardData": {"license": "llama3.1"},
        "safetensors": {"total": 70_553_706_496},
        "config": {"max_position_embeddings": 131072},
    }
    return {**base, **overrides}


def test_maps_a_hub_payload_onto_ai_models_columns():
    model = _to_hub_model(_payload())
    assert model is not None
    assert model.hub_id == "meta-llama/Llama-3.1-70B-Instruct"
    assert model.fields["name"] == "Llama 3.1 70B Instruct"
    assert model.fields["slug"] == "meta-llama-llama-3-1-70b-instruct"
    assert model.fields["family"] == "llm"
    assert model.fields["developer"] == "meta-llama"
    assert model.fields["context_length"] == 131072
    assert model.fields["license"] == "llama3.1"


def test_parameter_count_comes_from_the_safetensors_index_not_the_name():
    """The name says '70B'; the index says 70.554B. The index is the only
    non-marketing parameter count available anywhere, so it wins."""
    model = _to_hub_model(_payload())
    assert model is not None
    assert model.fields["params_billions"] == 70.554


def test_parameter_count_falls_back_to_the_name_without_an_index():
    """GGUF-only and older .bin repos publish no safetensors index — the name
    is where the number came from originally, so it beats nothing."""
    model = _to_hub_model(_payload(safetensors=None, id="unsloth/gpt-oss-120b-GGUF"))
    assert model is not None
    assert model.fields["params_billions"] == 120.0


def test_license_falls_back_to_the_tag_list():
    model = _to_hub_model(_payload(cardData={}))
    assert model is not None
    assert model.fields["license"] == "llama3.1"


def test_absent_fields_are_omitted_rather_than_nulled():
    """Omitted keys let the approval form fall back to its own defaults; an
    explicit null would overwrite them."""
    # An id with no parameter count in it, so the name fallback has nothing to
    # find either — otherwise "…-70B-Instruct" would supply one.
    model = _to_hub_model(
        _payload(
            id="openai/whisper-large-v3",
            safetensors=None,
            config={},
            cardData={},
            tags=[],
        )
    )
    assert model is not None
    assert "params_billions" not in model.fields
    assert "context_length" not in model.fields
    assert "license" not in model.fields


def test_unmapped_pipeline_tag_is_skipped_not_guessed():
    """A model staged under the wrong family would be matched against the wrong
    workloads, so an unrecognised tag drops the model instead."""
    assert _to_hub_model(_payload(pipeline_tag="table-question-answering")) is None
    assert _to_hub_model(_payload(pipeline_tag=None)) is None


def test_repos_without_an_owner_are_skipped():
    assert _to_hub_model(_payload(id="gpt2")) is None


def test_provenance_covers_every_field_it_emits():
    model = _to_hub_model(_payload())
    assert model is not None
    assert set(model.provenance) == set(model.fields)
    for entry in model.provenance.values():
        assert entry["source_url"].endswith("/models/meta-llama/Llama-3.1-70B-Instruct")


def test_family_mapping_spans_the_modalities_the_catalog_models():
    cases = {
        "text-generation": "llm",
        "image-text-to-text": "multimodal",
        "text-to-image": "image_gen",
        "text-to-video": "video_gen",
        "automatic-speech-recognition": "speech",
        "text-to-audio": "audio_gen",
        "object-detection": "vision",
        "sentence-similarity": "embedding",
        "tabular-regression": "classical",
        "reinforcement-learning": "rl",
    }
    for tag, family in cases.items():
        model = _to_hub_model(_payload(pipeline_tag=tag))
        assert model is not None, tag
        assert model.fields["family"] == family, tag
