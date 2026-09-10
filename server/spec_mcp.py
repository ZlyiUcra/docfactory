"""ПРАКТИКА М5 · MCP-сервер над пошуком по специфікації ECMAScript.

Своє API тут — шар пошуку з практик модулів 2–4: розділи специфікації, поділені
на фрагменти, і пошук по словах BM25 над ними. Агент курсу возить цей пошук
усередині свого процесу; тут той самий пошук виставлений назовні двома
інструментами, і його бачить будь-який MCP-клієнт — Inspector, Claude Code,
Cursor.

Завантажується весь корпус: уся ECMA-262 плюс ECMA-402, ECMA-404, ECMA-414 і
вільні документи, на які спирається 402, — сімдесят два файли, 4162 фрагменти.
Що саме завантажено, сервер пише в stderr на старті і дописує окремим реченням
до опису обох інструментів.

Шукає сервер двома способами. По словах — завжди: індекс BM25 будується з тих
самих файлів при завантаженні модуля і нічого більше не потребує. За змістом —
коли власник погодився на це при підготовці (server/setup.py): тоді поруч
працює Qdrant, а близькість рахує модель bge-small через ONNX. Обидва списки
зливаються за взаємним рангом, і відповідь пошуку каже полем `search`, який із
двох способів її дав.

Другий спосіб ніде не стоїть на критичному шляху. Контейнер піднімається, а
модель прогрівається в окремій нитці, тож на перший запит сервер відповідає
одразу, поки що по словах. Якщо Qdrant не піднявся або колекція порожня, сервер
не падає: пише причину в stderr і працює по словах далі.

Ключі серверу не потрібні: він не звертається ні до Anthropic, ні в мережу —
читає теки docs*/, говорить із Qdrant на localhost і більше нічого.

Запуск на stdio (сам по собі мовчки чекає клієнта — це не зависання): клієнт,
чи то Mode A, чи то Inspector, сам запускає цей процес і говорить у труби.

    .venv/bin/python server/spec_mcp.py

Запуск HTTP-сервером для Claude Code: довгий процес тримає порт примірника з
config.json, а Claude Code під'єднується за адресою (див. .mcp.json).

    .venv/bin/python server/spec_mcp.py --serve

Перевірки:

    .venv/bin/python -m server.smoke     функції напряму, повз протокол
    .venv/bin/python -m server.check     справжній stdio-клієнт
    npx -y @modelcontextprotocol/inspector .venv/bin/python server/spec_mcp.py
"""

import datetime
import os
import pathlib
import re
import sys
import textwrap
import threading

# Сервер запускають файлом («python server/spec_mcp.py»), і клієнт (Mode A,
# Inspector) робить це зі своєї поточної теки. Тому корінь коду фабрики
# (docfactory/, на рівень вище за server/) додаємо в шлях самі, інакше
# «import common» і «import server» не знайдуться.
_CODE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

# MCP SDK 1.x називав це FastMCP, у 2.0 — MCPServer; API той самий.
# Той самий подвійний імпорт, що в курсовому tracking_mcp.py.
try:
    from mcp.server import MCPServer as _Server          # SDK >= 2.0
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as _Server  # SDK 1.x
    except ImportError:
        raise SystemExit("Потрібен MCP SDK:  pip install 'mcp[cli]'")

from common import instance
from common import nform
from common.corpus import DOC_SET, Passage, section_map
from common.idmap import assign_ids
from common import mode
from common.lexical import LexicalIndex, tokenize

# Тека даних цього примірника: corpus/ уже прочитано через common, тут потрібні
# out/ для журналу викликів і config.json для порту HTTP-сервера. Її каже
# DF_INSTANCE_DIR, а не розташування коду — код спільний на всі домени.
_INSTANCE = instance.root()

# Скільки символів фрагмента віддавати у відповіді пошуку. Фрагменти бувають до
# півтори тисячі символів, і три таких у відповіді — це вже стіна тексту в
# контексті клієнта. Хто хоче повний текст, кличе read_section за ідентифікатором.
PREVIEW_CHARS = 600

# Межі k. Менше одного — безглуздо, більше десяти — це вже добрих кілька тисяч
# слів в одній відповіді, і клієнт платить за них своїми токенами.
K_MIN, K_MAX = 1, 10

# Індекс будується один раз при завантаженні модуля, а не на кожен виклик:
# уся ECMA-262 читається з файлів і індексується приблизно за секунду, але
# робити це щоразу означало б платити цією секундою за кожен запит.
_INDEX = LexicalIndex()

# Ідентифікатори роздає common/idmap.py, а не сам Passage.pid: на повній
# специфікації два фрагменти можуть дістати однаковий pid, і один із них став би
# недосяжним. Те саме місце використовує заливання в Qdrant, тож ідентифікатор у
# відповіді пошуку і ідентифікатор у базі — той самий рядок.
_BY_ID, _UID = assign_ids(_INDEX.passages)

# Порожній індекс — завжди помилка даних, і сказати про неї треба словами ще
# тут: нижче код бере з індексу приклад ідентифікатора, і на порожньому словнику
# це падало б голим StopIteration без жодного натяку на причину. Порожнім індекс
# буває у свіжого домену, де corpus/ ще не наповнений.
if not _BY_ID:
    raise SystemExit(
        "spec_mcp: індекс порожній — жоден документ у corpus/ не дав фрагмента з "
        "текстом. Наповніть корпус (./df <домен> refresh) і підніміть сервер знову.")

_COUNT = len(_INDEX.passages)
_DOCS = len({p.doc_id for p in _INDEX.passages})

# Що саме зараз завантажено — одним реченням для моделі. Це не можна написати в
# докстрінгу наперед: набір обирає той, хто запускає сервер, і лише сам сервер
# знає, що з цього вийшло. Модель, яка не знає меж того, що їй доступно, вигадує
# відповіді про розділи, яких тут немає.
_LOADED = {
    "core": (f"Loaded right now: only the {_DOCS} sections around the Object type "
             f"({_COUNT} excerpts) -- the object type itself, ordinary and exotic "
             f"objects, and the Object, Array, String, Number, Boolean, Symbol and "
             f"Proxy chapters. The rest of the language is not here."),
    "full": (f"Loaded right now: the whole of ECMA-262, {_COUNT} excerpts from "
             f"{_DOCS} sections -- syntax, semantics, every built-in object. Other "
             f"standards are not here: no ECMA-402 (Intl), no ECMA-404 (JSON)."),
    "suite": (f"Loaded right now: the whole of ECMA-262 together with ECMA-402 "
              f"(Intl), ECMA-404 (JSON), ECMA-414 and the free documents the Intl "
              f"specification builds on -- RFC 4647 and the Unicode reports UAX #29, "
              f"UTS #10 and UTS #35. {_COUNT} excerpts from {_DOCS} documents. This "
              f"is the widest set the server has; if something about JavaScript or "
              f"its internationalization is not found here, it is unlikely to be "
              f"anywhere in these standards."),
}[DOC_SET]

# Рядок діагностики — у stderr. У stdout не можна нічого: там ходять кадри
# JSON-RPC, і будь-який print ламає клієнтові розбір відповіді.
print(f"spec_mcp: набір «{DOC_SET}», проіндексовано {_COUNT} "
      f"{nform(_COUNT, 'фрагмент', 'фрагменти', 'фрагментів')} "
      f"з {_DOCS} {nform(_DOCS, 'розділу', 'розділів', 'розділів')}",
      file=sys.stderr)

# Звірка з документами: кожен розділ, у якого є власний текст, мусить бути в
# індексі. Мовчазна втрата вже траплялася — відбір за довжиною в corpus.py
# прибирав 368 коротких розділів, і пошук відповідав за них сусідніми номерами.
# Тепер втрата — не тихий мінус у числі фрагментів, а рядок з іменами при
# кожному старті, поруч із рештою чисел.
_SECTIONS = section_map()
_WITH_TEXT = {s for s, has in _SECTIONS.items() if has}
_LOST = _WITH_TEXT - {p.section for p in _INDEX.passages if p.section}
if _LOST:
    print(f"spec_mcp: УВАГА: {len(_LOST)} "
          f"{nform(len(_LOST), 'розділ', 'розділи', 'розділів')} із власним "
          f"текстом немає в індексі: {', '.join(sorted(_LOST)[:5])}"
          f"{'…' if len(_LOST) > 5 else ''} — пошук відповідатиме сусідніми",
          file=sys.stderr)
else:
    print(f"spec_mcp: усі {len(_WITH_TEXT)} розділів із власним текстом в "
          f"індексі; рубрик без тексту {len(_SECTIONS) - len(_WITH_TEXT)}",
          file=sys.stderr)

# Пошук за змістом: чи його просили, і чи він уже готовий.
#
# Рішення ухвалене один раз при підготовці (server/setup.py) і лежить у
# out/mode.json; тут його лише читають. Якщо просили — усе довге робиться в
# окремій нитці: підняти контейнер Qdrant, звірити колекцію, прогріти модель.
# На критичному шляху не стоїть нічого: сервер відповідає клієнтові одразу, а
# поки нитка не впоралася, пошук іде по словах. Так само він поводиться, коли
# Qdrant лежить і підняти його не вдалося.
_MODE = mode.read()
_VECTORS_ASKED = _MODE.get("search") == "vectors"
_VECTORS_READY = False
_VECTORS_WHY = "" if _VECTORS_ASKED else "не просили при підготовці"


def _prepare_vectors() -> None:
    global _VECTORS_READY, _VECTORS_WHY
    try:
        from common import embed, vectorstore
        if not vectorstore.ensure_running():
            _VECTORS_WHY = "Qdrant не відповідає і контейнер не піднявся"
        elif vectorstore.count() == 0:
            _VECTORS_WHY = (f"колекція {vectorstore.COLLECTION} порожня — "
                            f"запустіть python -m server.setup --vectors")
        else:
            embed.model()          # прогрів: перший запит не має платити за це
            _VECTORS_READY = True
            have = vectorstore.count()
            print(f"spec_mcp: пошук за змістом готовий, "
                  f"{have} точок у {vectorstore.COLLECTION}", file=sys.stderr)
            # Недолита колекція — не привід відмовлятися від неї: три з половиною
            # тисячі фрагментів шукають краще, ніж жодного. Але й мовчати про це
            # не можна: заливання переривається легко, а зовні недостача видно
            # тільки як «чомусь не знайшлося».
            if have < _COUNT:
                print(f"spec_mcp: у колекції {have} точок замість {_COUNT} — "
                      f"частина фрагментів шукається лише по словах; "
                      f"дорахувати: python -m server.setup --vectors",
                      file=sys.stderr)
            elif have > _COUNT:
                print(f"spec_mcp: у колекції {have} точок, а фрагментів {_COUNT} — "
                      f"колекція від іншого видання набору, і частина її точок "
                      f"описує текст, якого в документах уже немає; "
                      f"що з цим робити: python -m server.setup --vectors",
                      file=sys.stderr)
            return
    except Exception as exc:                      # noqa: BLE001 - причина в stderr
        _VECTORS_WHY = f"{type(exc).__name__}: {exc}"
    print(f"spec_mcp: пошук за змістом недоступний ({_VECTORS_WHY}); "
          f"працюю по словах", file=sys.stderr)


if _VECTORS_ASKED:
    # Нитка правильна для роботи: клієнт дістає відповідь одразу, поки що по
    # словах, а пошук за змістом підхоплюється секунд через десять. Для виміру
    # якості це не годиться — перші питання прогону шукали б по словах, пізніші
    # за змістом, і число залежало б від того, що встигло раніше. DF_VECTORS_WAIT
    # робить прогрів синхронним: сервер відповідає лише коли вектори готові.
    if os.getenv("DF_VECTORS_WAIT"):
        _prepare_vectors()
    else:
        threading.Thread(target=_prepare_vectors, name="vectors",
                         daemon=True).start()


# Журнал викликів: хто що питав.
#
# Кожен клієнт запускає власну копію цього сервера, тому в Inspector видно лише
# те, що питав Inspector, а в Claude Code — лише те, що питав Claude Code.
# Рядок нижче йде у два місця. У stderr — бо там його одразу показує Inspector
# (вкладка Console) і Claude Code із прапорцем --debug; це те саме stderr, куди
# картка велить складати всю діагностику, аби не зачепити stdout, де ходить
# протокол. У файл — бо stderr живе рівно стільки, скільки процес, а щоб
# порівняти виклики з різних клієнтів, запис має пережити їх усі.
#
# Файл тільки дописується. Ніщо в цьому коді його не читає, не чистить і не
# перезаписує; коли він набридне, власник прибирає його сам. Дві межі бережуть
# його від переповнення без жодного видалення:
# - поле запиту ріжеться тут, в одному місці, а не по місцях виклику — інакше
#   довжину рядка обирає той, хто кличе інструмент: запит на 50 000 символів
#   із хибним k колись лягав у файл цілим саме на тому шляху, що його відхиляв,
#   і модель, зациклена на незрозумілій їй помилці, заповнювала диск;
# - сягнувши стелі, файл більше не дописується — про це кажеться раз у stderr,
#   а сам stderr пишеться далі, тож Inspector і --debug нічого не втрачають.
#   Ротації навмисно немає: вона видаляла б журнал сама, без відома власника.
LOG_PATH = _INSTANCE / "out" / "calls.log"
LOG_FIELD_CHARS = 120
LOG_MAX_BYTES = 16 * 1024 * 1024
_PID = os.getpid()
_LOG_FULL = False


def _log(tool: str, request: str, outcome: str) -> None:
    global _LOG_FULL
    if len(request) > LOG_FIELD_CHARS:
        request = (f"{request[:LOG_FIELD_CHARS]}… "
                   f"(+{len(request) - LOG_FIELD_CHARS} симв.)")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} pid={_PID} {tool} {request} -> {outcome}"
    print(line, file=sys.stderr)
    if _LOG_FULL:
        return
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size >= LOG_MAX_BYTES:
            _LOG_FULL = True
            print(f"spec_mcp: журнал {LOG_PATH} сягнув "
                  f"{LOG_MAX_BYTES // (1024 * 1024)} МБ — у файл більше не пишу, "
                  f"stderr пишеться далі; приберіть або перейменуйте файл самі",
                  file=sys.stderr)
            return
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        # Журнал не має права зіпсувати відповідь клієнтові: якщо теки немає або
        # диск не пише, скаржимося в stderr і працюємо далі.
        print(f"spec_mcp: журнал не записався ({exc})", file=sys.stderr)


mcp = _Server("ecma-spec")


def _preview(text: str) -> str:
    """Текст фрагмента для відповіді пошуку: обрізаний, з видимою позначкою обрізки."""
    if len(text) <= PREVIEW_CHARS:
        return text
    return text[:PREVIEW_CHARS] + "..."


# Санітар видачі — серверна оборона для клієнта, якого ми не контролюємо (Claude
# Code, Inspector). Це розтяжка на очевидні формулювання вшитих указівок, а не
# класифікатор: регулярний вираз не впізнає вказівку природною мовою, тож ключі
# нижче чіпляють лише типові звороти, і вказівка, сказана іншими словами,
# пройде повз. Зате спрацювання гучне: зачеплений фрагмент вилучається з
# відповіді цілком (див. _sanitize). Корпус тут чистий, тож у нормі розтяжка
# мовчить; чесні межі захисту названі в README.
_INJECTION = re.compile(
    r"ignore\s+(all\s+)?previous|disregard\s+(the\s+)?(above|previous)|"
    r"system\s+prompt|reveal\s+your|call\s+the\s+tool|fetch_url|"
    r"append\s+.{0,40}https?://|EDITORIAL\s+NOTE", re.I)


def _sanitize(passage: Passage) -> tuple[str, bool]:
    """Текст фрагмента для відповіді, або відмова замість нього. Повертає
    (текст, чи спрацювала розтяжка).

    Раніше вирізалися лише збіглі слова — а це гірше, ніж нічого: вказівка
    лишалася читною, маркер же створював враження, що її знешкоджено. Фрагмент,
    який зачепив розтяжку, не можна читати по шматках: замість тексту клієнт
    дістає відмову з номером розділу, а спрацювання лягає в out/calls.log."""
    if _INJECTION.search(passage.text):
        return (f"[{passage.label}: текст вилучено політикою сервера — у "
                f"фрагменті знайдено вбудовану вказівку для моделі, тож його "
                f"не видано ані цілим, ані частинами.]"), True
    return passage.text, False


def _format_hits(passages: list[Passage], how: str) -> dict:
    """Відповідь пошуку. Формат той самий, що в common/search.py практики модуля 4,
    плюс поле `search` і обрізаний текст.

    Поле `search` каже моделі, як саме знайдено: «words» — лише по словах,
    «meaning+words» — обидва способи разом. Це не прикраса: коли пошук за змістом
    лежить, порожня відповідь означає інше, ніж коли він працює, і модель має
    змогу це врахувати."""
    if not passages:
        return {"found": 0, "search": how,
                "note": "Nothing in the available excerpts matches this query."}
    items = []
    for p in passages:
        clean, flagged = _sanitize(p)
        if flagged:
            _log("sanitize", f"id={_UID[p]}",
                 "фрагмент вилучено з видачі search_spec: розтяжка санітара")
        items.append({"id": _UID[p], "section": p.label,
                      "document": p.doc_title, "text": _preview(clean)})
    return {"found": len(passages), "search": how, "passages": items}


def _rrf(rankings: list[list[Passage]], k: int, const: int = 60) -> list[Passage]:
    """Злиття двох списків за взаємним рангом (RRF).

    Пошук по словах і пошук за змістом дають оцінки в різних шкалах, і порівнювати
    їх безпосередньо не можна. RRF порівнює не оцінки, а місця: фрагмент, що
    трапився високо в обох списках, підіймається вище за той, що виграв лише в
    одному. Стала 60 — та, з якою цей спосіб опублікували; вона згладжує різницю
    між першим і другим місцем.
    """
    score: dict[Passage, float] = {}
    for ranking in rankings:
        for place, passage in enumerate(ranking):
            score[passage] = score.get(passage, 0.0) + 1.0 / (const + place + 1)
    return sorted(score, key=lambda p: -score[p])[:k]


def _find(query: str, k: int) -> tuple[list[Passage], str]:
    """Пошук по словах, а якщо готовий — разом із пошуком за змістом."""
    words = _INDEX.retrieve(query, k)
    if not _VECTORS_READY:
        return words, "words"
    try:
        from common import embed, vectorstore
        hits = vectorstore.search(embed.embed_query(query), k)
        meaning = [_BY_ID[h["uid"]] for h in hits if h.get("uid") in _BY_ID]
    except Exception as exc:                      # noqa: BLE001 - причина в stderr
        print(f"spec_mcp: пошук за змістом не відповів ({exc}); "
              f"віддаю знайдене по словах", file=sys.stderr)
        return words, "words"
    if not meaning:
        return words, "words"
    return _rrf([words, meaning], k), "meaning+words"


def search_spec(query: str, k: int = 3) -> dict:
    """Search the text of the ECMAScript language specification (ECMA-262) and
    return the matching excerpts with their section numbers.

    Call this when a question is about how something is defined or behaves in
    JavaScript itself -- an operator, an abstract operation, a built-in method or
    property of Object, Array, String, Number, Boolean, Symbol, Proxy or
    TypedArray -- and the answer should cite where the specification says it.
    Example query: "Object.prototype.toString tag".

    Do not call this for questions about browsers, the DOM, Node.js, npm
    packages, TypeScript or any library: none of that is in the specification and
    the search will return unrelated sections with confident-looking numbers.

    Write the query in English, using the identifiers the specification itself
    uses. Everything here is English -- the excerpts, the word index and the
    meaning index alike -- and the word index keeps only latin letters and digits,
    so a query written in Ukrainian, Russian or any other non-latin script matches
    nothing at all and comes back with `found: 0`. When the user asks in another
    language, translate the question into specification terms first, then search.

    The `search` field of the result says how the excerpts were found: "words"
    means the words of the query had to appear in the text, so a query phrased in
    other words than the specification uses may miss; "meaning+words" means a
    second, meaning-based index answered as well, and a paraphrase had a chance.

    Answer in the language the user wrote in. When that language is Ukrainian,
    write as a Ukrainian engineer writes, not as a translation from English:
    plain technical prose, and the Ukrainian word wherever one exists -- "розділ",
    not "секція"; "уривок", not "ексерпт"; "вбудований метод", not "білт-ін".
    Never call this set of sections "корпус" -- say "розділи специфікації".
    Identifiers, method names and section titles stay in English, spelled exactly
    as the specification spells them. Name the section by its number, and build
    the answer out of the steps that came back, not out of memory: if the excerpt
    was cut, read the whole of it before describing what it says.

    Arguments: `query` is free text; `k` is how many excerpts to return, 1 to 10,
    default 3. Excerpt text is cut at 600 characters -- pass the `id` of an
    excerpt to `read_section` to get the whole thing.
    """
    if not isinstance(k, int) or k < K_MIN or k > K_MAX:
        _log("search_spec", f"query={query!r} k={k!r}", "помилка: k поза межами")
        return {"error": f"k має бути від {K_MIN} до {K_MAX}"}

    # Запит без жодного латинського слова далі не йде — ані в пошук по словах,
    # ані в пошук за змістом. По словах він і так дав би нуль: токенізатор бачить
    # тільки [a-z0-9_]+. А от пошук за змістом дав би відповідь, і в цьому вся
    # біда: модель векторів англійська, кирилицю вона зводить до чисел, які нічого
    # не означають, але найближчі сусіди в них знайдуться завжди. Клієнт дістав би
    # три впевнені номери розділів навмання — саме та помилка, проти якої написано
    # абзац «коли не кликати». Тому тут відповідь чесна: шукати не було чого.
    if not tokenize(query):
        _log("search_spec", f"query={query!r} k={k}",
             "нуль латинських слів у запиті")
        return {"found": 0, "search": "words",
                "note": "The query has no latin words, and everything held here "
                        "is English -- both the word index and the meaning index. "
                        "Translate the question into the terms the specification "
                        "uses, then search again."}

    hits, how = _find(query, k)
    _log("search_spec", f"query={query!r} k={k}",
         f"знайдено {len(hits)} ({how})")
    return _format_hits(hits, how)


def read_section(id: str) -> dict:
    """Return the full text of one specification excerpt by its identifier,
    together with the section number and the URL it was taken from.

    Call this after `search_spec` when the excerpt you need came back cut at 600
    characters, or when the exact wording of a step in an abstract operation
    matters -- a definition quoted half-way is how a wrong answer starts.

    Do not guess identifiers: they are not section numbers and cannot be
    assembled by hand. Every identifier this tool accepts comes from the `id`
    field of a `search_spec` result.

    The text comes back in English, as the specification is written. Answer the
    user in their own language, and follow the same rules as for `search_spec`:
    keep identifiers and section titles in English, use Ukrainian words for
    Ukrainian prose, and cite the section number you read.
    """
    passage = _BY_ID.get(id)
    if passage is None:
        # Голий номер розділу — найчастіша вгадка моделі. Якщо документи такий
        # розділ справді називають, відповідь мусить сказати, що з ним:
        # рубрика без власного тексту — це межа корпусу, а не помилка виклику,
        # і мовчати про неї означає вчити модель гадати номери далі.
        sec = id.strip()
        if sec in _SECTIONS and not _SECTIONS[sec]:
            _log("read_section", f"id={id!r}", "рубрика без власного тексту")
            return {"error": f"розділ {sec} існує, але власного тексту не має — "
                             f"його вміст лежить у підрозділах",
                    "hint": "знайдіть підрозділи через search_spec і читайте їх "
                            "за id з видачі"}
        if sec in _SECTIONS:
            _log("read_section", f"id={id!r}", "номер розділу замість id")
            return {"error": f"розділ {sec} в індексі є, але читається він за "
                             f"повним id, а не голим номером",
                    "hint": f"id береться з поля id у відповіді search_spec, "
                            f'напр. "{_EXAMPLE_ID}"'}
        _log("read_section", f"id={id!r}", "помилка: такого id немає")
        return {"error": "фрагмента з таким id немає",
                "hint": "id береться з поля id у відповіді search_spec"}
    clean, flagged = _sanitize(passage)
    if flagged:
        _log("read_section", f"id={id!r}",
             "фрагмент вилучено з відповіді: розтяжка санітара")
    else:
        _log("read_section", f"id={id!r}", f"{len(passage.text)} символів")
    return {"id": _UID[passage],
            "section": passage.label,
            "document": passage.doc_title,
            "url": passage.url,
            "text": clean}


# Реєстрація. Обидва інструменти могли б висіти на звичайному @mcp.tool(), і тоді
# описом ставав би самий докстрінг — так зроблено в курсовому tracking_mcp.py.
# Тут описом стає докстрінг ПЛЮС рядок про завантажений набір: інакше модель не
# знає, де межа того, що їй доступно, а докстрінг цієї межі знати не може, бо її
# обирають при запуску.
#
# Так само дописується приклад ідентифікатора для read_section. У докстрінгу його
# теж не напишеш наперед: ідентифікатор починається з імені файла, а те саме
# місце специфікації лежить у різних наборах у файлах із різними іменами —
# «14-object-objects#20.1.3.6/2» у наборі core і «20-fundamental-objects#20.1.3.6/2»
# у full та suite. Приклад модель копіює дослівно, тож він мусить бути з того
# індексу, який справді завантажений.
_EXAMPLE_ID = next(
    (uid for uid, p in _BY_ID.items() if "Object.prototype.toString" in p.label),
    next(iter(_BY_ID)))

_EXTRA = {
    "search_spec": _LOADED,
    "read_section": f'Example identifier: "{_EXAMPLE_ID}".\n\n{_LOADED}',
}

TOOL_DESCRIPTIONS: dict[str, str] = {}

for _fn in (search_spec, read_section):
    TOOL_DESCRIPTIONS[_fn.__name__] = (
        textwrap.dedent(_fn.__doc__).strip() + "\n\n" + _EXTRA[_fn.__name__])
    mcp.tool(description=TOOL_DESCRIPTIONS[_fn.__name__])(_fn)


def _serve_port() -> int:
    """Порт HTTP-сервера: змінна DF_PORT як явне перекриття, інакше поле port
    із config.json примірника (його читає й перевіряє common/instance.py), як
    останній засіб — 8000. Так один і той самий примірник завжди на своєму
    порту, а різні примірники фабрики не б'ються за один."""
    env = os.environ.get("DF_PORT")
    if env and env.isdigit():
        return int(env)
    port = instance.config().get("port")
    return port if isinstance(port, int) else 8000


if __name__ == "__main__":
    # Без аргументів — stdio: клієнт (Mode A, Inspector) сам запускає цей процес
    # і говорить у труби. З --serve — довгий HTTP-сервер на порту примірника, до
    # якого Claude Code під'єднується за адресою. Захист однаковий в обох
    # транспортах: санітар видачі й вузький перелік інструментів живуть на
    # сервері, тож клієнт, який ми не контролюємо, дістає вже очищену відповідь.
    if "--serve" in sys.argv[1:]:
        _port = _serve_port()
        print(f"spec_mcp: HTTP-сервер на http://127.0.0.1:{_port}/mcp "
              f"(Ctrl+C спиняє)", file=sys.stderr)
        mcp.run(transport="streamable-http", host="127.0.0.1", port=_port)
    else:
        mcp.run()      # stdio: клієнт сам запускає цей процес і говорить у труби
