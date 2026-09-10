"""
СПІЛЬНЕ · де лежать дані примірника, з яким працює цей код.

Код сервера тепер спільний і лежить у `docfactory/server/` та `docfactory/common/`,
поза будь-яким доменом. Тобто сам із себе він уже не може вивести, чий корпус
читати й куди писати журнал: раніше він лежав усередині примірника й брав сусідні
теки, тепер примірник йому кажуть ззовні.

Каже його `df`: перед кожним кроком виставляє змінну `DF_INSTANCE_DIR` на теку
`instances/<домен>/`. Звідти беруться `corpus/`, `.env`, `out/`, `config.json` —
усе, що в кожного домену своє. Коли клієнт (Mode A, Inspector) піднімає сервер
підпроцесом, та сама змінна передається в його оточення явно, бо стандартний
запуск stdio-сервера оточення батька не успадковує.

Змінної немає — значить, код підняли повз `df`, і він не знає, з яким доменом
працювати. Це не привід мовчки взяти якийсь домен навмання: падаємо з підказкою.
"""

import json
import os
import pathlib
import re

ENV = "DF_INSTANCE_DIR"

# Що дозволено в полях config.json, які стають ім'ям колекції чи моделі. Це
# дані, але дані, що потрапляють в адресу REST-запиту до Qdrant і в назви — тож
# межа тут не «що завгодно», а звичайне ім'я без роздільників шляхів і пробілів.
_NAME_OK = re.compile(r"^[A-Za-z0-9._-]+$")

_config: dict | None = None


def config() -> dict:
    """config.json примірника, прочитаний один раз за запуск. Це дані, а не код:
    значення перевіряються тут за шаблоном і ніколи не течуть в оболонку —
    раніше df друкував їх у eval, і поле collection могло виконати команду.

    Без DF_INSTANCE_DIR або без файла повертає порожній словник, не падає:
    жорстка вимога до змінної лишається в root(), де без примірника справді
    не можна, а перекриття змінними оточення працюють і без конфігурації."""
    global _config
    if _config is not None:
        return _config
    if not os.environ.get(ENV):
        _config = {}
        return _config
    path = root() / "config.json"
    if not path.exists():
        _config = {}
        return _config
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{path}: не читається ({exc})")
    for field in ("collection", "embed_model"):
        value = data.get(field)
        if value is not None and (not isinstance(value, str)
                                  or not _NAME_OK.match(value)):
            raise SystemExit(
                f"{path}: поле {field} мусить бути іменем з літер, цифр, "
                f"«._-», а не {value!r}")
    port = data.get("port")
    if port is not None and (not isinstance(port, int)
                             or not 1 <= port <= 65535):
        raise SystemExit(f"{path}: поле port мусить бути цілим 1–65535, "
                         f"а не {port!r}")
    _config = data
    return _config


def root() -> pathlib.Path:
    """Тека примірника, з яким працює цей запуск. Без неї далі йти не можна."""
    value = os.environ.get(ENV)
    if not value:
        raise SystemExit(
            f"Не задано {ENV} — код сервера спільний і не знає, чий домен обслуговує.\n"
            f"  Піднімайте кроки через ./df <домен> <крок> (він виставляє {ENV}).")
    path = pathlib.Path(value)
    if not path.is_dir():
        raise SystemExit(f"{ENV} вказує на неіснуючу теку: {path}")
    return path.resolve()
