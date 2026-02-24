from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    port: int = 8000
    openrouter_api_key: str
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536
    default_db_path: str = "~/.memo/memo.db"

    @property
    def resolved_default_db_path(self) -> str:
        return str(Path(self.default_db_path).expanduser())


settings = Settings()
