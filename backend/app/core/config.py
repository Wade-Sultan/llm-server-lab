from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    HttpUrl,
    PostgresDsn,
    computed_field,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="../.env",
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    FRONTEND_HOST: str = "http://localhost:5173"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST
        ]

    PROJECT_NAME: str
    SENTRY_DSN: HttpUrl | None = None
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    CLOUD_SQL_INSTANCE: str | None = None

    POSTGRES_DB_URL: PostgresDsn | None = None

    @computed_field
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        if not self.POSTGRES_DB_URL:
            # Production uses CLOUD_SQL_INSTANCE via the connector library,
            # so SQLALCHEMY_DATABASE_URI is only needed for local dev
            return ""
        return str(self.POSTGRES_DB_URL).replace(
            "postgresql://", "postgresql+psycopg://"
        )

    OPENROUTER_API_KEY: str

    # Hugging Face Hub token for the AI-model discovery job. Public model
    # listing works without it, but a token raises rate limits and returns
    # metadata for gated models. Create at https://huggingface.co/settings/tokens
    HF_TOKEN: str | None = None

    # Tavily search API key for parts-discovery enrichment (finding spec pages
    # for novel SKUs). Optional at boot; the discovery job fails fast with a
    # clear error if it runs without one. https://app.tavily.com
    TAVILY_API_KEY: str | None = None

    # Shared secret for the admin-triggered discovery endpoints (X-Admin-Key
    # header). The admin panel's server actions hold this key server-side.
    # Unset = discovery endpoints return 503.
    DISCOVERY_API_KEY: str | None = None

    SERPAPI_KEY: str


settings = Settings()  # type: ignore

