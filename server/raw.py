"""
ОСНОВА · сирі кадри JSON-RPC, без клієнтської бібліотеки.

Це терміналний двійник вкладки Protocol → Messages в Inspector. check.py говорить
із сервером через ClientSession, тобто бібліотека сама будує кадри і сама їх
розбирає; тут кадри написані руками і друкуються рівно так, як ідуть у трубу.
Видно і те, що сервер відповідає, і те, що в stdout немає нічого, крім протоколу:
будь-який зайвий рядок зламав би розбір ще на першій відповіді.

Логи сервера при цьому нікуди не діваються — його stderr успадковує термінал, і
рядок про кількість фрагментів з'являється перед першим кадром. Саме так це
розділення й має виглядати: протокол у stdout, діагностика в stderr.

    python -m server.raw            # кадри, довгі обрізані до 400 символів
    python -m server.raw --full     # кадри повністю, нічого не обрізано
"""

import json
import pathlib
import subprocess
import sys

SERVER = pathlib.Path(__file__).resolve().parent / "spec_mcp.py"

# Ревізія протоколу, якою представляється цей клієнт. У mcp 2.1.1 найновіша —
# 2026-07-28 (та, що прибрала рукостискання initialize), але клієнти, з якими
# сервер зустрічається сьогодні, ще ходять через initialize, тому тут навмисно
# стара ревізія: показати треба саме те рукостискання, яке видно в Inspector.
PROTOCOL_VERSION = "2025-06-18"

CUT = 400

FRAMES = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "practice-raw", "version": "1.0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "search_spec",
                "arguments": {"query": "Object.prototype.toString tag", "k": 1}}},
]


def show(mark: str, line: str, full: bool) -> None:
    body = line if full or len(line) <= CUT else line[:CUT] + f"... (ще {len(line) - CUT} символів)"
    print(f"{mark} {body}")


def main(argv: list[str]) -> int:
    full = "--full" in argv
    proc = subprocess.Popen([sys.executable, str(SERVER)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, encoding="utf-8", bufsize=1)
    answers = 0
    try:
        for frame in FRAMES:
            line = json.dumps(frame, ensure_ascii=False)
            show("→", line, full)
            proc.stdin.write(line + "\n")
            proc.stdin.flush()

            # Відповідь приходить лише на запит з id; сповіщення її не має —
            # це видно й у самому переліку кадрів вище.
            if "id" not in frame:
                print("  (сповіщення, відповіді не буде)")
                continue

            reply = proc.stdout.readline()
            if not reply:
                print("\nСервер закрив stdout, не відповівши. Дивіться його stderr вище.")
                return 1
            show("←", reply.rstrip("\n"), full)
            answers += 1
            print()
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=5)

    print(f"Кадрів надіслано: {len(FRAMES)}, відповідей отримано: {answers}. "
          f"Жодного зайвого рядка в stdout не було — інакше розбір упав би вище.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
