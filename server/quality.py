"""
ОСНОВА · чи додає пошук за змістом те, чого не давав пошук по словах.

Це замір, а не перевірка: він нічого не провалює і ні на що не скаржиться. Він
відповідає на одне питання, на яке інакше довелося б відповідати відчуттям — чи
варті сорок хвилин рахунку векторів того, що вони дають на запитах.

ЯК ВІН МІРЯЄ

Нижче десять запитів, і для кожного наперед названо номер розділу, який мав би
знайтися. Кожен запит проганяється трьома способами — самим пошуком по словах,
самим пошуком за змістом і їхнім злиттям, тим самим, яким користується сервер, —
і рахується, чи потрапив потрібний розділ у перші k.

ЩО ЦЕ ЧИСЛО ОЗНАЧАЄ І ЧОГО НЕ ОЗНАЧАЄ

Запити й очікувані розділи дібрані руками, тож «4 з 10» саме по собі не є оцінкою
пошуку: інша десятка дала б інше число. Сенс має тільки порівняння трьох стовпців
між собою на одній і тій самій десятці — вони бачать однакові запити й однакові
документи. Якщо злиття дає більше за кожен зі способів окремо, воно робить те,
заради чого його взяли; якщо не дає, вектори на цих документах не окупаються.

Половина запитів навмисне перефразована так, щоб жодне слово не збігалося з
текстом розділу — саме на них пошук по словах безсилий за побудовою. Друга
половина написана словами специфікації, але без її ідентифікаторів.

    python -m server.quality           # $0, десятки секунд
    python -m server.quality --show    # ще й що саме знайшов кожен спосіб

Без піднятого Qdrant міряти нема чого: буде виміряний лише пошук по словах, і
скрипт про це скаже.
"""

import sys
import time

# (запит, номер розділу, який має знайтися)
CASES = [
    # Перефразування без жодного слова з потрібного розділу.
    ("how to find out the type of a value", "20.1.3.6"),
    ("make an object impossible to change", "20.1.2.6"),
    ("check whether two values are the same", "7.2.9"),
    ("round a number down to a whole number", "21.3.2.29"),
    ("hide a property so loops do not list it", "6.1.7.1"),
    # Питання словами специфікації, але не її ідентифікаторами.
    ("what does the spread operator do to an array", "13.2.4"),
    ("how are template literals evaluated", "13.3.11"),
    ("rules for comparing a string with a number", "7.2.15"),
    ("what a getter returns when the property is missing", "10.1.8"),
    ("how a locale identifier is canonicalized", "9.2.1"),
]

K = 5
WARMUP_SEC = 90


def wait_for_vectors(spec_mcp) -> bool:
    """Чекає, поки фонова нитка сервера прогріє пошук за змістом.

    Сервер прогріває модель уже після того, як відповів клієнтові, і для клієнта
    це правильно. Але замір, який спитає готовність одразу після імпорту, завжди
    міряв би самий пошук по словах. Тут чекати можна: це не сервер.
    """
    if not spec_mcp._VECTORS_ASKED:
        return False
    if not spec_mcp._VECTORS_READY:
        print(f"чекаю прогріву пошуку за змістом (до {WARMUP_SEC} с)...")
        deadline = time.time() + WARMUP_SEC
        while (time.time() < deadline and not spec_mcp._VECTORS_READY
               and not spec_mcp._VECTORS_WHY):
            time.sleep(1)
    return spec_mcp._VECTORS_READY


def main(argv: list[str]) -> int:
    show = "--show" in argv

    from server import spec_mcp
    from common import embed, nform, vectorstore
    from common.corpus import DOC_SET

    ready = wait_for_vectors(spec_mcp)
    total = len(spec_mcp._INDEX.passages)
    print(f"набір «{DOC_SET}», {total} "
          f"{nform(total, 'фрагмент', 'фрагменти', 'фрагментів')}, k={K}")
    if ready:
        points = vectorstore.count()
        print(f"пошук за змістом: {embed.MODEL_NAME}, {points} "
              f"{nform(points, 'точка', 'точки', 'точок')} "
              f"у {vectorstore.COLLECTION}\n")
    else:
        print(f"пошук за змістом недоступний ({spec_mcp._VECTORS_WHY or '—'}); "
              f"міряю лише пошук по словах\n")

    ways = ["по словах"] + (["за змістом", "разом"] if ready else [])
    score = dict.fromkeys(ways, 0)
    t_words = t_meaning = 0.0

    for query, want in CASES:
        t0 = time.perf_counter()
        words = spec_mcp._INDEX.retrieve(query, K)
        t_words += time.perf_counter() - t0
        found = {"по словах": words}

        if ready:
            t0 = time.perf_counter()
            hits = vectorstore.search(embed.embed_query(query), K)
            t_meaning += time.perf_counter() - t0
            meaning = [spec_mcp._BY_ID[h["uid"]] for h in hits
                       if h.get("uid") in spec_mcp._BY_ID]
            found["за змістом"] = meaning
            found["разом"] = spec_mcp._rrf([words, meaning], K)

        marks = []
        for way in ways:
            hit = any(p.section.startswith(want) for p in found[way])
            score[way] += hit
            marks.append(f"{way} {'+' if hit else '-'}")
        print(f"· {query}\n    треба {want:<11} {'   '.join(marks)}")
        if show:
            for way in ways:
                names = ", ".join(p.section for p in found[way])
                print(f"      {way:<11} {names}")

    n = len(CASES)
    print()
    for way in ways:
        print(f"{way:<11} {score[way]} із {n}")
    print(f"\nчас на запит: по словах {t_words / n * 1000:.0f} мс"
          + (f", за змістом {t_meaning / n * 1000:.0f} мс" if ready else ""))
    if ready:
        best = max(score["по словах"], score["за змістом"])
        print("злиття дає більше за кожен спосіб окремо"
              if score["разом"] > best else
              "злиття не дало більше за кращий зі способів")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
