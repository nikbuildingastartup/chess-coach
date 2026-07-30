from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_secret: str
    cors_origins: list[str]
    database_url: str = "sqlite:///./chess_coach.db"
    stockfish_path: str = "stockfish"


settings = Settings()
