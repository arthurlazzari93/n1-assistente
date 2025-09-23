from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    WEBHOOK_SHARED_SECRET: str = os.getenv("WEBHOOK_SHARED_SECRET", "")
    MOVIDESK_TOKEN: str = os.getenv("MOVIDESK_TOKEN", "")
    ALLOW_EMAIL_TO: str = os.getenv("ALLOW_EMAIL_TO", "suporte@tecnogera.com.br")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "700"))
    DB_PATH: str = os.getenv("DB_PATH", "n1agent.db")
    KB_TOP_K = 2
    KB_MIN_SCORE = 0.62

settings = Settings()
