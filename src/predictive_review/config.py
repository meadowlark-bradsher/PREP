from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./predictive_review.db"
    anthropic_api_key: str = ""
    basic_auth_user: str = ""
    basic_auth_password: str = ""

    # Per-role model ids. The four LLM-bearing components are independent
    # and each gets its own model. v1 expects a tiered assignment: cheap
    # for selector, stronger for reading / judge / dialogue. Empty defaults
    # mean components will raise loudly if a role's model isn't configured.
    selector_model: str = ""
    reading_model: str = ""
    judge_model: str = ""
    dialogue_model: str = ""


def get_settings() -> Settings:
    return Settings()
