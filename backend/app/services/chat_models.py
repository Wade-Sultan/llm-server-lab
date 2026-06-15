from __future__ import annotations

import os


class ChatModelConfig:
    # Gemma 3 4B for extraction — fast, supports json_object response_format, no Gemma 4 small on OpenRouter
    EXTRACT_MODEL: str = os.getenv("CHAT_EXTRACT_MODEL", "google/gemma-3-4b-it")
    # Keep the larger model for the recommendation (needs nuance and personality)
    RECOMMEND_MODEL: str = os.getenv("CHAT_RECOMMEND_MODEL", "google/gemma-4-26b-a4b-it")
    ELICIT_MODEL: str = os.getenv("CHAT_ELICIT_MODEL", "google/gemma-4-26b-a4b-it")

    @classmethod
    def get_extract_model(cls) -> str:
        return cls.EXTRACT_MODEL

    @classmethod
    def get_recommend_model(cls) -> str:
        return cls.RECOMMEND_MODEL

    @classmethod
    def get_elicit_model(cls) -> str:
        return cls.ELICIT_MODEL
