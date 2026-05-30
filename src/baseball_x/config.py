from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    notion_token: str
    notion_database_id: str
    x_api_key: str
    x_api_secret: str
    x_access_token: str
    x_access_secret: str
    anthropic_api_key: str

    model_config = {"env_file": ".env"}


settings = Settings()
