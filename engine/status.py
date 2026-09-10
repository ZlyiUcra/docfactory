"""Перевірка «що змінилося нагорі, за джерелом». Нічого не пише.

Питає кожне джерело з `sources.json` і порівнює з тим, що вже лежить у `corpus/`:
чи з'явилися нові документи, чи якісь зникли (сироти), і — де це дешево — чи не
змінився вміст. Вміст рахується сумою тексту після розбору, тож зміна даты
завантаження за зміну не рахується.

Де дешево:
- ECMA-402 приходить однією сторінкою — розділи й їхній вміст коштують одне
  звернення, тож зміст звіряється завжди;
- вільні документи й PDF питаються умовним запитом «чи змінилося після нашої
  дати» (дата — з шапки нашого ж документа): сервер відповідає «304» без тіла;
- ECMA-262 дешево не звіряється: tc39 перезбирає видання на кожен запит, тож
  єдиний чесний сигнал — сума тексту, а для неї главу треба завантажити. Це
  робить `--deep`, доокремлюючи кожну главу.
"""

import re
import time
import urllib.error

from engine import manifest as M
from engine import net
from engine import readers
from engine import sources as S
from engine.refresh import Ctx

_P402 = re.compile(r"^402-\d+-")
_POS = re.compile(r"^\d+-")


def _slug_of(name: str) -> str:
    """Хвіст імені файла без позиції — щоб упізнати перейменування при зсуві."""
    stem = name[:-4] if name.endswith(".txt") else name
    if _P402.match(stem):
        return stem.split("-", 2)[2]
    if _POS.match(stem):
        return stem.split("-", 1)[1]
    return stem


def _base_sha(path, base) -> str:
    """Сума-взірець: із паспорта, якщо він є; інакше порахована з файла."""
    rec = base.get(path.name)
    if rec and rec.get("sha256"):
        return rec["sha256"]
    return M.digest(M.body(path))


def _changed(path, fresh_text, base) -> bool:
    return M.digest(M.body_of(fresh_text)) != _base_sha(path, base)


def status(instance_dir, deep: bool) -> int:
    data = S.load(instance_dir)
    src = data["sources"]
    corpus = instance_dir / "corpus"
    ctx = Ctx(src, time.strftime("%Y-%m-%d"))
    allow = lambda u: S.allowed(u, src)  # noqa: E731
    base = M.by_file(M.load(corpus))
    print(f"── Звірка «{data.get('instance', '?')}» "
          f"({'за паспортом index.json' if base else 'за файлами corpus/'}) ──")

    on_disk = sorted(p.name for p in corpus.glob("*.txt"))
    expected: set = set()
    failed_sources: set = set()
    new_files: list = []
    new = changed = same = unch_262 = failed = 0

    for source in src:
        rn = source["reader"]
        print(f"── {source['id']} ({rn}) · {source['url']} ──")
        try:
            items = readers.get(rn)(source, ctx)
        except (net.Refused, SystemExit, urllib.error.URLError) as e:
            print(f"  не вдалось спитати джерело: {e}")
            failed += 1
            failed_sources.add(source["id"])
            continue
        for it in items:
            expected.add(it.file)
            path = corpus / it.file
            if not path.exists():
                print(f"  + новий: {it.file}")
                new += 1
                new_files.append(it.file)
                continue
            try:
                if rn == "page":                         # 402: зміст уже в руках
                    if _changed(path, it.make(), base):
                        print(f"  ~ змінився: {it.file}")
                        changed += 1
                    else:
                        same += 1
                elif rn == "toc":                        # 262: тільки --deep
                    if not deep:
                        unch_262 += 1
                        continue
                    if _changed(path, it.make(), base):
                        print(f"  ~ змінився: {it.file}")
                        changed += 1
                    else:
                        same += 1
                    time.sleep(net.PAUSE_SEC)
                else:                                    # pdf/rfc/report/ldml
                    since = base.get(it.file, {}).get("fetched") or M.header(path)["fetched"]
                    code, _ = net.fetch(source["url"], allow, since=since)
                    if code == "304":
                        same += 1
                    elif code == "200":
                        if deep and _changed(path, it.make(), base):
                            print(f"  ~ змінився: {it.file}")
                            changed += 1
                        elif deep:
                            same += 1
                        else:
                            print(f"  ~ новіше за нашу дату: {it.file} "
                                  f"(звірити тіло: --deep)")
                            changed += 1
                    else:
                        print(f"  збій: {it.file}: {code}")
                        failed += 1
                    time.sleep(net.PAUSE_SEC)
            except (net.Refused, SystemExit, urllib.error.URLError) as e:
                print(f"  збій розбору: {it.file}: {e}")
                failed += 1

    # Джерело, якого не вдалося спитати, не має думки про свої файли: без його
    # переліку «немає нагорі» не відрізнити від «не дізналися». Тому файли
    # відмовленого джерела — не сироти, а «невідомо», без запрошення діяти:
    # за поганим списком читач видалив би здорові документи, а другої копії
    # корпус не тримає. Кому належить файл, каже manifest.classify.
    orphans, unknown = [], []
    for n in on_disk:
        if n in expected:
            continue
        _, owner = M.classify(n, src)
        if owner in failed_sources or (not owner and failed_sources):
            unknown.append(n)
        else:
            orphans.append(n)

    if unknown:
        print("── Невідомо: джерело не відповіло і про ці файли думки не має ──")
        for n in unknown:
            print(f"  {n}")
        print("  це не сироти — нічого з ними не робіть; повторіть звірку, "
              "коли джерело відповість")
    if orphans:
        print("── Сироти: є в corpus/, немає нагорі за джерелом ──")
        for n in orphans:
            twin = next((nf for nf in new_files
                         if _slug_of(nf) == _slug_of(n) and nf != n), None)
            hint = f"  ← ймовірно зсув: те саме, що новий {twin}" if twin else ""
            print(f"  {n}{hint}")
        print("  нічого не видалено — що з ними робити, вирішувати вам")

    tail = f", 262 без глибокої звірки {unch_262}" if unch_262 else ""
    summary = (f"── Без змін {same}, змінилося {changed}, нових {new}, "
               f"сиріт {len(orphans)}")
    if unknown:
        summary += f", невідомих {len(unknown)}"
    print(summary + f", збоїв {failed}{tail}. Не записано нічого ──")
    return 1 if failed else 0
