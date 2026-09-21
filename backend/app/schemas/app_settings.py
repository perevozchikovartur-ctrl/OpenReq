from pydantic import BaseModel, Field


class AppSettingsOut(BaseModel):
    has_openai_key: bool = False
    openai_api_key_hint: str | None = None
    ai_provider: str = "openai"
    openai_model: str | None = None
    ollama_base_url: str | None = None
    ollama_model: str | None = None
    has_ollama_url: bool = False
    request_defaults: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class AppSettingsUpdate(BaseModel):
    openai_api_key: str | None = None
    ai_provider: str | None = None
    openai_model: str | None = None
    ollama_base_url: str | None = None
    ollama_model: str | None = None
    request_defaults: dict | None = None
