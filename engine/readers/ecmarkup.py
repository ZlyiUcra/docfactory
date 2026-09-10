"""Читачі ecmarkup — розмітки, якою tc39 видає ECMA-262 і ECMA-402.

`toc`  — багатосторінкове видання (ECMA-262): зі змісту беруться адреси глав,
         кожна глава тягнеться окремою сторінкою і стає окремим документом.
`page` — одна сторінка з розділами (ECMA-402): сторінка ділиться на верхні
         розділи й додатки, кожен стає документом.

Розмітка зчищається так, щоб вийшов той самий вигляд, що в решті корпусу:
заголовок підрозділу окремим рядком, абзаци через порожній рядок, кроки
алгоритмів рядками з дефісом — саме по цих заголовках `common/corpus.py` ділить
документ на фрагменти. Код перенесено з колишніх завантажувачів практики без змін
по суті; змінилося тільки те, що адреса й дозвіл беруться з оголошення джерел.
"""

import html as html_lib
import re

from engine.readers import Item, register

# Розмітка видання мініфікована: значення атрибутів без лапок, а одразу за
# іменем файлу може стояти якір (href=indexed-collections.html#sec-...).
_HREF = re.compile(r"href=[\"']?([a-z0-9-]+\.html)[\"']?")
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_FIRST_CLAUSE_ID = re.compile(r"<emu-clause[^>]*\bid=[\"']?([\w.-]+)[\"']?[\s>]")
# Верхній розділ або додаток однієї сторінки ecmarkup: атрибути там без лапок.
_TOP = re.compile(r"<(emu-clause|emu-annex) id=([^\s>]+)[^>]*>\s*<h1><span class=secnum>"
                  r"([0-9]+|Annex [A-Z])(?=</span>|\s*<span)")


def _body(page: str) -> str:
    """Вміст сторінки без бічного меню: від spec-container до кінця."""
    start = page.find("<div id=spec-container")
    return page[start:] if start != -1 else page


def _strip(chunk: str) -> str:
    """Зчищає розмітку, лишаючи заголовки і абзаци окремими рядками."""
    chunk = re.sub(r"<(script|style)\b.*?</\1>", " ", chunk, flags=re.S)
    chunk = re.sub(r"<h1[^>]*>", "\n\n", chunk)
    chunk = re.sub(r"</h1>", "\n\n", chunk)
    chunk = re.sub(r"<li\b[^>]*>", "\n- ", chunk)
    chunk = re.sub(r"</(p|li|tr|dd|dt|emu-alg|emu-note|emu-table|div)>", "\n", chunk)
    chunk = re.sub(r"<br\s*/?>", "\n", chunk)
    chunk = re.sub(r"<[^>]+>", " ", chunk)
    chunk = html_lib.unescape(chunk)

    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in chunk.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def _title(page: str) -> str:
    """Заголовок розділу: «20 Fundamental Objects»."""
    m = _H1.search(_body(page))
    if not m:
        return ""
    return _strip(m.group(1)).replace("\n", " ").strip()


# Рядок-заголовок розділу в уже зчищеному тілі: «20.1.3 …», «B.1.1 …»,
# «Annex B …». Ним або довжиною тіла глава доводить, що вона — глава.
_SECTION_LINE = re.compile(r"^(?:Annex [A-Z]|[A-Z]|\d+)(?:\.\d+)*\s+\S", re.M)

# Найкоротше тіло справжньої глави без нумерованих підзаголовків — службові
# сторінки видання: колофон 920 символів, бібліографія 2049. Сторінка-заглушка
# («This page has moved.», тіло помилки) до цієї межі не дотягує.
_MIN_BODY = 400


def _looks_like_chapter(title: str, body: str) -> bool:
    return bool(title) and (bool(_SECTION_LINE.search(body))
                            or len(body) >= _MIN_BODY)


def _document_262(page: str, url: str, stamp: str) -> str:
    """Готовий текст документа глави 262: трирядкова шапка, порожній рядок, тіло.

    Сторінку, якої не впізнає, відкидає винятком — тим самим, яким відмовляють
    решта читачів. Раніше перевірки не було, і заглушка «This page has moved.»
    ставала документом на двадцять один символ: refresh писав його поверх
    доброї глави, manifest фіксував суму пошкодження, а check далі казав
    «без змін»."""
    title = _title(page)
    body = _strip(_body(page))
    if not _looks_like_chapter(title, body):
        raise SystemExit(
            f"Сторінка не схожа на главу видання ({url}): "
            f"{'немає заголовка h1' if not title else 'тіло куце і без жодного підзаголовка'}"
            f" — документ не записую, наявний лишається як був.")
    anchor = _FIRST_CLAUSE_ID.search(_body(page))
    source = f"{url}#{anchor.group(1)}" if anchor else url
    return f"# {title}\n# джерело: {source}\n# отримано: {stamp}\n\n{body}\n"


def _page_id(name: str) -> str:
    """Ідентифікатор глави — слаг сторінки видання, без позиції і без .html."""
    return name[:-5] if name.endswith(".html") else name


def _file_name(position: int, name: str) -> str:
    """Ім'я файла глави: позиція у змісті плюс слаг сторінки. Позиція тут
    і є тим, що зсувається, коли у зміст вклинюють нову главу."""
    return f"{position:02d}-{_page_id(name)}.txt"


def _chapters(toc_html: str) -> list[str]:
    """Імена сторінок глав у порядку змісту багатосторінкового видання."""
    seen, out = set(), []
    for name in _HREF.findall(toc_html):
        if name not in seen:
            seen.add(name)
            out.append(name)
    if not out:
        raise SystemExit("У змісті не знайдено жодного посилання на главу — "
                         "розмітка сторінки змінилася, читача треба поправити.")
    return out


@register("toc")
def toc(source: dict, ctx) -> list[Item]:
    base = source["url"]
    names = _chapters(ctx.text(base))
    items = []
    for pos, name in enumerate(names, 1):
        url = base + name

        def make(url=url):
            return _document_262(ctx.text(url), url, ctx.stamp)

        items.append(Item(id=_page_id(name), file=_file_name(pos, name), make=make))
    return items


# ── ECMA-402: одна сторінка → документ на верхній розділ ──────────────────

def _slug(cid: str) -> str:
    return re.sub(r"^(sec-|annex-)", "", cid)


def _name_402(num: str, cid: str) -> str:
    tag = f"{int(num):02d}" if num.isdigit() else num.replace("Annex ", "")
    return f"402-{tag}-{_slug(cid)}.txt"


def _chapters_402(page: str) -> list[tuple[str, str, str]]:
    """(id, номер, html розділу) для кожного верхнього розділу і додатків A/B."""
    hits = list(_TOP.finditer(page))
    out = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(page)
        cid, num = m.group(2), m.group(3)
        if cid in ("sec-colophon", "sec-copyright-and-software-license"):
            continue
        out.append((cid, num, page[m.start():end]))
    if not out:
        raise SystemExit("На сторінці ECMA-402 не знайдено жодного розділу — "
                         "розмітка змінилася, читача треба поправити.")
    return out


def _document_402(chunk: str, cid: str, url: str, stamp: str) -> str:
    """Та сама відмова, що в _document_262: розділ без заголовка h1 чи з куцим
    тілом без підзаголовків — виняток, а не куций документ поверх доброго.
    Колишній тихий fallback «немає h1 — хай назвою буде cid» саме й ховав
    такий випадок."""
    m = _H1.search(chunk)
    title = _strip(m.group(1)).replace("\n", " ").strip() if m else ""
    body = _strip(chunk)
    if not _looks_like_chapter(title, body):
        raise SystemExit(
            f"Розділ {cid} не схожий на розділ ECMA-402: немає заголовка або "
            f"тіло куце і без підзаголовків — документ не записую.")
    return f"# {title}\n# джерело: {url}#{cid}\n# отримано: {stamp}\n\n{body}\n"


@register("page")
def page(source: dict, ctx) -> list[Item]:
    url = source["url"]
    html = ctx.text(url)
    items = []
    for cid, num, chunk in _chapters_402(html):
        def make(chunk=chunk, cid=cid):
            return _document_402(chunk, cid, url, ctx.stamp)

        items.append(Item(id=_slug(cid), file=_name_402(num, cid), make=make))
    return items
