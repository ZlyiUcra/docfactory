"""Реєстр читачів: ім'я формату → функція, що дає перелік документів джерела.

Читач приймає джерело (запис `sources.json`) і контекст (звернення крізь білий
список, дата) і повертає список `Item(id, file, make)`. `make()` — відкладене:
воно завантажує й розбирає документ лише тоді, коли оновлювач вирішив його
записати, тож пропущені документи не смикають чужий сервер. Читачі
багатосторінкового джерела (`toc`) і сторінки з розділами (`page`) складають
перелік одразу, бо для цього треба прочитати зміст; тіло глав тягнеться в `make`.

Новий домен додає своїх читачів окремим модулем із `@register("ім'я")`; ядро при
цьому не змінюється. Тут зареєстровані шість, потрібних для ECMAScript.
"""

import collections

Item = collections.namedtuple("Item", "id file make")

REGISTRY: dict = {}


def register(name: str):
    """Декоратор: додає читача в реєстр під іменем формату."""
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def get(name: str):
    if name not in REGISTRY:
        raise SystemExit(f"Немає читача «{name}». Є: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[name]


# Імпорт наповнює REGISTRY — кожен модуль реєструє свої читачі.
from engine.readers import ecmarkup, pdf, references  # noqa: E402,F401
