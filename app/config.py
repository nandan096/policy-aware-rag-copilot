from pydantic_settings import BaseSettings, SettingsConfigDict
    
class Settings(BaseSettings):
    # 1. Tell Pydantic to read values from our .env file
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 2. Required secret keys (app won't start if DATABASE_URL is missing)
    database_url: str
    gemini_api_key: str = ""
    groq_api_key: str = ""

    # 3. Model settings (can be overridden in .env anytime)
    answering_provider: str = "gemini"
    answering_model: str = "gemini-3.5-flash-lite"

    judge_provider: str = "groq"
    judge_model: str = "llama-3.3-70b-versatile"

    embedding_model: str = "gemini-embedding-001"


# 4. Create one shared settings object for the whole app to import
settings = Settings()