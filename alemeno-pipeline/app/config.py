from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://txn_user:txn_pass@localhost:5432/txn_pipeline"
    redis_url: str = "redis://localhost:6379/0"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    upload_dir: str = "/code/uploads"

    class Config:
        env_file = ".env"
        # Allows DATABASE_URL / REDIS_URL (set directly by docker-compose) to map onto
        # database_url / redis_url without needing a second alias for each field.
        case_sensitive = False


settings = Settings()
