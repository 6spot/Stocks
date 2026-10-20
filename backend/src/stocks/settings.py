from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    s3_endpoint_url: str | None = None
    s3_bucket: str = "stocks"
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    parse_chunk_size: int = Field(default=1200, ge=200, le=4000)
    parse_chunk_overlap: int = Field(default=150, ge=0, le=1000)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    embedding_max_query_characters: int = Field(default=4000, ge=1, le=20000)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def model_post_init(self, __context: object, /) -> None:
        if self.parse_chunk_overlap >= self.parse_chunk_size:
            raise ValueError("PARSE_CHUNK_OVERLAP must be smaller than PARSE_CHUNK_SIZE")


@lru_cache
def settings() -> Settings:
    return Settings()
