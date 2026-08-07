from __future__ import annotations

import os


class ChatModelConfig:
    # Gemma 3 4B for extraction — fast, supports json_object response_format, no Gemma 4 small on OpenRouter
    EXTRACT_MODEL: str = os.getenv("CHAT_EXTRACT_MODEL", "google/gemma-3-4b-it")
    # Keep the larger model for the recommendation (needs nuance and personality)
    RECOMMEND_MODEL: str = os.getenv("CHAT_RECOMMEND_MODEL", "google/gemma-4-31b-it")
    ELICIT_MODEL: str = os.getenv("CHAT_ELICIT_MODEL", "google/gemma-4-31b-it")
    # Question ordering only — the router returns a single integer index into a
    # list the caller already computed, so this is the smallest model in the
    # stack on purpose. It runs on every elicitation turn and it cannot decide
    # anything except which of several known-missing items to raise first.
    ROUTE_MODEL: str = os.getenv("CHAT_ROUTE_MODEL", "google/gemma-3-4b-it")
    # MiniMax M3 for parts-discovery spec extraction — multimodal (rasterized
    # PDF spec sheets) and cheap enough for 2-3 extraction calls per SKU.
    DISCOVERY_EXTRACT_MODEL: str = os.getenv(
        "DISCOVERY_EXTRACT_MODEL", "minimax/minimax-m3"
    )

    @classmethod
    def get_extract_model(cls) -> str:
        return cls.EXTRACT_MODEL

    @classmethod
    def get_recommend_model(cls) -> str:
        return cls.RECOMMEND_MODEL

    @classmethod
    def get_elicit_model(cls) -> str:
        return cls.ELICIT_MODEL

    @classmethod
    def get_route_model(cls) -> str:
        return cls.ROUTE_MODEL

    @classmethod
    def get_discovery_extract_model(cls) -> str:
        return cls.DISCOVERY_EXTRACT_MODEL
