"""Опис корпусу: шапки, суми, і чим упізнати зсув. $0, без мережі.

Кожен .txt у `corpus/` починається з трирядкової шапки: назва, адреса джерела,
дата завантаження. Походження кожного документа вже записане в ньому самому —
маніфест збирає це в один файл і додає те, чого в шапці немає: суму тексту, якою
`--status` відповідає на питання «чи змінилося».

ЧОМУ СУМА РАХУЄТЬСЯ ЛИШЕ ПО ТІЛУ

Третій рядок шапки — дата завантаження, і вона міняється щоразу, коли документ
перезавантажують. Якби сума рахувалася по цілому файлу, кожне перезавантаження
виглядало б як зміна вмісту, навіть коли текст той самий. Тому шапка з-під суми
виключена: сума описує текст специфікації, а не факт звернення до сервера.

ЧОМУ ІДЕНТИФІКАТОР І ІМ'Я ФАЙЛА — ЦЕ РІЗНІ РЕЧІ

Ім'я файла глави несе позицію документа у змісті видання: «22-text-processing.txt»
— двадцять другий за порядком, а не двадцять другий розділ специфікації.
Вклиниться нова глава раніше — і всі наступні файли перейменуються, а разом з
іменами поїдуть ідентифікатори фрагментів і номери точок у Qdrant. Ідентифікатор
такого зсуву не має: за ним документ упізнається після зсуву, а за іменем видно,
що зсув стався. Тому маніфест тримає і ім'я, і ідентифікатор.

Сам ідентифікатор глави — з префіксом джерела: «ecma262/scope», не «scope».
Обидва видання відкриваються главами Scope, Conformance, Normative references і
Overview, тож голий хвіст імені ключем не є — чотири таких id ділилися б на два
документи кожен, і refresh за id перезаписував би обидва.
"""

import hashlib
import json
import pathlib
import re

NAME = "index.json"

_SOURCE = "джерело:"
_FETCHED = "отримано:"


def header(path: pathlib.Path) -> dict:
    """Назва, адреса і дата з трирядкової шапки документа."""
    out = {"title": "", "url": "", "fetched": ""}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            value = line[1:].strip()
            if value.startswith(_SOURCE):
                out["url"] = value[len(_SOURCE):].strip()
            elif value.startswith(_FETCHED):
                out["fetched"] = value[len(_FETCHED):].strip()
            elif not out["title"]:
                out["title"] = value
    return out


def body_of(text: str) -> str:
    """Текст документа без шапки — те, що справді прийшло зі специфікації.

    Береться від рядка, який уже не починається з «#», і далі до кінця. Через цю
    саму функцію проходить і документ із диска, і щойно зібраний зі сторінки —
    інакше суми порівнювати не можна, бо дата в шапці в них різна за означенням.
    """
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "".join(lines[i:])


def body(path: pathlib.Path) -> str:
    """Тіло документа, що лежить на диску."""
    return body_of(path.read_text(encoding="utf-8"))


def digest(text: str) -> str:
    """Сума тексту. Нею вирішується, чи змінився документ."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entry(path: pathlib.Path, doc_id: str, **extra) -> dict:
    """Запис маніфеста для одного документа, зібраний із самого файла."""
    head = header(path)
    text = body(path)
    record = {"file": path.name, "id": doc_id, "title": head["title"],
              "url": head["url"], "fetched": head["fetched"],
              "chars": len(text), "sha256": digest(text)}
    record.update({k: v for k, v in extra.items() if v})
    return record


def load(folder: pathlib.Path) -> dict | None:
    """Маніфест теки або None, якщо його ще не писали."""
    path = folder / NAME
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"written": "", "documents": data}
    return data


def save(folder: pathlib.Path, data: dict) -> pathlib.Path:
    """Записує маніфест поруч із документами. Повертає шлях."""
    path = folder / NAME
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def by_id(data: dict | None) -> dict:
    """Документи маніфеста за ідентифікатором."""
    return {d["id"]: d for d in (data or {}).get("documents", [])}


def by_file(data: dict | None) -> dict:
    """Документи маніфеста за ім'ям файла."""
    return {d["file"]: d for d in (data or {}).get("documents", [])}


def classify(name: str, sources: list) -> tuple[str, str]:
    """(ідентифікатор, джерело) для файла корпусу за іменем і оголошенням.

    Одиничні документи (`pdf`/`rfc`/`report`/`ldml`) названі рівно за своїм id,
    тож упізнаються точно з оголошення. Глави багатосторінкового видання несуть
    позицію («22-text-processing»), розділи однієї сторінки — префікс стандарту
    («402-08-intl-object»); в обох ідентифікатор — джерело плюс хвіст без
    позиції («ecma262/text-processing»), а джерело — те, чий читач розгортає
    такі імена. Префікс джерела обов'язковий: обидва видання починаються
    главами Scope і Conformance, і голий хвіст ключем не був би — чотири id
    ділилися б на два документи кожен. Та сама форма id — у читачів
    (engine/readers/ecmarkup.py), інакше refresh за id не знайде документа.
    """
    stem = name[:-4] if name.endswith(".txt") else name
    singles = {f"{s['id']}.txt": s["id"] for s in sources
               if s["reader"] in ("pdf", "rfc", "report", "ldml")}
    if name in singles:
        return singles[name], singles[name]
    page_src = next((s["id"] for s in sources if s["reader"] == "page"), "")
    toc_src = next((s["id"] for s in sources if s["reader"] == "toc"), "")
    if stem.startswith("402-"):
        _, _, slug = stem[4:].partition("-")
        tail = slug or stem
        return (f"{page_src}/{tail}" if page_src else tail, page_src)
    m = re.match(r"^\d+-(.+)$", stem)
    if m and toc_src:
        return (f"{toc_src}/{m.group(1)}", toc_src)
    return (stem, "")


def build_corpus(corpus_dir: pathlib.Path, sources: list, written: str) -> dict:
    """Паспорт корпусу з того, що лежить на диску. Без мережі: ідентифікатор і
    джерело виводяться з імені файла та оголошення, решта — із самого файла."""
    docs = []
    for p in sorted(corpus_dir.glob("*.txt")):
        doc_id, source = classify(p.name, sources)
        docs.append(entry(p, doc_id, source=source))
    return {"written": written, "documents": docs}


def order_change(was: list, now: list) -> dict:
    """Що сталося з переліком і з якої позиції поїхали імена файлів.

    `added` і `removed` — ідентифікатори, яких не було або не стало. `shift` —
    перша позиція, на якій переліки розійшлися: усе від неї і далі дістане інше
    ім'я файла, навіть якщо сам документ не змінився ні на символ.
    """
    was_set, now_set = set(was), set(now)
    shift = None
    for i in range(min(len(was), len(now))):
        if was[i] != now[i]:
            shift = i
            break
    if shift is None and len(was) != len(now):
        shift = min(len(was), len(now))
    return {
        "added": [(i, d) for i, d in enumerate(now) if d not in was_set],
        "removed": [(i, d) for i, d in enumerate(was) if d not in now_set],
        "shift": shift,
        "moved": 0 if shift is None else len(now) - shift,
    }
