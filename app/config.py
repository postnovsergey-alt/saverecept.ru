"""Конфигурация из переменных окружения. Ничего обязательного — всё имеет разумные дефолты."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _path(env_name: str, default: str) -> Path:
    raw = os.getenv(env_name, default)
    p = Path(raw)
    if not p.is_absolute():
        p = BASE_DIR / p
    p.mkdir(parents=True, exist_ok=True)
    return p


DATA_DIR = _path("DATA_DIR", "./data")
MEDIA_DIR = _path("MEDIA_DIR", "./data/media")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'samobranka.db'}")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

# ---- LLM ----
# Основной провайдер и необязательный запасной. Оба говорят на OpenAI-совместимом
# протоколе, поэтому Gemini, OpenRouter, DeepSeek и локальная Ollama
# подключаются одинаково — меняется только базовый адрес и имя модели.
GEMINI_OPENAI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

LLM_BASE_URL = os.getenv("LLM_BASE_URL", GEMINI_OPENAI_BASE).strip().rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()

LLM_FALLBACK_BASE_URL = os.getenv("LLM_FALLBACK_BASE_URL", "").strip().rstrip("/")
LLM_FALLBACK_API_KEY = os.getenv("LLM_FALLBACK_API_KEY", "").strip()
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "").strip()


def _provider(name: str, base_url: str, api_key: str, model: str) -> dict | None:
    if base_url and api_key and model:
        return {"name": name, "base_url": base_url, "api_key": api_key, "model": model}
    return None


LLM_PROVIDERS = [p for p in (
    _provider("основной", LLM_BASE_URL, LLM_API_KEY, LLM_MODEL),
    _provider("запасной", LLM_FALLBACK_BASE_URL, LLM_FALLBACK_API_KEY, LLM_FALLBACK_MODEL),
) if p]

LLM_ENABLED = bool(LLM_PROVIDERS)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# Параметры сжатия картинок
IMAGE_MAX_WIDTH = int(os.getenv("IMAGE_MAX_WIDTH", "1280"))
IMAGE_THUMB_WIDTH = int(os.getenv("IMAGE_THUMB_WIDTH", "480"))
IMAGE_QUALITY = int(os.getenv("IMAGE_QUALITY", "78"))
IMAGE_MAX_COUNT = int(os.getenv("IMAGE_MAX_COUNT", "4"))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
