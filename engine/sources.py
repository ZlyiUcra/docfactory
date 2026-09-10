"""Оголошення джерел примірника: читання, перевірка, білий список.

`sources.json` лежить у теці примірника й описує кожен ендпоінт, з якого
будується корпус. Той самий файл обмежує оновлювач: звертатися можна лише за
адресою, яку тут задекларовано. Дозвіл рахується з оголошення — точний збіг
адреси або, для джерела з `expand: chapters`, дочірня адреса тієї самої теки на
тому самому хості (так тягнуться глави багатосторінкового видання). Усе інше
відхиляється, хоч би звідки надійшла вказівка це завантажити.

Один запис — одне джерело. Поля: `id` (стабільний ключ), `url`, `reader` (як
читати відповідь), `title`, `added`, `note` (навіщо це в корпусі), для змісту —
`expand: chapters`.
"""

import json
import posixpath
from urllib.parse import unquote, urlsplit

NAME = "sources.json"

# Читачі, яких знає ядро. Новий домен додає своїх; тоді цей набір розширюється
# разом із реєстром читачів.
READERS = {"toc", "page", "pdf", "rfc", "report", "ldml"}


def load(instance_dir) -> dict:
    """Прочитати й перевірити `sources.json` примірника. Повертає весь об'єкт."""
    path = instance_dir / NAME
    if not path.exists():
        raise SystemExit(f"Немає {path}. Оголошення джерел примірника обов'язкове.")
    data = json.loads(path.read_text(encoding="utf-8"))
    src = data.get("sources")
    if not isinstance(src, list) or not src:
        raise SystemExit(f"{path}: очікував непорожній список `sources`.")
    seen = set()
    for s in src:
        for field in ("id", "url", "reader"):
            if not s.get(field):
                raise SystemExit(f"{path}: у записі бракує поля `{field}`: {s}")
        if s["id"] in seen:
            raise SystemExit(f"{path}: повторений id `{s['id']}`.")
        seen.add(s["id"])
        if urlsplit(s["url"]).scheme != "https":
            raise SystemExit(f"{path}: адреса не https: {s['url']}")
        if s["reader"] not in READERS:
            raise SystemExit(f"{path}: невідомий читач `{s['reader']}` у `{s['id']}`. "
                             f"Відомі: {', '.join(sorted(READERS))}")
    return data


def hosts(sources) -> list:
    """Хости, що виводяться з оголошення. Оновлювач звертається лише до них."""
    return sorted({urlsplit(s["url"]).hostname for s in sources})


def _within(candidate: str, base: str) -> bool:
    """Чи лежить шлях candidate всередині теки base. Порівнюються шляхи, а не
    рядки: кандидат розкодовується і нормалізується, тож «..» і його відсоткові
    форми не виводять за оголошену теку, а межа сегмента обов'язкова — /spec
    не дозволяє сусіда /spec-internal. Колишній startswith по сирих рядках
    пропускав і те, і те; тримався дефект лише випадкових властивостей
    нинішнього оголошення (завершальна коса риска) і нинішнього читача.
    """
    cand = posixpath.normpath(unquote(candidate))
    if ".." in cand.split("/"):
        return False
    base_norm = posixpath.normpath(base if base.endswith("/") else base + "/")
    return cand == base_norm or cand.startswith(base_norm + "/")


def allowed(url: str, sources) -> bool:
    """Чи дозволено оновлювачеві звертатися за цією адресою.

    Дозвіл рахується з оголошення: тільки https і або точний збіг задекларованої
    адреси, або — для джерела з `expand: chapters` — адреса того самого хоста,
    що лежить усередині оголошеної теки (шляхи порівнюються нормалізованими,
    див. _within). Усе поза цим — ні, незалежно від того, хто попросив
    завантажити.
    """
    p = urlsplit(url)
    if p.scheme != "https":
        return False
    if any(url == s["url"] for s in sources):
        return True
    for s in sources:
        if s.get("expand") == "chapters":
            b = urlsplit(s["url"])
            if p.hostname == b.hostname and b.path and _within(p.path, b.path):
                return True
    return False
