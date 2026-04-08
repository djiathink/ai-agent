from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- Requis ---
    telegram_token: str
    anthropic_api_key: str

    # --- Optionnel : URL publique du service (Railway la fournit automatiquement) ---
    # Si définie, le webhook Telegram sera enregistré au démarrage.
    base_url: str = ""

    # --- Configuration de l'agent ---
    system_prompt: str = (
        "Tu es un assistant IA serviable et concis. "
        "Réponds toujours dans la langue de l'utilisateur."
    )
    welcome_message: str = (
        "Bonjour ! Je suis votre assistant IA. Comment puis-je vous aider ?"
    )
    claude_model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024


settings = Settings()
