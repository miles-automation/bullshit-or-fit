from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    spark_swarm_api_url: str = "https://swarm.sparkswarm.com/api/v1"
    spark_slug: str = "bullshit-or-fit"
    environment: str = "dev"
    cors_origins: str = "https://bullshitorfit.com,https://www.bullshitorfit.com"


settings = Settings()
