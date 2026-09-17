#!/usr/bin/env python3
"""Happy Bite — turn the big corpus into recipes the app can actually cook.

`shared/recipes.jsonl` is the large source corpus the RAG searches. It is flat
text: a title, a list of ingredients as a shopper wrote them, and a block of
instructions. The app needs something quite different — ingredient IDS from
your catalogue, quantities in the catalogue's units, one action per step, a
heat, a cue, and the ids that go in at each step.

Nothing mechanical can bridge that. A regex can map "2 cloves garlic" to
`garlic`, but it cannot tell you the pan should be medium-high or that you
wait for the edges to go golden. So this uses YOUR model, the one already in
backend/.env, to restructure each corpus recipe into the app's shape — and
then refuses anything that comes back wrong.

It is slow on purpose: about a minute a recipe on a 4B. Start it and go and do
something else. It saves after every accepted recipe and can be stopped and
resumed.

    python3 tools/import_corpus.py --count 79

    --count N       how many to add (default 79)
    --source PATH   the jsonl (default shared/recipes.jsonl)
    --dry           show what it would accept, write nothing

There is deliberately no "fast, no model" mode. One was written and thrown
away: with nothing to reason from it had to invent a quantity for every
ingredient — 200 g of this, 2 of that — and those inventions sailed through
the checks below looking exactly like real numbers. A recipe that is wrong
and passes is worse than one that is missing.

Every recipe must pass the same gate as one the assistant writes, plus the
checks in audit.py. A recipe that fails is SKIPPED and the reason printed;
nothing is repaired into the book.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

G, R, Y, D, O = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = O = ""

SYSTEM = """You restructure an existing recipe into a strict JSON shape for a kitchen app.
You are NOT inventing a dish: the source recipe below is the authority on what
it is and what goes in it. You are adding the things the source leaves out —
which pan heat, what to look for, what goes in at each step.

Valid ingredient ids, with their name and unit: {ids}

Reply with ONE JSON object and nothing else:
{{"name": str, "minutes": int, "complexity": 1|2|3, "cuisine": str,
  "serves": 4, "types": ["dinner"], "description": str,
  "needs": [{{"id": str, "qty": number, "prep": str, "flexible": bool}}],
  "seasoning": [{{"id": str, "qty": number, "essential": bool}}],
  "extras": [str],
  "steps": [{{"do": str, "why": str, "heat": str, "cue": str, "minutes": number,
              "uses": [str]}}]}}

Rules:
- Ingredient ids ONLY from the list, quantities in that id's unit, for 4 people.
  Write about what an id MEANS, not what it is spelled like. Anything with no
  id goes in "extras" as plain text. Never invent an id.
- Every ingredient you list MUST be used by at least one step's "uses".
  A thing nobody cooks with does not belong on the list.
- "uses" holds the ids that go IN at that step. Not things added earlier.
- One action per step. If it needs "and then", split it. Four to eight steps.
- Every hob or oven step gets a "heat" (Low | Medium-low | Medium |
  Medium-high | High | Oven 200 °C) and, where it helps, a "cue" — what you
  see, hear or smell. "oil shimmers", "edges going golden".
- Anything seared, fried or grilled gets the time PER SIDE inside "do":
  "three minutes on the first side, two on the second".
- "why" only where skipping it ruins the dish. One plain line.
- Seasonings go in "seasoning": a few grams, not hundreds. Oils and vinegars
  are a few spoonfuls and "flexible": true.
- "minutes" is the wall clock. If a step runs unattended while you do the next
  one, say "Start this first" in its "do"."""


# --------------------------------------------------------------- the corpus

# ------------------------------------------------------- reading the corpus

def _inner(row: dict) -> dict:
    """The recipe inside a line.

    kaiser_recipes.jsonl is not a flat list of recipes. Every line is a
    chat-training record, and the recipe is a JSON STRING inside the assistant
    message, with capitalised keys:

        {"messages": [..., {"role": "assistant",
                            "content": "{\\"Title\\": ..., \\"Ingredients\\": [...]}"}]}

    backend/app/rag.py has always known this. These tools did not, and read
    2.1 million lines while understanding none of them. Flat shapes are still
    accepted, so another corpus can be dropped in without editing anything.
    """
    msgs = row.get("messages")
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "assistant":
                try:
                    inner = json.loads(m.get("content") or "{}")
                except ValueError:
                    return {}
                return inner if isinstance(inner, dict) else {}
    return row


def _listy(rec: dict, *names) -> list:
    for n in names:
        v = rec.get(n)
        if isinstance(v, list) and v:
            return [str(x) for x in v]
        if isinstance(v, str) and v.strip():
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except ValueError:
                pass
            return [p for p in re.split(r"[;\n]", v) if p.strip()]
    return []


def _texty(rec: dict, *names) -> str:
    for n in names:
        v = rec.get(n)
        if isinstance(v, list) and v:
            return " ".join(str(x) for x in v)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def recipe_parts(row: dict):
    """(title, ingredient lines, instructions) — whatever shape the line is."""
    rec = _inner(row)
    return (_texty(rec, "Title", "title", "name", "recipe_name"),
            _listy(rec, "Ingredients", "ingredients", "ingredient", "NER", "ingredients_raw"),
            _texty(rec, "Instructions", "instructions", "directions", "steps", "method"))


def shape_report(samples: list) -> str:
    """When nothing was understood, say what WAS there. A tool that reads two
    million lines, understands none of them and prints a cheerful zero is worse
    than one that crashes."""
    keys, inner_keys = set(), set()
    for row in samples:
        keys |= set(row.keys())
        got = _inner(row)
        if got is not row:
            inner_keys |= set(got.keys())
    out = "  top-level keys seen: " + (", ".join(sorted(keys)[:12]) or "none")
    if inner_keys:
        out += "\n  inside the assistant message: " + ", ".join(sorted(inner_keys)[:12])
    return out


def read_corpus(path: Path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


# ------------------------------------------------------------ the catalogue

def load_catalogue(root: Path) -> dict:
    path = root / "shared" / "catalog.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["ingredients"]


def id_block(known: dict) -> str:
    return ", ".join(f"{i} = {v.get('name', i)} ({v.get('unit', 'g')})"
                     for i, v in known.items())


def mentions(text: str, known: dict) -> set[str]:
    """Which catalogue ingredients does this corpus recipe actually name?"""
    low = " " + re.sub(r"[^a-z ]+", " ", text.lower()) + " "
    hits = set()
    for iid, ing in known.items():
        name = str(ing.get("name", "")).lower().strip()
        for word in {name, name.rstrip("s"), name + "s", iid.replace("u_", "")}:
            if len(word) >= 4 and f" {word} " in low:
                hits.add(iid)
                break
    return hits


# ----------------------------------------------------------------- the model

def env(root: Path) -> dict:
    path = root / "backend" / ".env"
    if not path.is_file():
        return {}
    return dict(re.findall(r"^([A-Z_]+)=(.*)$", path.read_text(encoding="utf-8"), re.M))


# Which optional fields this server will actually accept. Discovered once, on
# the first 400, and then left alone — exactly what backend/app/providers/llm.py
# had to learn the hard way. Ollama ignores a field it doesn't know; LM Studio
# validates the body and answers 400. "The OpenAI protocol" means different
# things to different servers.
EXTRAS = {"response_format": {"type": "json_object"}}


def _post(base: str, key: str, body: dict, wait: float) -> tuple[dict | None, str | None]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key or 'none'}"})
    try:
        with urllib.request.urlopen(req, timeout=wait) as r:
            return json.loads(r.read().decode()), None
    except urllib.error.HTTPError as e:
        # THE WHOLE POINT. "HTTP Error 400: Bad Request" is a category with the
        # evidence thrown away, and the evidence is sitting right here in the
        # body: the server says which field it refused and why.
        detail = ""
        try:
            detail = (e.read().decode(errors="replace") or "").strip().replace("\n", " ")
        except Exception:  # noqa: BLE001
            pass
        return None, f"HTTP {e.code}: {detail[:400] or 'no detail given'}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def ask(base: str, model: str, key: str, system: str, user: str, wait: float) -> tuple[dict | None, str | None]:
    global EXTRAS
    core = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 2000, "temperature": 0.7, "top_p": 0.8, "stream": False,
    }
    plans = ([dict(core, **EXTRAS)] if EXTRAS else []) + [dict(core)]
    for i, body in enumerate(plans):
        out, err = _post(base, key, body, wait)
        if out is not None:
            return _parse(out), None
        if not err.startswith("HTTP 400") or i == len(plans) - 1:
            return None, err
        # Only blame response_format if the server actually blamed it. A 400
        # that says "model not loaded" is not a field problem, and saying so
        # would send you looking in the wrong place.
        blamed = "response_format" in err
        print(f"    {Y}the server answered 400 — "
              + ("dropping response_format and asking again."
                 if blamed else "trying a plainer request.") + O
              + f"\n    It said: {err[9:240]}")
        EXTRAS = {}                       # don't ask again for the rest of this run
    return None, "unreachable"


def _parse(out: dict) -> dict | None:
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if "</think>" in text.lower():
        text = re.split(r"</think>", text, maxsplit=1, flags=re.I)[-1]
    text = re.sub(r"```(?:json)?", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        return json.loads(text[s:e + 1])
    except ValueError:
        return None


# ------------------------------------------------------------------ the gate

def check(r: dict, known: dict) -> list[str]:
    """The same standard as a recipe the assistant writes, plus audit.py."""
    bad = []
    if not isinstance(r, dict) or not str(r.get("name", "")).strip():
        return ["no name"]
    needs = [n for n in r.get("needs") or [] if isinstance(n, dict)]
    seas = [s for s in r.get("seasoning") or [] if isinstance(s, dict)]
    steps = [s for s in r.get("steps") or [] if isinstance(s, dict) and str(s.get("do", "")).strip()]
    if len(steps) < 3:
        bad.append(f"only {len(steps)} steps")
    if len(steps) > 10:
        bad.append(f"{len(steps)} steps")
    if len(needs) < 3:
        bad.append(f"only {len(needs)} tracked ingredients")

    have = set()
    for item in needs + seas:
        iid = item.get("id")
        if iid not in known:
            bad.append(f"invented id {iid!r}")
        else:
            have.add(iid)
    used = set()
    for st in steps:
        for u in st.get("uses") or []:
            if u not in have:
                bad.append(f"a step uses {u!r}, not in the recipe")
            used.add(u)
    for iid in sorted(have - used):
        prose = str(known[iid].get("name", "")).lower()[:5]
        if not any(prose in (st.get("do", "") + st.get("why", "")).lower() for st in steps):
            bad.append(f"{known[iid].get('name', iid)} is never used")

    serves = int(r.get("serves") or 4) or 4
    for n in needs:
        q, unit = n.get("qty"), known.get(n.get("id"), {}).get("unit", "g")
        if not isinstance(q, (int, float)) or q <= 0:
            bad.append(f"{n.get('id')} has no quantity")
            continue
        cap = {"g": 400, "ml": 400, "cl": 40, "l": 0.4, "u": 4}.get(unit, 400) * serves
        if q > cap:
            bad.append(f"{n.get('id')}: {q} {unit} for {serves}")
    for s in seas:
        q, unit = s.get("qty"), known.get(s.get("id"), {}).get("unit", "g")
        cap = {"g": 8, "ml": 10, "cl": 1, "l": 0.05, "u": 1}.get(unit, 8) * serves
        if isinstance(q, (int, float)) and q > cap:
            bad.append(f"seasoning {s.get('id')}: {q} {unit}")

    clock = 0
    for st in steps:
        m = st.get("minutes")
        if isinstance(m, (int, float)):
            clock += 0 if re.search(r"start th(is|ese) first", st.get("do", ""), re.I) else m
        if len([p for p in re.split(r"(?<=[.!?])\s+", str(st.get("do", "")).strip()) if p]) > 2:
            bad.append("a step packs three sentences")
    card = r.get("minutes")
    if isinstance(card, (int, float)) and clock and clock > card * 1.35:
        bad.append(f"card says {card} min, steps need {clock}")

    MEAT = {"beef", "pork", "chicken", "lamb", "cod", "salmon", "duck", "veal"}
    if have & MEAT and any(re.search(r"\b(sear|brown|fry|grill)", st.get("do", ""), re.I)
                           and st.get("heat") in ("High", "Medium-high") for st in steps):
        if not any(re.search(r"\b(side|turn them|flip)", st.get("do", ""), re.I) for st in steps):
            bad.append("meat seared but never turned")
    return bad


def slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "recipe"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    return out


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=79)
    ap.add_argument("--source", default="shared/kaiser_recipes.jsonl")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--wait", type=float, default=300.0)
    a = ap.parse_args()

    root = Path(".").resolve()
    for c in (root, root / "happy-bite", root.parent / "happy-bite"):
        if (c / "backend" / "app").is_dir() and (c / "shared").is_dir():
            root = c
            break
    src = root / a.source
    if not src.is_file():
        print(f"{R}No corpus at {src}{O}")
        found = sorted(q.name for q in (root / "shared").glob("*.jsonl"))
        if found:
            print(f"  shared/ holds: {', '.join(found)}")
            print(f"  Try:  python3 tools/import_corpus.py --source shared/{found[0]}")
        return 2

    known = load_catalogue(root)
    book_path = root / "shared" / "recipes.json"
    if not book_path.is_file():
        print(f"{R}No recipe book at {book_path}.{O} shared/recipes.json has been removed from the "
              "project and the app no longer reads it, so there is nothing to import into.")
        return 2
    book = json.loads(book_path.read_text(encoding="utf-8"))
    taken = {r["id"] for r in book["recipes"]}
    seen_names = {str(r["name"]).lower() for r in book["recipes"]}
    print(f"{root}\n{len(book['recipes'])} recipes in the book, "
          f"{len(known)} ingredients in the catalogue\n")

    e = env(root)
    base = (e.get("OPENAI_BASE_URL") or "http://localhost:1234/v1").rstrip("/")
    model = e.get("LLM_MODEL", "")
    ids = id_block(known)

    print(f"Using {model} at {base}")

    # One tiny request first. A misconfigured server should cost two seconds,
    # not the first recipe attempt — and the server's own words should be on
    # screen either way.
    probe, err = ask(base, model, e.get("OPENAI_API_KEY", ""),
                     'Reply with this exact JSON and nothing else: {"ok": 1}',
                     "Go.", min(a.wait, 120))
    if err:
        print(f"\n{R}The model server refused a one-line request.{O}")
        print(f"  {err}\n")
        print("Nothing here is about recipes. Check that LM Studio is running, that")
        print(f"  LLM_MODEL in backend/.env ({model!r}) matches a model it has loaded,")
        print(f"  and that {base} is the right address.")
        print("\n  python3 check_model.py   asks the same questions and shows the answers.")
        return 2
    print(f"{G}the server answers.{O} {D}About a minute each — leave it running.{O}\n")

    added, tried, skipped, scanned = 0, 0, 0, 0
    understood, samples = 0, []
    in_a_row = 0
    t0 = time.time()
    for row in read_corpus(src):
        if added >= a.count:
            break
        scanned += 1
        if len(samples) < 5:
            samples.append(row)
        title, ing_lines, instr = recipe_parts(row)
        ings = "; ".join(ing_lines)
        if title and ings:
            understood += 1
        elif scanned >= 2000 and not understood:
            # Never a cheerful zero. Say what the lines actually contain.
            print(f"{R}2000 lines read and not one recipe understood.{O}")
            print("The corpus is not the shape this expects. What it holds:\n")
            print(shape_report(samples))
            return 2
        if not title or not instr or len(instr) < 80:
            continue
        if title.lower() in seen_names:
            continue
        hits = mentions(ings + " " + instr, known)
        if len(hits) < 4:
            continue                      # too little of it is in your kitchen's language
        tried += 1
        print(f"[{added + 1}/{a.count}, tried {tried}] {title[:60]}")

        user = (f"Source recipe.\nTitle: {title}\nIngredients: {ings[:1500]}\n"
                f"Instructions: {instr[:2500]}")
        out, err = ask(base, model, e.get("OPENAI_API_KEY", ""),
                       SYSTEM.format(ids=ids), user, a.wait)
        if out is None:
            skipped += 1
            in_a_row += 1
            print(f"    {R}no recipe{O} — {err or 'the reply held no JSON'}")
            # Grinding through seventy-nine identical failures helps nobody.
            if in_a_row >= 3:
                print(f"\n{R}Three in a row failed the same way. Stopping.{O}")
                print("Nothing above this line is a recipe problem — it is the model")
                print(f"server at {base} refusing the request. The line above says why.")
                return 2
            continue
        in_a_row = 0

        faults = check(out, known)
        if faults:
            skipped += 1
            print(f"    {R}skipped{O} — " + "; ".join(faults[:3]))
            continue

        out["id"] = slug(out.get("name", title), taken)
        out["origin"] = "corpus"
        taken.add(out["id"])
        seen_names.add(str(out["name"]).lower())
        book["recipes"].append(out)
        added += 1
        print(f"    {G}kept{O} — {len(out['needs'])} ingredients, {len(out['steps'])} steps, "
              f"{out.get('minutes')} min")

        if not a.dry:
            if added == 1:
                shutil.copy2(book_path, book_path.with_suffix(".json.backup"))
            book_path.write_text(json.dumps(book, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")

    mins = (time.time() - t0) / 60
    print(f"\n{G}{added} added{O}, {skipped} skipped of {tried} tried, in {mins:.0f} min.")
    if a.dry:
        print("Nothing written (--dry).")
    elif added:
        print(f"shared/recipes.json now holds {len(book['recipes'])} recipes.")
        print(f"The original is at {book_path.with_suffix('.json.backup').name}.")
        print("Run  python3 audit.py  to read the result as a cook would.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())