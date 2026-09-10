"""
СПІЛЬНЕ · виклик моделі Anthropic для агента й guardrail.

Усе, що фабрика бере від моделі, — два помічники: `_call` для циклу агента і
`ask_json` для guardrail. Раніше вони жили у спільному ядрі курсу
(`core/agent.py`); тут перенесені в код фабрики, щоб примірник не тягнув за собою
цілий модуль. Логіка та сама: клієнт Anthropic, ретраї на перевантаженні, дешевша
модель для допоміжних викликів.

Ключ береться з `.env` того примірника, з яким працює запуск (див. instance.py):
у код нічого не зашивається. І потрібен він рівно одному крокові — `ask`: без
ключа модуль імпортується, а падає перший виклик моделі, з підказкою.
"""

import inspect
import json
import os
import time

from anthropic import Anthropic, APIError, APIStatusError
from dotenv import load_dotenv

from . import instance

# Ключ і моделі — з .env примірника, з яким працює цей запуск (див. instance.py).
load_dotenv(instance.root() / ".env")

API_KEY = os.getenv("ANTHROPIC_API_KEY")

MODEL      = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")             # цикл агента
MODEL_FAST = os.getenv("ANTHROPIC_MODEL_FAST", "claude-haiku-4-5-20251001")  # guardrail
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 1200))

# Клієнт і перевірка ключа — при першому виклику, а не при імпорті. Гроші
# витрачає рівно один крок, ask, і відмова без ключа мусить бути локальною для
# нього: сервер, шари і smoke імпортують цей модуль і працюють без .env.
# Раніше перевірка стояла на рівні модуля, і будь-який імпорт без ключа валив
# увесь процес — smoke у тому числі.
_CLIENT: Anthropic | None = None
_ACCEPTS_TEMPERATURE = True


def _connect() -> Anthropic:
    global _CLIENT, _ACCEPTS_TEMPERATURE
    if _CLIENT is None:
        if not API_KEY:
            raise SystemExit(
                "Не знайдено ANTHROPIC_API_KEY — він потрібен лише крокові ask.\n"
                "  cp .env.example .env   і впишіть ключ у .env")
        _CLIENT = Anthropic(api_key=API_KEY)
        # У anthropic 1.x параметр temperature прибрали з messages.create();
        # зʼясовуємо це один раз і мовчки прибираємо там, де його не беруть, щоб
        # код працював і на 0.x.
        _ACCEPTS_TEMPERATURE = "temperature" in inspect.signature(
            _CLIENT.messages.create).parameters
    return _CLIENT


def _call(**kwargs):
    """Виклик API з ретраями на перевантаження і rate limit."""
    client = _connect()
    if not _ACCEPTS_TEMPERATURE:
        kwargs.pop("temperature", None)
    for attempt in range(3):
        try:
            return client.messages.create(**kwargs)
        except APIStatusError as e:
            if e.status_code in (429, 500, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
        except APIError:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise


def ask(system: str, user: str, max_tokens: int = 400, fast: bool = True,
        temperature: float = 0.0) -> str:
    """Допоміжний виклик без інструментів (guardrail). Дешева модель, temperature=0,
    щоб перевірка була детермінованою."""
    resp = _call(model=MODEL_FAST if fast else MODEL, max_tokens=max_tokens,
                 temperature=temperature, system=system,
                 messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def ask_json(system: str, user: str, fallback: dict, fast: bool = True) -> dict:
    """Те саме, але з очікуванням JSON. Нерозпарсений результат повертає fallback."""
    raw = ask(system + "\nПовертай ТІЛЬКИ валідний JSON, без пояснень.", user, fast=fast)
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return {**fallback, "_raw": raw[:200]}
