"""Читачі вільних нормативних документів: `rfc`, `report`, `ldml`.

`rfc`    — текст RFC (rfc-editor.org): колонтитули сторінок, зміст і кінцевий
           юридичний блок прибираються, а заголовки «2.1.  Basic Language Range»
           стають «2.1 Basic Language Range» — так їх упізнає поділ корпусу.
`report` — HTML звіту Unicode (UAX/UTS) з номерами розділів у заголовках.
`ldml`   — HTML частини UTS #35, де номерів розділів у розмітці немає: читач
           нумерує заголовки сам, рівень у рівень від першого після змісту.

Розділи «Status», «Contents», «Parts», «Acknowledgments» і «Modifications»
викидаються. Код перенесено з колишнього завантажувача практики без змін по суті.
"""

import re
from html.parser import HTMLParser

from engine.readers import Item, register

# ── RFC: текст → текст без колонтитулів ───────────────────────────────────

_RFC_FOOTER = re.compile(r"^\S.*\[Page \d+\]$")
_RFC_HEADER = re.compile(r"^RFC \d+ {2,}.* {2,}[A-Z][a-z]+ \d{4}$")
_RFC_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.\s+(\S.*)$")


def _clean_rfc(text: str) -> str:
    """Текст RFC як є, без колонтитулів сторінок, змісту з крапками і
    кінцевого юридичного блоку. Заголовки в RFC стоять на нульовій колонці з
    крапкою після номера, тіло — з відступом у три пробіли: перші стають
    рядками «2.1 Basic Language Range», у другого відступ знімається."""
    out = []
    for ln in text.replace("\f", "").splitlines():
        ln = ln.rstrip()
        if _RFC_FOOTER.match(ln) or _RFC_HEADER.match(ln) or "......" in ln:
            continue
        if ln == "Full Copyright Statement":
            break
        m = _RFC_HEADING.match(ln)
        if m:
            out.extend(["", f"{m.group(1)} {m.group(2)}", ""])
            continue
        out.append(re.sub(r"^ {3}", "", ln))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


# ── Звіти Unicode: HTML → блоки → текст ───────────────────────────────────

_BLOCK = {"p", "div", "li", "tr", "table", "ul", "ol", "dl", "dt", "dd", "blockquote",
          "pre", "h6", "section", "figure", "caption"}
_SKIP = {"script", "style", "head", "title"}
_LEVEL = {"h1": 0, "h2": 1, "h3": 2, "h4": 3, "h5": 4}
_DROP = {"Status", "Parts", "Acknowledgments", "Acknowledgements", "Modifications"}
_INDENT = "    "
# Рядок, який поділ корпусу прийняв би за заголовок розділу: число, пробіл, слово.
_LOOKS_LIKE_HEADING = re.compile(r"^\d+(?:\.\d+)*\s+\S")


class _Blocks(HTMLParser):
    """Розбирає сторінку на блоки: («h», рівень, заголовок) і («t», текст).
    Поза <pre> пробіли стискаються; всередині переноси рядків лишаються, а
    кожен рядок дістає відступ у чотири пробіли. Комірки таблиці розділяє
    « | », пункт списку починається з «- »."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._buf = []
        self._skip = 0
        self._pre = 0
        self._level = None

    def _flush(self):
        text = "".join(self._buf)
        if text.strip():
            self.blocks.append(("t", text))
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre += 1
            self._buf.append("\n" + _INDENT)
        if tag in _LEVEL:
            self._flush()
            self._level = _LEVEL[tag]
        elif tag in ("td", "th"):
            self._buf.append(" | ")
        elif tag == "li":
            self._buf.append("\n- ")
        elif tag in _BLOCK or tag == "br":
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "pre":
            self._pre = max(0, self._pre - 1)
        if tag in _LEVEL:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self._buf = []
            if text and self._level is not None:
                self.blocks.append(("h", self._level, text))
            self._level = None
        elif tag in _BLOCK or tag == "li":
            self._buf.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._pre:
            self._buf.append(data.replace("\n", "\n" + _INDENT))
        else:
            self._buf.append(re.sub(r"\s+", " ", data))


def _number_headings(blocks: list) -> list:
    """Дописує номери заголовкам частини LDML: рахунок рівень у рівень
    від першого заголовка після змісту, як на самій сторінці."""
    counters = [0, 0, 0, 0]
    started = False
    out = []
    for b in blocks:
        if b[0] != "h" or b[1] < 1:
            out.append(b)
            continue
        if not started:
            started = b[2].startswith("Contents")
            out.append(b)
            continue
        level = b[1]
        counters[level - 1] += 1
        for k in range(level, 4):
            counters[k] = 0
        num = ".".join(str(c) for c in counters[:level])
        out.append(("h", level, f"{num} {b[2]}"))
    return out


def _drop_sections(blocks: list) -> list:
    """Викидає розділи, що не є змістом звіту: статус чернетки, зміст,
    перелік частин, подяки, історію змін. Розділ триває до наступного
    заголовка того самого або вищого рівня."""
    out, cut = [], None
    for b in blocks:
        if b[0] == "h":
            level = b[1]
            name = re.sub(r"^\d+(?:\.\d+)*\s+", "", b[2])
            if cut is not None and level <= cut:
                cut = None
            if cut is None and (name in _DROP or name.startswith("Contents")):
                cut = level
                continue
        if cut is None:
            out.append(b)
    return out


def _render_blocks(blocks: list) -> str:
    """Заголовки — окремими рядками з порожніми навколо. Поділ корпусу
    вважає заголовком рядок «число, пробіл, назва» без відступу, тому рядки
    таблиць зберігають початковий «|», а рядки з <pre> і взагалі будь-який
    рядок тексту такого вигляду — відступ: інакше приклад даних «0385 0021;
    …» або речення «1.00 gets the same category as 1.» відкрили б новий
    розділ."""
    lines = []
    for b in blocks:
        if b[0] == "h":
            lines.extend(["", b[2], ""])
            continue
        for ln in b[1].splitlines():
            flat = re.sub(r"[ \t\xa0]+", " ", ln).strip()
            code = ln.startswith(_INDENT) or _LOOKS_LIKE_HEADING.match(flat)
            lines.append(_INDENT + flat if code and flat else flat)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def _document(source: dict, kind: str, ctx) -> str:
    url = source["url"]
    title = source.get("title", source["id"])
    text = ctx.bytes(url).decode("utf-8")
    if kind == "rfc":
        body = _clean_rfc(text)
    else:
        parser = _Blocks()
        parser.feed(text)
        blocks = parser.blocks
        if kind == "ldml":
            blocks = _number_headings(blocks)
        body = _render_blocks(_drop_sections(blocks))
    if not re.search(r"^\d+(?:\.\d+)* \S", body, re.M):
        raise SystemExit(f"У {source['id']} не знайдено жодного нумерованого заголовка — "
                         f"розмітка змінилася, читача треба поправити.")
    return f"# {title}\n# джерело: {url}\n# отримано: {ctx.stamp}\n\n{body}\n"


def _reader(kind: str):
    def read(source: dict, ctx) -> list[Item]:
        def make():
            return _document(source, kind, ctx)
        return [Item(id=source["id"], file=f"{source['id']}.txt", make=make)]
    return read


register("rfc")(_reader("rfc"))
register("report")(_reader("report"))
register("ldml")(_reader("ldml"))
