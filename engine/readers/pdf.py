"""Читач `pdf` — стандарти, які Ecma видає лише як PDF (ECMA-404, ECMA-414).

Текст дістає pypdf. У витягнутому тексті трапляються розриви слів («inter change»,
«abou t») — так розкладено літери у самому файлі, і читач цього не лагодить, щоб
не вигадувати слова. Колонтитули, номери сторінок, зміст із крапками і сторінка з
ліцензією прибираються; вступ лишається перед першим розділом. Код перенесено з
колишнього завантажувача практики без змін по суті.
"""

import io
import re

from engine.readers import Item, register

_HEADING = re.compile(r"^\d+(?:\.\d+)*\s+\S")
_PAGE_NUMBER = re.compile(r"^\s*(\d+|[ivx]+)\s*$")


def _pdf_text(data: bytes) -> list[str]:
    try:
        import pypdf
    except ImportError:
        raise SystemExit("Потрібен pypdf: .venv/bin/pip install pypdf")
    reader = pypdf.PdfReader(io.BytesIO(data))
    lines = []
    for page in reader.pages:
        lines.extend((page.extract_text() or "").splitlines())
    return lines


def _keep(line: str) -> bool:
    s = line.strip()
    if not s or "Ecma International" in s and ("©" in s or "©" in line):
        return False
    if _PAGE_NUMBER.match(s) or "......" in s:
        return False
    return True


def _clean_pdf(lines: list[str]) -> str:
    """Вступ плюс тіло від останнього «1 Scope»; колонтитули і зміст геть."""
    lines = [re.sub(r"[ \t]+", " ", ln).rstrip() for ln in lines]
    starts = [i for i, ln in enumerate(lines) if re.match(r"^1 Scope\s*$", ln)]
    if not starts:
        raise SystemExit("У PDF не знайдено розділу «1 Scope» — текст видобуто не так, як очікувалося.")
    body = lines[starts[-1]:]
    intro = []
    intro_at = [i for i, ln in enumerate(lines[:starts[-1]]) if ln.strip() == "Introduction"]
    if intro_at:
        for ln in lines[intro_at[0] + 1:starts[-1]]:
            if ln.strip().upper().startswith("COPYRIGHT") or ln.lstrip().startswith("©") \
                    or "......" in ln or ln.strip() == "Contents":
                break
            intro.append(ln)
    out = []
    for ln in intro + body:
        if not _keep(ln):
            continue
        s = ln.strip()
        if _HEADING.match(s):
            out.append("")
            out.append(s)
            out.append("")
            continue
        # Кінець речення і наступний рядок з великої літери — межа абзацу.
        if out and out[-1] and re.search(r"[.:;]$", out[-1]) and s[:1].isupper():
            out.append("")
        out.append(s)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def _document_pdf(data: bytes, title: str, url: str, stamp: str) -> str:
    body = _clean_pdf(_pdf_text(data))
    return f"# {title}\n# джерело: {url}\n# отримано: {stamp}\n\n{body}\n"


@register("pdf")
def pdf(source: dict, ctx) -> list[Item]:
    url = source["url"]
    title = source.get("title", source["id"])

    def make():
        return _document_pdf(ctx.bytes(url), title, url, ctx.stamp)

    return [Item(id=source["id"], file=f"{source['id']}.txt", make=make)]
