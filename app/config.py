from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    gemini_api_key: str = ""
    groq_api_key: str = ""

    answering_provider: str = "gemini"
    answering_model: str = "gemini-3.5-flash-lite"
    embedding_model: str = "gemini-embedding-001"


settings = Settings()