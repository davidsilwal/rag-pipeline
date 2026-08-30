#!/usr/bin/env python3
"""apps/control_api/config.py — Pydantic Settings & environment validation.

Central place for all environment variables used by the Control API
and the GPU worker pipeline. All secrets should be loaded from platform
secret stores (Colab userdata / Deepnote env vars), never committed
to .env or Git.
"""

from __future__ import annotations

from functools import lru_cache
import warnings
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- VPS Control Plane ---
    database_url: str = Field(..., env="DATABASE_URL")
    redis_url: str = Field("redis://redis:6379/0", env="REDIS_URL")
    api_token: str = Field(..., env="API_TOKEN")
    wiki_repo_path: str = Field("/var/data/wiki", env="WIKI_REPO_PATH")
    ingest_root: str = Field("/var/data/ingest", env="INGEST_ROOT")
    ensure_tables: bool = Field(False, env="ENSURE_TABLES")

    # LiteLLM / Local LLM
    litellm_proxy_url: str | None = Field(None, env="LITELLM_PROXY_URL")
    local_llm_api_base: str = Field("http://127.0.0.1:8000/v1", env="LOCAL_LLM_API_BASE")
    default_llm_model: str = Field("Qwen/Qwen2.5-14B-Instruct-AWQ", env="DEFAULT_LLM_MODEL")

    # --- GPU Worker Pipeline (secrets from platform stores only) ---
    azure_tenant_id: str | None = Field(None, env="AZURE_TENANT_ID")
    azure_client_id: str | None = Field(None, env="AZURE_CLIENT_ID")
    azure_client_secret: str | None = Field(None, env="AZURE_CLIENT_SECRET")
    onedrive_drive_id: str | None = Field(None, env="ONEDRIVE_DRIVE_ID")
    onedrive_root_folder: str | None = Field(None, env="ONEDRIVE_ROOT_FOLDER")

    embedding_model_name: str = Field("BAAI/bge-m3", env="EMBEDDING_MODEL_NAME")

    # Ignore unrelated env vars (Colab/Deepnote hosts export many); only the
    # declared fields are read. This also keeps tests robust to host env.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def check_azure_secret_not_real(self) -> "Settings":
        if self.azure_client_secret and self.azure_client_secret.startswith("sec_") and len(self.azure_client_secret) > 20:
            warnings.warn(
                "azure_client_secret appears to contain a real secret. "
                "Load from platform secret store instead (Colab userdata / Deepnote env)."
            )
        return self


@lru_cache()
def get_settings() -> Settings:
    return Settings()


config = get_settings()