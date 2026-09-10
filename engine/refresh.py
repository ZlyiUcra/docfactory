"""Оновлювач корпусу: за оголошенням джерел завантажує документи в `corpus/`.

Тягне лише те, що дозволяє білий список примірника (`sources.allowed`). Наявні
файли не перезаписує без `--refresh`; `--refresh [id…]` перезаписує все або
назване. `--list` показує, що завантажилося б, нічого не пишучи. Один документ на
файл, окрім джерел, що розгортаються (`toc`, `page`): ті дають файл на главу.
"""

import time
import urllib.error

from engine import net
from engine import readers
from engine import sources as S


class Ctx:
    """Контекст читача: звернення крізь білий список примірника і дата прогону."""

    def __init__(self, sources, stamp: str):
        self._sources = sources
        self.stamp = stamp

    def _allow(self, url: str) -> bool:
        return S.allowed(url, self._sources)

    def bytes(self, url: str) -> bytes:
        code, data = net.fetch(url, self._allow)
        if code != "200":
            raise SystemExit(f"{url}: {code}")
        return data

    def text(self, url: str) -> str:
        return self.bytes(url).decode("utf-8", errors="replace")


def _wanted(item, source, targets, do_refresh) -> bool:
    if not do_refresh:
        return False
    if not targets:
        return True
    return item.id in targets or item.file in targets or source["id"] in targets


def refresh(instance_dir, targets: set, do_refresh: bool, listing: bool) -> int:
    data = S.load(instance_dir)
    src = data["sources"]
    corpus = instance_dir / "corpus"
    if not listing:
        corpus.mkdir(exist_ok=True)
    ctx = Ctx(src, time.strftime("%Y-%m-%d"))
    written = skipped = failed = 0

    for source in src:
        reader = readers.get(source["reader"])
        try:
            items = reader(source, ctx)
        except (net.Refused, SystemExit, urllib.error.URLError) as e:
            print(f"── {source['id']}: не вдалось скласти перелік: {e}")
            failed += 1
            continue
        print(f"── {source['id']} ({source['reader']}): {len(items)} документів ──")
        for it in items:
            path = corpus / it.file
            if listing:
                print(f"  {it.file}")
                continue
            if path.exists() and not _wanted(it, source, targets, do_refresh):
                size = len(path.read_text(encoding="utf-8"))
                print(f"  {it.file}  уже є, {size} символів")
                skipped += 1
                continue
            try:
                text = it.make()
            except (net.Refused, SystemExit, urllib.error.URLError) as e:
                print(f"  {it.file}  збій: {e}")
                failed += 1
                continue
            # Запис через тимчасовий файл із перейменуванням: невдалий запис
            # (повний диск, права, обрив) лишає попередній документ цілим —
            # корпус тут єдина копія, попередньої версії ніде немає. А OSError
            # валить один документ і рахується збоєм, не обриває весь прогін.
            tmp = path.with_name(path.name + ".tmp")
            try:
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(path)
            except OSError as e:
                print(f"  {it.file}  запис не вдався: {e}")
                failed += 1
                tmp.unlink(missing_ok=True)
                continue
            print(f"  {it.file}  {len(text)} символів")
            written += 1
            time.sleep(net.PAUSE_SEC)

    if listing:
        print("── Лише перелік; нічого не записано ──")
    else:
        print(f"── Готово: записано {written}, лишено як є {skipped}, збоїв {failed} ──")
    return 1 if failed else 0
