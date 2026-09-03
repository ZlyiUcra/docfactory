"""
СПІЛЬНЕ · Qdrant: сховище векторів і тексту фрагментів.

Пошук по словах живе в цьому ж процесі й нічого не потребує. Пошук за змістом
потребує двох речей: чисел, якими описано кожен фрагмент, і місця, де ці числа
лежать. Числа рахує fastembed (див. embed.py), лежать вони тут — у Qdrant, який
працює окремим процесом у Docker.

ЧОМУ HTTP, А НЕ БІБЛІОТЕКА

У `.venv` модуля є `qdrant-client`, він стоїть заради курсового
knowledge_qdrant.py. Практика його не імпортує і користується HTTP-API самого
сервера через urllib зі стандартної бібліотеки: потрібно рівно п'ять запитів —
чи живий, чи є колекція, створити колекцію, залити точки, пошукати. Так само
зроблено в практиці модуля 4, і причина та сама: менше залежностей у тому коді,
який запускає чужий клієнт.

ЩО ЛЕЖИТЬ У КОЛЕКЦІЇ

Точка — це фрагмент: вектор плюс увесь його паспорт у payload (ідентифікатор,
розділ, заголовок, документ, адреса джерела) і сам текст. Після заливання Qdrant
містить самодостатню копію того, що потрібно для відповіді: і числа для пошуку,
і текст для цитування. Ім'я колекції складається з набору документів і моделі —
`spec-suite-bge-small`: набір визначає, які фрагменти всередині, модель — довжину і
геометрію векторів, і мішати їх в одній колекції означало б рахувати відстань
між непорівнюваними числами.

ЩО ЦЕЙ МОДУЛЬ НЕ РОБИТЬ

Не видаляє колекцій, не чистить томів, не зупиняє контейнерів. Створити,
дописати, прочитати — усе. Прибирання лишається людині, і команди для нього
названі в README практики.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from .corpus import DOC_SET
from .embed import MODEL_KEY

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")

# Контейнер і том — ті самі імена, що в лабораторній модуля 2, щоб на машині не
# з'являлося другої бази з тими самими даними.
CONTAINER = os.getenv("QDRANT_CONTAINER", "agent0826-qdrant")
VOLUME = os.getenv("QDRANT_VOLUME", "agent0826-qdrant")
IMAGE = os.getenv("QDRANT_IMAGE", "qdrant/qdrant")

COLLECTION = os.getenv("QDRANT_COLLECTION", f"spec-{DOC_SET}-{MODEL_KEY}")

TIMEOUT_SEC = 30
BATCH = 128


class Unavailable(RuntimeError):
    """Qdrant зараз недосяжний. Це не привід падати: той, хто нас кличе, має
    відповісти пошуком по словах і сказати про це вголос."""


def _request(method: str, path: str, payload: dict | None = None,
             timeout: int = TIMEOUT_SEC) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{QDRANT_URL}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise Unavailable(f"Qdrant відповів {e.code} на {method} {path}: {body}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise Unavailable(f"немає зв'язку з Qdrant ({QDRANT_URL}): "
                          f"{getattr(e, 'reason', e)}")


def alive(timeout: int = 3) -> bool:
    """Чи відповідає сервер просто зараз."""
    try:
        _request("GET", "/collections", timeout=timeout)
        return True
    except Unavailable:
        return False


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "version"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_running(wait_sec: int = 30) -> bool:
    """Піднімає контейнер, якщо сервер не відповідає. Повертає True, коли Qdrant
    живий — байдуже, чи він уже працював, чи ми його щойно запустили.

    Наявний контейнер запускається (`docker start`), відсутній створюється
    (`docker run`) з іменованим томом, щоб дані пережили і зупинку, і видалення
    контейнера. Нічого не видаляється й не перестворюється.
    """
    if alive():
        return True
    if not docker_available():
        return False

    exists = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{CONTAINER}$"],
        capture_output=True, text=True, timeout=20).stdout.strip()
    cmd = (["docker", "start", CONTAINER] if exists else
           ["docker", "run", "-d", "--name", CONTAINER,
            "-p", "6333:6333", "-p", "6334:6334",
            "-v", f"{VOLUME}:/qdrant/storage", IMAGE])
    if subprocess.run(cmd, capture_output=True, timeout=180).returncode != 0:
        return False

    # Контейнер стартує не миттєво: перші секунди порт уже відкритий, а сервер
    # ще не відповідає.
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if alive(timeout=2):
            return True
        time.sleep(1)
    return False


def stop() -> bool:
    """Зупиняє контейнер. Повертає True, якщо після цього він не працює.

    Саме зупиняє — `docker stop`, не `rm`. Контейнер лишається на місці, том
    лишається на місці, колекції всередині лишаються на місці; наступний
    `docker start` повертає все таким, яким воно було. Видалення тут немає і не
    буде: том цієї бази спільний з лабораторною модуля 2, і команда практики не
    має права зносити чужі дані.
    """
    if not docker_available():
        return not alive()
    subprocess.run(["docker", "stop", CONTAINER], capture_output=True, timeout=60)
    return not alive(timeout=2)


def collection_info(name: str = COLLECTION) -> dict | None:
    """Опис колекції або None, якщо її ще немає."""
    try:
        return _request("GET", f"/collections/{name}")["result"]
    except Unavailable as e:
        if "404" in str(e):
            return None
        raise


def count(name: str = COLLECTION) -> int:
    info = collection_info(name)
    return int(info["points_count"] or 0) if info else 0


def ensure_collection(dim: int, name: str = COLLECTION) -> bool:
    """Створює колекцію, якщо її немає. Наявну не чіпає. True — щойно створено.

    Розбіжність довжини вектора не виправляється мовчки: стара колекція
    лишається на місці, а людина вирішує, що з нею робити.
    """
    info = collection_info(name)
    if info is not None:
        have = info["config"]["params"]["vectors"]["size"]
        if have != dim:
            raise Unavailable(
                f"колекція {name} тримає вектори довжини {have}, а модель дає "
                f"{dim}; нічого не змінюю — приберіть стару колекцію самі "
                f"або залийте під іншим іменем")
        return False
    _request("PUT", f"/collections/{name}",
             {"vectors": {"size": dim, "distance": "Cosine"}})
    return True


def upsert(points: list[dict], name: str = COLLECTION) -> int:
    """Записує точки пачками. Точка з тим самим номером замінюється — номер
    фрагмента сталий, тож повторне заливання оновлює, а не дублює."""
    sent = 0
    for start in range(0, len(points), BATCH):
        chunk = points[start:start + BATCH]
        _request("PUT", f"/collections/{name}/points?wait=true", {"points": chunk})
        sent += len(chunk)
    return sent


def fetch(ids: list, name: str = COLLECTION) -> dict:
    """Payload названих точок, за їхніми стійкими номерами. Потрібно, щоб знати,
    чи точка з цим номером уже лежить і чи не змінився її текст: у payload лежить
    сума тексту, і заливання порівнює її з поточною. Ключ — номер як є (рядок
    UUID), без перетворення на число."""
    res = _request("POST", f"/collections/{name}/points",
                   {"ids": ids, "with_payload": True})["result"]
    return {r["id"]: r["payload"] for r in res}


def all_ids(name: str = COLLECTION) -> set:
    """Номери всіх точок колекції. Лише читання: гортає сторінками (scroll) без
    векторів і payload. Потрібно, щоб знайти точки, яких немає серед поточних
    фрагментів, — тобто такі, що описують уже видалений текст. Нічого не видаляє."""
    ids: set = set()
    offset = None
    while True:
        body = {"limit": 1024, "with_payload": False, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        res = _request("POST", f"/collections/{name}/points/scroll", body)["result"]
        for point in res["points"]:
            ids.add(point["id"])
        offset = res.get("next_page_offset")
        if offset is None:
            return ids


def search(vector: list[float], limit: int, name: str = COLLECTION) -> list[dict]:
    """Найближчі точки. Повертає список payload'ів разом з оцінкою подібності."""
    body = {"vector": [float(x) for x in vector], "limit": limit,
            "with_payload": True}
    res = _request("POST", f"/collections/{name}/points/search", body)["result"]
    return [{"score": float(r["score"]), **r["payload"]} for r in res]
