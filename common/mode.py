"""
СПІЛЬНЕ · рішення про спосіб пошуку, ухвалене один раз при підготовці.

Файл `out/mode.json` пише `server/setup.py` — там і тільки там
у людини питають, чи піднімати Docker з Qdrant. Сервер цей файл лише читає.

Файла немає — значить, ніхто нічого не питав і не ставив, і працює пошук по
словах. Це навмисно: мовчазне рішення завжди на користь того способу, який
нічого не потребує.
"""

import json
import time

from . import instance

PATH = instance.root() / "out" / "mode.json"


def read() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"search": "words"}


def write(mode: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    mode = dict(mode, decided=time.strftime("%Y-%m-%d %H:%M:%S"))
    PATH.write_text(json.dumps(mode, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
