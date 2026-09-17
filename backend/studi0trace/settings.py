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

    @classmethod
    def from_env(cls) -> "Settings":
        origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]
        return cls(
            allowed_origins=origins or [DEFAULT_ORIGINS],
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", cls.model_fields["max_upload_bytes"].default)),
            max_image_pixels=int(os.getenv("MAX_IMAGE_PIXELS", cls.model_fields["max_image_pixels"].default)),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
