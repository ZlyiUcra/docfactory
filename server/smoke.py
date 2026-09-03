"""
ОСНОВА · безкоштовні перевірки сервера повз протокол. Ані моделей, ані мережі.

Тут інструменти викликаються як звичайні функції Python: перевіряється те, що
сервер віддає, а не те, як він це загортає в JSON-RPC. Протокол перевіряє сусідній
check.py, і розділяти ці дві перевірки варто — коли клієнт отримує дурницю, одразу
видно, у кому вона: у пошуку чи в обгортці.

Кожна перевірка друкує рядок ok/FAIL; будь-який FAIL завершує процес ненульовим
кодом.

    python -m server.smoke           # $0, секунди
"""

import sys
import time

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def skip(name: str, why: str) -> None:
    """Перевірка, для якої зараз немає умов. Провалом це не рахується: пошук за
    змістом потребує Docker, а сервер зобов'язаний працювати й без нього. Рядок
    друкується все одно, щоб з виводу було видно, чого саме не перевіряли."""
    print(f"  --    {name} — пропущено: {why}")


def main(argv: list[str]) -> int:
    from server import spec_mcp
    from common import nform

    search, read = spec_mcp.search_spec, spec_mcp.read_section

    # 1. Розділи на місці, і поділені вони тим самим кодом, що в модулі 4. Число
    # фрагментів зняте 1 вересня 2026 року на корпусі після оновлення
    # (./df ecmascript refresh), на тих самих файлах, що лежать у corpus/.
    from common.corpus import DOC_SET

    EXPECTED = 4168
    total = len(spec_mcp._INDEX.passages)
    check(f"індекс зібрано при завантаженні модуля (набір {DOC_SET})",
          total == EXPECTED,
          f"{total} {nform(total, 'фрагмент', 'фрагменти', 'фрагментів')}")
    check("кожен фрагмент доступний за своїм id", len(spec_mcp._BY_ID) == total)
    check("опис називає моделі, що саме завантажено",
          spec_mcp._LOADED in spec_mcp.TOOL_DESCRIPTIONS["search_spec"]
          and spec_mcp._LOADED in spec_mcp.TOOL_DESCRIPTIONS["read_section"],
          spec_mcp._LOADED[:60] + "...")

    # 2. Пошук знаходить те, що в розділах явно є, і кожен знайдений фрагмент
    # приходить із заповненими полями — саме за ними клієнт цитує джерело.
    # Перевіряти лише `found > 0` тут замало, і це видно на самому наборі suite:
    # по словах перший результат на цей запит — розділ 15.1.1 «Intl.Locale ( tag )»,
    # бо слово «tag» у ньому трапляється частіше, а потрібний 20.1.3.6 стоїть
    # другим. Тому перевіряється не кількість, а те, що потрібний розділ узагалі є
    # серед трьох перших.
    hits = search("Object.prototype.toString tag")
    found = hits.get("found", 0)
    ids = [p["id"] for p in hits.get("passages", [])]
    check("search_spec знаходить Object.prototype.toString",
          any("20.1.3.6" in i for i in ids),
          f"found={found}, перший {ids[0] if ids else '—'}")
    first = (hits.get("passages") or [{}])[0]
    check("у відповіді є id, section, document, text",
          all(first.get(f) for f in ("id", "section", "document", "text")),
          first.get("id", "—"))
    check("k керує кількістю", len(search("prototype", 5).get("passages", [])) == 5)

    # 3. Обрізка. Довший за межу фрагмент має прийти рівно 600 символів плюс три
    # крапки, і серед розділів мусить бути хоч один такий, інакше перевірка порожня.
    long_hits = search("string prototype replace searchValue replaceValue", 10)
    texts = [p["text"] for p in long_hits.get("passages", [])]
    check("жоден текст у відповіді пошуку не довший за 603 символи",
          texts and max(len(t) for t in texts) <= 603,
          f"найдовший {max(len(t) for t in texts) if texts else 0}")
    check("обрізаний текст позначено трьома крапками",
          any(t.endswith("...") for t in texts))

    # 4. Мова запиту. Запит ріжеться на слова виразом [a-z0-9_]+, тобто кирилиця
    # зникає ще до пошуку, і суто український запит не має жодного шансу — це не
    # «погано шукає», це порожній вхід. Опис search_spec каже про це моделі прямо,
    # і саме тому перевіряється тут: якщо розділи колись заміняться іншими, разом
    # із перевіркою доведеться правити й опис.
    from common.lexical import tokenize

    ua = "Як працює перехоплення читання властивості"
    check("кирилиця дає нуль токенів", tokenize(ua) == [])
    check("суто український запит повертає found=0", search(ua).get("found") == 0)
    mixed = "Що каже специфікація про Object.prototype.toString?"
    check("український запит із латинським ідентифікатором працює",
          search(mixed).get("found", 0) > 0, str(tokenize(mixed)))
    check("опис search_spec попереджає про мову запиту",
          "in English" in (search.__doc__ or ""))
    check("опис search_spec каже, як писати українську відповідь",
          all(s in (search.__doc__ or "")
              for s in ("Answer in the language", "розділ", "Never call this set")))

    # 5. Межі k. Виняток тут був би гіршим за словник: клієнт побачив би збій
    # інструмента замість пояснення, що саме не так із аргументом.
    check("k=0 дає error", "error" in search("object", 0))
    check("k=11 дає error", "error" in search("object", 11))
    check("k=1 і k=10 проходять",
          "error" not in search("object", 1) and "error" not in search("object", 10))

    # 6. read_section віддає повний текст того самого фрагмента, а не свій.
    pid = first.get("id", "")
    full = read(pid)
    origin = spec_mcp._BY_ID.get(pid)
    check("read_section повертає текст розділу дослівно",
          origin is not None and full.get("text") == origin.text,
          f"{len(full.get('text', ''))} симв.")
    # Адреса джерела. У наборах core і full усе приходить з ecma262; у suite поруч
    # лежать ECMA-402, 404, 414 і вільні документи довкола 402, тому там перевіряємо
    # лише те, що адреса взагалі є і вона https.
    url = str(full.get("url", ""))
    expected = "https://" if DOC_SET == "suite" else "https://tc39.es/ecma262/"
    check("read_section дає посилання на джерело", url.startswith(expected), url)
    check("повний текст не коротший за обрізаний",
          len(full.get("text", "")) >= len(first.get("text", "")))

    # 6a. Приклад ідентифікатора в описі read_section має існувати насправді.
    # Опис — це інструкція для моделі, і приклад із нього вона копіює дослівно;
    # неіснуючий приклад навчає її кликати інструмент так, як він не працює.
    # Перевірка не декоративна: приклад справді бував неправильним — назви файлів
    # мінялися, а приклад в описі за ними не встигав.
    import re

    example = re.search(r'Example identifier: "([^"]+)"',
                        spec_mcp.TOOL_DESCRIPTIONS["read_section"])
    check("приклад id в описі read_section справді існує",
          bool(example) and example.group(1) in spec_mcp._BY_ID,
          example.group(1) if example else "прикладу в описі немає")

    # 7. Вигаданий ідентифікатор. Підказка в помилці важить не менше за саму
    # помилку: без неї модель починає гадати id далі.
    bad = read("22.1.3.19")
    check("вигаданий id дає error", "error" in bad)
    check("до помилки додано підказку, звідки брати id", bool(bad.get("hint")))

    # 8. Описи інструментів — головна робота цього завдання, тож перевіряється і
    # те, що вони взагалі доїхали до сервера, і те, що вони не однорядкові.
    for name, fn in (("search_spec", search), ("read_section", read)):
        doc = (fn.__doc__ or "").strip()
        check(f"{name}: опис довший за один рядок", len(doc) > 400,
              f"{len(doc)} симв.")
        check(f"{name}: опис каже, коли НЕ викликати", "Do not " in doc)

    # 9. Пошук за змістом. Перевіряється насамперед те, що він нікого не тримає в
    # заручниках: без Qdrant сервер мусить відповідати по словах, а не падати й не
    # чекати. Тому основна частина цього розділу працює без контейнера і без
    # моделі, а живий запит іде тільки тоді, коли база справді заповнена.
    #
    # Спершу — очікування. Сервер піднімає контейнер і прогріває модель у фоні, і
    # для клієнта це правильно: він відповідає одразу, поки що по словах. Але
    # перевірка, яка спитає готовність через мілісекунду після імпорту, завжди
    # побачить «ще ні» і мовчки пропустить головне. Тут чекати можна — це не
    # сервер, а перевірка.
    if spec_mcp._VECTORS_ASKED and not spec_mcp._VECTORS_READY:
        print("  ..    чекаю прогріву пошуку за змістом (до 90 с)")
        deadline = time.time() + 90
        while (time.time() < deadline and not spec_mcp._VECTORS_READY
               and not spec_mcp._VECTORS_WHY):
            time.sleep(1)
    # Злиття за взаємним рангом на трьох вигаданих фрагментах: b другий в обох
    # списках, a і c — перші, але кожен лише в одному. Спільний має виграти,
    # інакше злиття не робить того, заради чого його взяли.
    a, b, c = spec_mcp._INDEX.passages[:3]
    check("злиття двох списків підіймає спільний фрагмент",
          spec_mcp._rrf([[a, b], [c, b]], 3)[0] is b)
    if spec_mcp._VECTORS_READY:
        skip("без готових векторів пошук іде по словах", "вектори готові")
    else:
        check("без готових векторів пошук іде по словах",
              spec_mcp._find("object", 3)[1] == "words")
    check("відповідь пошуку каже, яким способом знайдено",
          search("object", 1).get("search") in {"words", "meaning+words"},
          str(search("object", 1).get("search")))
    empty = search(ua, 1)          # кирилиця — єдиний надійно порожній запит
    check("порожня відповідь теж каже спосіб",
          empty.get("found") == 0 and "search" in empty)
    check("опис search_spec пояснює моделі поле search",
          '"meaning+words"' in (search.__doc__ or ""))

    from common.mode import read as read_mode
    check("рішення про пошук читається з файла",
          read_mode().get("search") in {"words", "vectors"},
          str(read_mode().get("search")))

    if spec_mcp._VECTORS_READY:
        # Запит навмисне не називає жодного слова з потрібного розділу: по словах
        # такий не знаходить нічого схожого, тож «meaning+words» тут — доказ, що
        # відповів саме змістовий пошук.
        res = search("how to find out the type of a value", 5)
        check("живий пошук за змістом відповідає",
              res.get("search") == "meaning+words", str(res.get("search")))
    else:
        skip("живий пошук за змістом",
             spec_mcp._VECTORS_WHY or "не дочекався прогріву")

    # 10. Стик сервера і шару 2. Ця перевірка не про сервер і не про шар окремо:
    # обидва працюють, а розходяться вони мовчки. Шар 2 дозволяє read_section
    # лише на id з попередньої видачі search_spec, тож він читає відповідь
    # пошуку — і мусить читати її тим самим ключем, яким сервер її кладе.
    # Розійшлися саме тут: код шарів приїхав з модуля 6, де сервер повертав
    # "hits", а сервер фабрики повертає "passages". Наслідок був невидимий у
    # жодній іншій перевірці — сервер відповідав правильно, шар працював
    # правильно, і при цьому агент не міг дочитати жодного розділу.
    from server import layers

    sess = layers.Session()
    sess.remember(search("object", 3))
    check("шар 2 бачить id з видачі search_spec", len(sess.known_ids) == 3,
          f"{len(sess.known_ids)} id")
    seen = next(iter(sess.known_ids), "")
    check("read_section на id з видачі дозволено",
          layers.deny_before("read_section", {"id": seen}, sess) is None, seen)
    check("read_section на id, якого не показував пошук, відхиляється",
          layers.deny_before("read_section", {"id": "99.9.9.9"},
                             layers.Session()) is not None)

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО: {len(FAILED)} — " + "; ".join(FAILED))
        return 1
    print("Усі перевірки пройдено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
