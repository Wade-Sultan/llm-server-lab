from __future__ import annotations

import os


class ChatModelConfig:
    EXTRACT_MODEL: str = os.getenv("CHAT_EXTRACT_MODEL", "claude-haiku-4-5-20251001")
    RECOMMEND_MODEL: str = os.getenv("CHAT_RECOMMEND_MODEL", "claude-haiku-4-5-20251001")
    ELICIT_MODEL: str = os.getenv("CHAT_ELICIT_MODEL", "claude-haiku-4-5-20251001")

    @classmethod
    def get_extract_model(cls) -> str:
        return cls.EXTRACT_MODEL

    @classmethod
    def get_recommend_model(cls) -> str:
        return cls.RECOMMEND_MODEL

    @classmethod
    def get_elicit_model(cls) -> str:
        return cls.ELICIT_MODEL
