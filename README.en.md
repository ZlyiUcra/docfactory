# docfactory — a factory of MCP documentation servers

*The documentation in this repository is written in Ukrainian; this page is the short English entry point. For the
full story see [README.md](README.md) (architecture, protection layers, roadmap) and [UPDATE.md](UPDATE.md) (how
to keep the corpus current).*

One engine, many isolated servers. Each server answers questions about one body of documentation — today the
ECMAScript specification (ECMA-262, ECMA-402 and the Unicode, IETF and Ecma documents they build on), tomorrow the
docs of a JavaScript framework — over MCP: search over a local corpus plus four protection layers. Domains never
mix: the code is shared, everything else belongs to one *instance* under `instances/<domain>/`.

## What it is for

Not "documentation search", even though it looks like one. A model that does not know the limits of what it was
given does not stay silent — it invents: asked about a section it does not have, it names the neighbouring one
with the same confidence. Everything here makes such an answer hard to produce:

- the corpus holds normative documents only — the specification and the standards it references; no tutorials,
  no articles, no comments from the web;
- the unit of an answer is a clause with its section number, document title, source URL and fetch date, so every
  quotation can be checked in a minute — and a year later;
- the tool descriptions are long on purpose: they tell the model when *not* to call the tool (browsers, the DOM,
  Node.js, TypeScript — none of it is here, and a search would return unrelated sections with confident-looking
  numbers) and to build the answer out of the excerpts that came back, not out of memory;
- the second of four protection layers rejects `read_section` on an identifier the search never returned.

One boundary worth naming: the corpus says what the standard *requires*, not what browsers *do*.

## Layout

```
docfactory/
  engine/       shared: sources schema and whitelist, readers, corpus builder, passport, updater
  server/       shared: MCP server (HTTP and stdio), four protection layers, the project's own agent
  common/       shared: word search (BM25), meaning search (fastembed + Qdrant), instance config
  instances/
    ecmascript/ one instance: sources.json, corpus/, config.json, .env, .mcp.json — data only, no code
  df            launcher: ./df <instance> <step>, always from this directory
```

## Setup

Prerequisites: Python 3.10+ (tested on 3.14) and bash — Linux, macOS or WSL on Windows. Docker is needed only for
meaning search and is optional. An Anthropic API key is needed only for the `ask` step; everything else runs
without it.

```
cd instances/ecmascript
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt
cd ../..
```

Never activate the venv: `df` calls the right instance's `.venv/bin/python` itself.

## Run the server and connect Claude Code

```
./df ecmascript serve                                                  # separate terminal, keep it running
claude mcp add --transport http ecma-spec http://127.0.0.1:8760/mcp    # once; port comes from config.json
```

In a Claude Code session `/mcp` should list the server with two tools, `search_spec` and `read_section`. The
server itself never talks to a model: when the client is Claude Code, Claude Code is the model and pays with its
own tokens.

## Steps and what they cost

| Step | Does | Costs |
|------|------|-------|
| `sources`, `list`, `check`, `refresh`, `manifest` | build and update the corpus; network only to the URLs declared in `sources.json` | $0 |
| `setup`, `vectors`, `status` | optional meaning search: Qdrant in Docker, vectors computed locally | $0, minutes of CPU |
| `serve`, `tools`, `smoke`, `protocol`, `quality`, `raw` | the server and its checks | $0 |
| `ask "question"` | the project's own agent with all four protection layers | **paid** — model tokens, needs the key |

Updating the corpus is five commands — `check`, `refresh`, `manifest`, `vectors`, restart `serve` — described step
by step in [UPDATE.md](UPDATE.md).

## License

MIT — see [LICENSE](LICENSE). The corpus texts keep the licenses of their publishers (Ecma, IETF Trust, Unicode).
