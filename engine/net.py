"""Звернення до мережі крізь білий список примірника.

Ніщо в ядрі не звертається до мережі повз цей модуль, і кожен виклик спершу
питає дозвіл (`allow(url)`): адреса поза оголошенням примірника не завантажується,
хоч би хто її підсунув. Обмеження ті самі, що були в завантажувачах: лише https
(це вже в `allow`), відповідь не більша за `MAX_BYTES`, між зверненнями пауза, і
є умовний запит «чи змінилося після нашої дати».
"""

import datetime
import email.utils
import urllib.error
import urllib.request

MAX_BYTES = 20_000_000
TIMEOUT_SEC = 60
PAUSE_SEC = 1.0
_UA = "agent0826-docfactory/1.0"


class Refused(Exception):
    """Адреса не проходить білий список примірника."""


def since_header(day: str) -> str:
    """Дата з шапки документа у вигляді, який розуміє If-Modified-Since."""
    when = datetime.datetime.strptime(day, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc)
    return email.utils.format_datetime(when, usegmt=True)


def fetch(url: str, allow, *, since: str = "") -> tuple[str, bytes]:
    """(код, байти). Код: «200», «304» або рядок «збій: …».

    `allow` — функція примірника: недозволена адреса не завантажується взагалі,
    підіймається Refused. `since` вмикає умовний запит: сервер відповість «304»,
    якщо документ не змінювався з тієї дати, і тіла не надішле.
    """
    if not allow(url):
        raise Refused(f"адреса поза оголошенням примірника: {url}")
    headers = {"User-Agent": _UA}
    if since:
        try:
            headers["If-Modified-Since"] = since_header(since)
        except ValueError:
            pass
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return (f"збій: більше за {MAX_BYTES} байтів", b"")
        return ("200", data)
    except urllib.error.HTTPError as e:
        return ("304", b"") if e.code == 304 else (f"збій: HTTP {e.code}", b"")
    except urllib.error.URLError as e:
        return (f"збій: {e.reason}", b"")
