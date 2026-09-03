"""CLI ядра фабрики.

    .venv/bin/python -m engine --instance <назва> --sources [--why]
    .venv/bin/python -m engine --instance <назва> --list
    .venv/bin/python -m engine --instance <назва> --refresh [id...]

`--sources` друкує оголошення джерел і білий список, що з нього виводиться;
`--list` показує, що завантажилося б, нічого не пишучи; `--refresh` завантажує
корпус у `instances/<назва>/corpus/`, тягнучи лише дозволене. Без імен `--refresh`
оновлює все, з іменами — лише названі документи чи джерела.

Кличеться з теки `docfactory/` (щоб `engine` був видимий як пакет), venv-ом самого
примірника (щоб залежності читачів були на місці).
"""

import pathlib
import sys

from engine import sources as S

_DOCFACTORY = pathlib.Path(__file__).resolve().parent.parent


def _instance_dir(name: str) -> pathlib.Path:
    d = _DOCFACTORY / "instances" / name
    if not d.is_dir():
        raise SystemExit(f"Немає примірника «{name}»: {d}")
    return d


def _print_sources(data: dict, why: bool) -> None:
    src = data["sources"]
    print(f"── Джерела примірника «{data.get('instance', '?')}»: {len(src)} ──")
    for s in src:
        exp = "  [expand:chapters]" if s.get("expand") == "chapters" else ""
        print(f"  {s['id']:<34} {s['reader']:<7} {s['url']}{exp}")
        if why and s.get("note"):
            print(f"      {s['note']}")
    print(f"── Білий список хостів (звідси й тільки звідси): "
          f"{', '.join(S.hosts(src))} ──")


def main(argv: list[str]) -> int:
    if "--instance" not in argv or argv.index("--instance") + 1 >= len(argv):
        print("Вкажіть примірник:  -m engine --instance <назва> "
              "--sources [--why] | --list | --refresh [id...]")
        return 2
    name_idx = argv.index("--instance") + 1
    name = argv[name_idx]
    instance_dir = _instance_dir(name)

    if "--manifest" in argv:
        import time
        from engine import manifest as M
        data = S.load(instance_dir)
        corpus = instance_dir / "corpus"
        m = M.build_corpus(corpus, data["sources"], time.strftime("%Y-%m-%d"))
        path = M.save(corpus, m)
        print(f"── Паспорт {path.name}: {len(m['documents'])} документів ──")
        return 0

    if "--status" in argv:
        from engine import status as ST
        return ST.status(instance_dir, deep="--deep" in argv)

    if "--refresh" in argv or "--list" in argv:
        from engine import refresh as R
        targets = {a for i, a in enumerate(argv)
                   if not a.startswith("-") and i != name_idx}
        return R.refresh(instance_dir, targets,
                         do_refresh="--refresh" in argv, listing="--list" in argv)

    data = S.load(instance_dir)
    if "--sources" in argv:
        _print_sources(data, "--why" in argv)
        return 0
    print("Доступно:  --sources [--why] | --list | --refresh [id...]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
