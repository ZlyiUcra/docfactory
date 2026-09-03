"""
ОСНОВА · підготовка сервера: одне питання, і далі все автоматично.

Сервер уміє шукати двома способами. По словах — завжди: усе потрібне лежить
текстовими файлами поруч, ставити нічого не треба. За змістом — коли поруч
працює Qdrant: тоді запит «how to find out the type of a value» має шанс
привести до Object.prototype.toString, у якому цих слів немає.

Другий спосіб потребує Docker, бо Qdrant працює окремим процесом у контейнері.
Це єдине рішення, яке за людину ухвалити не можна, тому воно ставиться питанням
рівно один раз — тут. Відповідь лягає у файл `out/mode.json`, і далі
сервер лише читає її: нічого не питає, нічого не встановлює без дозволу і
нічого не вимагає від того, хто потім користується інструментами в Claude Code.

Саме питання і його підказки написані англійською, а відповідь — одна літера:
`y` піднімає, `n` лишає пошук по словах, `q` виходить, не змінивши нічого.
Типова відповідь — `n`, і порожній рядок означає саме її; на це вказує велика
літера в `[y/N/q]`. Будь-яка інша відповідь не вгадується, а перепитується.

    python -m server.setup              # спитати і зробити
    python -m server.setup --vectors    # без питань: з Qdrant
    python -m server.setup --no-vectors # без питань: лише пошук по словах
    python -m server.setup --stop       # вимкнути і зупинити контейнер
    python -m server.setup --status     # що вирішено і що зараз працює

`--no-vectors` лише записує рішення, `--stop` ще й зупиняє контейнер. Різниця
має значення: після `--no-vectors` контейнер працює далі, просто ним ніхто не
користується; після `--stop` він зупинений і ресурсів не їсть. Дані в обох
випадках лишаються — видалення тому робить людина, не ця команда.

Відмова від Qdrant нічого не ламає: сервер працюватиме по словах над найповнішим
набором документів, який є. Передумати можна будь-коли — просто запустіть цю
команду ще раз.

Рахунок векторів для повного набору триває хвилини, тому він іде пачками і друкує
поступ. Номер точки зроблено зі стійкого ідентифікатора фрагмента, а не з його
позиції у списку, і поряд із текстом лежить його сума: тому повторний запуск
рахує лише ті фрагменти, що додалися чи змінилися, незмінні не чіпає, і перервана
робота не пропадає. `--vectors --refill` рахує все наново, поверх наявних точок;
наявні точки при цьому не видаляються, а перезаписуються тими самими номерами.
"""

import hashlib
import sys
import time
import uuid

from common.mode import PATH as MODE_PATH, read as read_mode, write as write_mode


# Скільки триває рахунок вектора для одного фрагмента. Заміряно на цій машині:
# 256 справжніх фрагментів пачками по 32 за 163 секунди. Число тут потрібне лише
# для попередження людині — з нього рахується очікуваний час.
SEC_PER_PASSAGE = 0.635

# Латиниця, те саме при ввімкненій українській розкладці, і самі слова. Літери
# між розкладками не збігаються: «у» стоїть на клавіші «y» у фонетичній, «н» —
# на «n» у ній же і водночас на «y» у ЙЦУКЕН. «н» читається як «ні», бо так
# каже і слово, і фонетична розкладка, а помилка в цей бік нічого не встановлює.
# «т» не приймається зовсім: у ЙЦУКЕН це клавіша «n», а в слові «так» — перша
# літера, тобто два прочитання протилежні, і одне з них ставить контейнер.
YES = {"y", "yes", "у", "так"}
NO = {"n", "no", "н", "ні"}
QUIT = {"q", "quit", "й", "я", "вихід", "вийти"}


def _shown(answer: str) -> str:
    """Відповідь у лапках, а для не-латиниці ще й коди символів.

    Найчастіша причина невпізнаної відповіді — не помилка в літері, а розкладка:
    кирилична «у» в терміналі виглядає точно як латинська «y», і без коду цього
    не видно. Невидимі символи, що приїхали разом із вставленим рядком, так само
    стають видимі.
    """
    if answer.isascii():
        return f'"{answer}"'
    codes = " ".join(f"U+{ord(ch):04X}" for ch in answer)
    return f'"{answer}" ({codes})'


def _ask() -> bool | None:
    """Єдине питання цієї практики. Повертає True, False або None — «вийти».

    Ставиться воно один раз, і воно не риторичне: «y» означає, що на машині
    з'явиться контейнер, том і завантажена модель. Тому питання називає все, що
    буде зроблено, і скільки це триватиме, — згода без цього переліку згодою не
    є. «n» не є поразкою: сервер працює і без Qdrant, і про це сказано тут же,
    щоб відмова не виглядала відмовою від сервера. «q» — вихід без рішення: файл
    рішення лишається такий, який був, і жодного разу не переписується.

    Текст самого питання англійською на прохання власника, і відповідь — літера,
    а не слово: це єдине місце практики, де людина відповідає машині, і воно має
    читатися так само в будь-якому терміналі. Приймається і те, що дає та сама
    клавіша при ввімкненій українській розкладці; перелік і його межі описані
    коментарем біля YES, NO і QUIT. Велика «N» у [y/N/q] означає типову
    відповідь: порожній рядок — це «n», тобто не встановлювати нічого.
    """
    from common import embed, vectorstore
    from common.corpus import DOC_SET, load_passages

    passages = load_passages()
    n = len(passages)
    have_docker = vectorstore.docker_available()

    # Скільки лишилося рахувати насправді. Попередження про сорок хвилин там, де
    # база вже залита і робота займе секунди, — не обережність, а неправда, і
    # людина після одного такого перестає читати попередження взагалі. Рахунок
    # чесний: не «n мінус кількість точок» (стара колекція під іншою схемою
    # номерів дала б нуль, хоч рахувати треба все), а рівно нові плюс змінені.
    left, seen = n, False
    if vectorstore.alive() and vectorstore.collection_info() is not None:
        from common.idmap import assign_ids

        _, uid_of = assign_ids(passages)
        new, changed, _, _ = _plan(passages, uid_of)
        left, seen = len(new) + len(changed), True
    minutes = max(1, round(left * SEC_PER_PASSAGE / 60))
    if not left:
        cost = (f"Nothing to compute: all {n} are already in the database, so this\n"
                f"        only records the decision.")
    elif seen:
        cost = (f"Computes the {left} new or changed vectors (out of {n}): about\n"
                f"        {minutes} min on this machine. The rest is already in the\n"
                f"        database and is not recomputed.")
    else:
        cost = (f"Computes vectors for all {n} excerpts of the \"{DOC_SET}\" set and\n"
                f"        loads them into the database: about {minutes} min, once. An\n"
                f"        interrupted run is not lost -- the next one resumes where it\n"
                f"        stopped.")

    print(f"""
Meaning search needs Docker with Qdrant. Bring it up?

  y - this machine gets:
          * container {vectorstore.CONTAINER} from image {vectorstore.IMAGE}
            (the image is pulled if missing), ports 6333 and 6334;
          * named volume {vectorstore.VOLUME}, where the data will live;
          * model {embed.MODEL_NAME} (128 MB on disk) in ~/.cache/huggingface.
        {cost}
        Each query then costs about 70 ms.

  N - (default, also what an empty line means) nothing is installed and Docker
        is not touched. The server will search by words over the same set of
        documents: ready at once, needs no container, no model and no network.
        The price is that a query has to use the same words as the section you
        are looking for.

  q - quit without deciding. Nothing is written, nothing is started.

Nothing existing is ever deleted: not other collections in Qdrant, not the
volume, not the container. You can change your mind at any time by running this
same command with --vectors or --no-vectors.
""")
    if not have_docker:
        print("Docker does not answer on this machine right now, so \"y\" will stop\n"
              "at that and word search will be recorded instead. This is not a\n"
              "failure -- Docker is simply not installed or not running.\n")

    # Порожній рядок означає «n» — це те, що показує велика літера в [y/N/q], і
    # це безпечний бік: не встановити нічого. Мовчазним тлумаченням це не є, бо
    # мовчазним воно було б тоді, коли так само сприймалася б і друкарська
    # помилка; будь-яка інша відповідь нижче не вгадується, а перепитується.
    while True:
        answer = input("Bring up Docker and Qdrant? [y/N/q]: ").strip().lower()
        if not answer:
            return False
        if answer in YES:
            return True
        if answer in NO:
            return False
        if answer in QUIT:
            return None
        print(f"Unclear answer {_shown(answer)}. Please enter one of the three\n"
              "letters above: y to bring it up, n to stay with word search,\n"
              "q to quit.")


def status() -> int:
    from common import embed, nform, vectorstore
    from common.corpus import DOC_SET

    mode = read_mode()
    print(f"Файл рішення : {MODE_PATH}")
    print(f"Вирішено     : {mode.get('search')} (від {mode.get('decided', '—')})")
    print(f"Набір        : {DOC_SET}")
    print(f"Модель       : {embed.MODEL_NAME}, {embed.DIM} "
          f"{nform(embed.DIM, 'вимір', 'виміри', 'вимірів')}")
    print(f"Колекція     : {vectorstore.COLLECTION}")
    if vectorstore.alive():
        from common.corpus import load_passages
        from common.idmap import assign_ids

        info = vectorstore.collection_info()
        have = info["points_count"] if info else 0
        passages = load_passages()
        total = len(passages)
        print(f"Qdrant       : відповідає, точок {have} із {total}")
        if info is None:
            print("Оновлення    : колекції ще немає — залийте: --vectors")
        else:
            _, uid_of = assign_ids(passages)
            new, changed, unchanged, want_ids = _plan(passages, uid_of)
            orphans = _orphans(want_ids)
            print(f"Оновлення    : нових {len(new)}, змінених {len(changed)}, "
                  f"незмінних {len(unchanged)}, зайвих {len(orphans)}")
            if have and not unchanged and not changed:
                print("               жодну наявну точку не впізнано як поточний "
                      "фрагмент —")
                print("               стара схема номерів або інший корпус; "
                      "--vectors скаже, як перейти")
            elif not new and not changed:
                print("               усі вектори на місці й актуальні — "
                      "рахувати нічого")
    else:
        print(f"Qdrant       : не відповідає ({vectorstore.QDRANT_URL})")
    return 0


# Простір імен для стійких номерів точок. Стала назавжди: номер фрагмента мусить
# виходити той самий на будь-якій машині й у будь-якому прогоні, інакше «те саме»
# не впізнається. Саме значення довільне — важливо лише, щоб воно не мінялося.
_NAMESPACE = uuid.UUID("6d0d1f6e-2b8a-4a1e-9f3c-6ec0ffee6006")


def _stable_id(uid: str) -> str:
    """Стійкий номер точки з ідентифікатора фрагмента. Не залежить ні від позиції
    у списку, ні від машини — лише від самого uid."""
    return str(uuid.uuid5(_NAMESPACE, uid))


def _digest(text: str) -> str:
    """Сума тексту фрагмента. За нею відрізняємо змінений фрагмент від того, що
    лежить у базі без змін."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plan(passages, uid_of):
    """Розкладає фрагменти на три купи, не рахуючи жодного вектора: нові (номера
    немає в базі), змінені (номер є, сума інша) і незмінні (номер є, сума та
    сама). Повертає (new, changed, unchanged) — списки четвірок
    (фрагмент, uid, стійкий номер, сума) — і множину стійких номерів усіх
    поточних фрагментів.

    Номер точки — стійкий, зроблений з uid фрагмента, а не з його позиції у
    списку. Тому правка документа в середині набору не збиває номери решти:
    оновиться лише той фрагмент, чия сума змінилася, а колишня звірка «перші N
    точок — це перші N фрагментів» більше не потрібна.
    """
    from common import vectorstore

    want = [(p, uid_of[p], _stable_id(uid_of[p]), _digest(p.text)) for p in passages]
    want_ids = {sid for _, _, sid, _ in want}
    ids = [sid for _, _, sid, _ in want]
    stored: dict = {}
    for start in range(0, len(ids), vectorstore.BATCH):
        stored.update(vectorstore.fetch(ids[start:start + vectorstore.BATCH]))
    new, changed, unchanged = [], [], []
    for rec in want:
        _, _, sid, digest = rec
        payload = stored.get(sid)
        if payload is None:
            new.append(rec)
        elif payload.get("digest") != digest:
            changed.append(rec)
        else:
            unchanged.append(rec)
    return new, changed, unchanged, want_ids


def _orphans(want_ids: set) -> list:
    """Стійкі номери точок, що лежать у колекції, але яких немає серед поточних
    фрагментів: описують текст, який із документів зник. Лише читання."""
    from common import vectorstore

    return [sid for sid in vectorstore.all_ids() if sid not in want_ids]


def _fill(todo: list) -> int:
    """Рахує вектори і заливає їх пачками, друкуючи поступ.

    Пачками, а не одним махом, з двох причин. Перша: рахунок трьох із половиною
    тисяч фрагментів триває хвилини, і людина за терміналом мусить бачити, що
    робота йде, а не гадати, чи процес живий. Друга: пачка, яку вже прийняв
    Qdrant, лишається в колекції назавжди, тому перерваний Ctrl+C запуск не
    зникає в нікуди.

    `todo` — це список четвірок (фрагмент, uid, стійкий номер, сума) з _plan:
    рівно ті фрагменти, які треба порахувати (нові й змінені). Порядок не
    важливий — у кожної точки свій стійкий номер, тож перервати безпечно:
    наступний запуск побачить залите як «сума та сама» й продовжить із рештою.
    Нічого не видаляється: до наявних точок лише дописуються нові, а змінені
    перезаписуються на своїх номерах.
    """
    from common import embed, vectorstore

    total = len(todo)
    t0 = time.perf_counter()
    batch: list[dict] = []
    sent = 0

    # embed() віддає вектори по одному, у порядку тексту, тож накопичуємо пачку
    # і відправляємо її, не чекаючи, поки порахується весь набір.
    stream = embed.model().embed([p.text for p, _, _, _ in todo],
                                 batch_size=embed.BATCH)
    for offset, vector in enumerate(stream):
        p, uid, sid, digest = todo[offset]
        batch.append({
            "id": sid,
            "vector": vector.tolist(),
            "payload": {"uid": uid, "digest": digest, "pid": p.pid,
                        "doc_id": p.doc_id, "doc_title": p.doc_title,
                        "section": p.section, "label": p.label,
                        "url": p.url, "text": p.text},
        })
        if len(batch) == vectorstore.BATCH:
            sent += vectorstore.upsert(batch)
            batch = []
            spent = time.perf_counter() - t0
            left = spent / sent * (total - sent)
            print(f"  {sent}/{total}, минуло {spent:.0f} с, "
                  f"лишилося приблизно {left:.0f} с", flush=True)
    if batch:
        sent += vectorstore.upsert(batch)
    print(f"  пораховано і залито за {time.perf_counter() - t0:.0f} с")
    return sent


# Скільки триває підйом самого сервера. Обидва числа зняті на цій машині на
# наборі «suite»: збірка індексу при старті — близько п'яти секунд, прогрів
# пошуку за змістом у фоновій нитці — ще близько семи після того, як сервер уже
# відповідає (у журналі це видно як проміжок між першим викликом і рядком «пошук
# за змістом готовий»).
START_SEC = 5
WARMUP_SEC_AFTER_START = 7


def _report(spent: float | None = None) -> None:
    """Звіт після рішення: що записано, у якому стані сервер і що робити далі.

    Це не прикраса. Людина щойно відповіла на єдине питання практики і має піти
    з відповіддю на своє власне — чи можна вже піднімати MCP-сервер, чи ще ні, і
    скільки він підійматиметься. Два рядки «записано, Docker не потрібен» на це
    не відповідають.

    Українською, бо діалог скінчився на відповіді: англійською тут лише саме
    питання і його підказки.
    """
    from common import embed, nform, vectorstore
    from common.corpus import DOC_SET, load_passages

    mode = read_mode()
    vectors = mode.get("search") == "vectors"
    passages = load_passages()
    total = len(passages)
    docs = len({p.doc_id for p in passages})

    print()
    print("Записано" + (":" if not vectors else " — пошук за змістом і по словах разом:"))
    print(f"  файл рішення : {MODE_PATH}")
    print(f"  спосіб пошуку: " + ("за змістом і по словах разом (RRF)" if vectors
                                  else "лише по словах (BM25)"))
    print(f"  набір        : «{DOC_SET}» — {total} "
          f"{nform(total, 'фрагмент', 'фрагменти', 'фрагментів')} із {docs} "
          f"{nform(docs, 'документа', 'документів', 'документів')}")
    if vectors:
        points = vectorstore.count() if vectorstore.alive() else 0
        print(f"  модель       : {embed.MODEL_NAME}, {embed.DIM} "
              f"{nform(embed.DIM, 'вимір', 'виміри', 'вимірів')}")
        print(f"  колекція     : {vectorstore.COLLECTION} — {points} "
              f"{nform(points, 'точка', 'точки', 'точок')}")
        print(f"  контейнер    : {vectorstore.CONTAINER} на {vectorstore.QDRANT_URL}")
    else:
        print(f"  Docker       : не потрібен, контейнер не піднімається")
    # Рядок про час доречний лише тоді, коли час справді витрачено. «0 с» після
    # запуску, який нічого не рахував, — зайвий рядок, а не звіт.
    if spent is not None and spent >= 1:
        print(f"  витрачено    : {spent:.0f} с на цю підготовку")

    print()
    print("MCP-сервер можна піднімати — точніше, піднімати його руками й не треба:")
    print("його запускає сам клієнт (Claude Code, Inspector), коли до нього звертається.")
    print(f"Від запуску до першої відповіді — близько {START_SEC} с: стільки збирається")
    print("індекс по словах.")
    if vectors:
        print(f"Ще близько {WARMUP_SEC_AFTER_START} с у фоні йде прогрів пошуку за "
              f"змістом — контейнер і модель.")
        print("Ці секунди нікого не тримають: сервер уже відповідає, поки що по словах,")
        print("а поле `search` у кожній відповіді каже, який спосіб відпрацював.")
    print()
    print("Далі:")
    print("  .venv/bin/python -m server.smoke     перевірки, ~15 с")
    print("  .venv/bin/python -m server.check     діалог по протоколу, ~10 с")
    if vectors:
        print("  .venv/bin/python -m server.quality   що дає пошук за змістом, ~30 с")
    print("  .venv/bin/python -m server.setup --status   стан у будь-який момент")
    print()
    print("Передумати: та сама команда з "
          + ("--no-vectors." if vectors else "--vectors."))


def enable_vectors(refill: bool = False) -> int:
    """Піднімає Qdrant, заливає фрагменти і записує рішення."""
    from common import embed, nform, vectorstore
    from common.corpus import DOC_SET, load_passages
    from common.idmap import assign_ids

    started = time.perf_counter()

    if not vectorstore.docker_available():
        print("Docker на цій машині не відповідає. Пошук за змістом без нього не\n"
              "працює, тому лишаю пошук по словах — сервер від цього не постраждає.")
        write_mode({"search": "words", "why": "docker недоступний"})
        _report()
        return 1

    print(f"Піднімаю Qdrant ({vectorstore.CONTAINER})...")
    if not vectorstore.ensure_running():
        print("Контейнер не піднявся. Лишаю пошук по словах.")
        write_mode({"search": "words", "why": "контейнер не піднявся"})
        _report()
        return 1
    print(f"  Qdrant відповідає: {vectorstore.QDRANT_URL}")

    passages = load_passages()
    _, uid_of = assign_ids(passages)
    n = len(passages)
    print(f"  фрагментів у наборі «{DOC_SET}»: {n}")

    created = vectorstore.ensure_collection(embed.DIM)
    print(f"  колекція {vectorstore.COLLECTION}: "
          f"{'створена' if created else 'уже була'}")

    new, changed, unchanged, want_ids = _plan(passages, uid_of)
    have = vectorstore.count()

    # Жоден поточний фрагмент не впізнано, а точки в колекції є: або вона під
    # старою схемою номерів (номер-позиція з попередньої версії практики), або в
    # ній зовсім інший корпус. Доливати не можна — старі точки лишилися б поряд
    # дублями. Знести й залити наново вирішує людина.
    if have and not refill and not unchanged and not changed:
        print(f"\nУ колекції {have} точок, але жодну не впізнано як поточний "
              f"фрагмент.\nСхоже, це стара схема номерів або інший корпус. Доливання "
              f"лишило б\nстарі точки поряд дублями, тому я його не роблю. Перехід — "
              f"через\nодноразове перезаливання наново (видаляєте колекцію ви самі):\n"
              f"  curl -X DELETE {vectorstore.QDRANT_URL}/collections/"
              f"{vectorstore.COLLECTION}\n"
              f"  python -m server.setup --vectors")
        return 1

    todo = (new + changed + unchanged) if refill else (new + changed)
    if refill:
        print(f"  --refill: перераховую всі {n}")
    else:
        print(f"  нових {len(new)}, змінених {len(changed)}, "
              f"незмінних {len(unchanged)} — рахую {len(todo)}")

    if not todo:
        print(f"  у колекції вже всі {n} "
              f"{nform(n, 'точка', 'точки', 'точок')} і вони актуальні — "
              f"заливати нічого")
    else:
        print(f"  рахую вектори моделлю {embed.MODEL_NAME} "
              f"(перший запуск ще й довантажує її)...")
        sent = _fill(todo)
        print(f"  залито точок: {sent}, у колекції тепер {vectorstore.count()}")

    orphans = _orphans(want_ids)
    if orphans:
        m = len(orphans)
        print(f"\n  {m} {nform(m, 'точка', 'точки', 'точок')} описують текст,\n"
              f"  якого в документах уже немає. Пошук їх не показує (їхній фрагмент\n"
              f"  не входить у набір). Прибрати фізично можна лише знесенням\n"
              f"  колекції — робите це ви самі:\n"
              f"    curl -X DELETE {vectorstore.QDRANT_URL}/collections/"
              f"{vectorstore.COLLECTION}\n"
              f"    python -m server.setup --vectors")

    write_mode({"search": "vectors", "docs": DOC_SET, "model": embed.MODEL_KEY,
                "collection": vectorstore.COLLECTION, "points": vectorstore.count()})
    _report(spent=time.perf_counter() - started)
    return 0


def _offer_stop() -> None:
    """Друге питання, і лише тоді, коли є про що питати: гасити контейнер чи ні.

    Ставиться після того, як рішення вже записане. Порядок той самий, що в
    --stop, і з тієї ж причини: зупинити базу, не змінивши рішення, — марна
    праця, бо сервер прочитає з файла «vectors» і підніме її назад.

    Сама зупинка не робиться мовчки, і причина конкретна. Контейнер тут
    спільний: ім'я agent0826-qdrant однакове в модулях 2-5, у ньому лежать
    колекції всіх чотирьох, а цього модуля стосується одна з п'яти. Вибір,
    зроблений тут, не має тихо забирати базу в сусідніх модулів — тому текст
    питання це й називає, а типова відповідь «ні» лишає чуже недоторканим.

    У прогоні без термінала питати нема кого, тож там друкується команда.
    """
    from common import vectorstore

    if not vectorstore.alive():
        return

    print(f'\nQdrant is running in container "{vectorstore.CONTAINER}".\n'
          "It is shared: modules 2, 3 and 4 of this course keep their own\n"
          "collections in the same container, and only one of the collections\n"
          "there belongs to this module. Stopping it takes the database away\n"
          "from them as well -- they fall back to searching the documents on\n"
          "disk and say so in their output.\n"
          "Nothing is deleted either way: the volume and every collection stay,\n"
          "and starting it again costs seconds, not a recount of vectors.\n")

    if not sys.stdin.isatty():
        print("Not a terminal, so nothing is stopped. Щоб зупинити самому:\n"
              f"  docker stop {vectorstore.CONTAINER}")
        return

    while True:
        answer = input("Stop the container to free its memory? [y/N]: ").strip().lower()
        if not answer or answer in NO:
            print("Контейнер лишено працювати. Зупинити пізніше:\n"
                  "  python -m server.setup --stop")
            return
        if answer in YES:
            break
        print(f"Unclear answer {_shown(answer)}. Enter y to stop the container,\n"
              "n to leave it running.")

    if vectorstore.stop():
        print(f"Контейнер {vectorstore.CONTAINER} зупинено. Дані лишилися в томі\n"
              f"{vectorstore.VOLUME}: нічого не видалено.")
    else:
        print(f"Не вдалося зупинити {vectorstore.CONTAINER}. Це не заважає: рішення\n"
              f"вже записане, сервер до бази не звертатиметься.\n"
              f"Зупинити руками: docker stop {vectorstore.CONTAINER}")


def disable_vectors(offer_stop: bool = False) -> int:
    """Відповідь «n» — і те саме, що робить --no-vectors.

    `offer_stop` вмикається лише на діалоговому шляху. У --no-vectors його
    немає навмисно: цей прапорець існує для скриптів, а скрипт нікому не
    відповість на питання й зависне на ньому.
    """
    write_mode({"search": "words", "why": "вибір власника"})
    if offer_stop:
        _offer_stop()
    _report()
    return 0


def stop_vectors() -> int:
    """--stop: вимкнути пошук за змістом і зупинити контейнер.

    Порядок тут не випадковий. Зупинити контейнер, не змінивши рішення, —
    марна праця: сервер прочитає з файла «vectors» і при наступному ж запуску
    підніме його назад, бо саме так він і задуманий. Тому спершу записується
    пошук по словах, і аж потім зупиняється контейнер.

    Нічого не видаляється: ані колекція, ані том, ані сам контейнер. Дані
    лишаються на місці, і `--vectors` повертає все за секунди, не перераховуючи
    жодного вектора. Видалення тому — робота людини, і команда для неї названа
    в README.
    """
    from common import vectorstore

    write_mode({"search": "words", "why": "вимкнено командою --stop"})
    print("Записано пошук по словах — тепер сервер не підніматиме контейнер сам.")

    if not vectorstore.alive():
        print(f"Контейнер {vectorstore.CONTAINER} і так не відповідає — зупиняти нічого.")
    elif vectorstore.stop():
        print(f"Контейнер {vectorstore.CONTAINER} зупинено. Дані лишилися в томі\n"
              f"{vectorstore.VOLUME}: нічого не видалено, і `--vectors` поверне все\n"
              f"за секунди, не перераховуючи векторів.")
    else:
        print(f"Не вдалося зупинити {vectorstore.CONTAINER}. Це не заважає: рішення\n"
              f"вже записане, сервер шукатиме по словах і до бази не звертатиметься.\n"
              f"Зупинити руками: docker stop {vectorstore.CONTAINER}")
    _report()
    return 0


def main(argv: list[str]) -> int:
    if "--status" in argv:
        return status()
    if "--vectors" in argv:
        return enable_vectors(refill="--refill" in argv)
    if "--no-vectors" in argv:
        return disable_vectors()
    if "--stop" in argv:
        return stop_vectors()

    answer = _ask()
    if answer is None:
        # Вихід без рішення. Файл рішення не переписується навіть тим самим
        # значенням: людина натиснула «вийти», а не «лиши як є», і мовчазний
        # запис з новою датою виглядав би так, наче вона щось підтвердила.
        print("Nothing changed. The decision file is left exactly as it was:\n"
              f"  {MODE_PATH}")
        return 0
    return enable_vectors() if answer else disable_vectors(offer_stop=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
