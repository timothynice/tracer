"""Runtime configuration, read once from the environment."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel, Field

DEFAULT_ORIGINS = "http://localhost:5173"


class Settings(BaseModel):
    allowed_origins: list[str] = Field(default_factory=lambda: [DEFAULT_ORIGINS])
    max_upload_bytes: int = 20 * 1024 * 1024
    max_image_pixels: int = 40_000_000
    max_upload_cache_bytes: int = 256 * 1024 * 1024
    upload_ttl_seconds: int = 30 * 60

    @classmethod
    def from_env(cls) -> "Settings":
        origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]

        def env_int(name: str, field: str) -> int:
            return int(os.getenv(name, cls.model_fields[field].default))

        return cls(
            allowed_origins=origins or [DEFAULT_ORIGINS],
            max_upload_bytes=env_int("MAX_UPLOAD_BYTES", "max_upload_bytes"),
            max_image_pixels=env_int("MAX_IMAGE_PIXELS", "max_image_pixels"),
            max_upload_cache_bytes=env_int("MAX_UPLOAD_CACHE_BYTES", "max_upload_cache_bytes"),
            upload_ttl_seconds=env_int("UPLOAD_TTL_SECONDS", "upload_ttl_seconds"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
