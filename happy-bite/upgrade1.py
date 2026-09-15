#!/usr/bin/env python3
"""Happy Bite — when the request is bigger than the model's context window.

Put this file in your KitchenBuddy folder — the one with backend/ and
frontend/ — and run it from there, then restart the backend:

    python3 upgrade_happy_bite_14.py

    400 - {'error': 'The number of tokens to keep from the initial prompt is
    greater than the context length. Try to load the model with a larger
    context length, or provide a shorter input'}

That is not a bad request. The body is perfectly well formed. It means the
prompt plus the reply cannot both fit in the context window the model was
LOADED with — in LM Studio, a slider on the model, usually left at 4096
however much the model itself supports. A 3B that handles 32k will still
refuse at 4096.

Until now the app surfaced that as a raw 400 and stopped. Now it makes room:
it asks for half the reply and tries once more, because a shorter recipe beats
no recipe and most of that budget was head room nobody used. If it still will
not fit, the message names the three settings that move it — the LM Studio
slider, LLM_JSON_TOKENS and LLM_ID_LIMIT — instead of one of them.

    python3 upgrade_happy_bite_14.py --check    say what would change, change nothing
    python3 upgrade_happy_bite_14.py --show     print every edit as find/replace
    python3 upgrade_happy_bite_14.py --undo     put everything back

Everything it overwrites is copied to .happybite-backup/<timestamp>/ first, and
running it twice is safe — an edit already in place is left alone.

An edit is applied only where its anchor text appears EXACTLY ONCE. If a file
has drifted, that one edit is reported and skipped; nothing is guessed and
nothing is half-written.
"""

from __future__ import annotations

import ast
import base64
import gzip
import json
import re
import shutil
import sys
import time
from pathlib import Path

FRONT = "frontend/src"
RED, GREEN, YEL, DIM, OFF = "\033[31m", "\033[32m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    RED = GREEN = YEL = DIM = OFF = ""

PAYLOAD = (
    "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"
)

problems: list[str] = []


def bad(msg: str) -> None:
    problems.append(msg)
    print(f"  {RED}x{OFF} {msg}")


def good(msg: str) -> None:
    print(f"  {GREEN}+{OFF} {msg}")


def skip(msg: str) -> None:
    print(f"  {DIM}={OFF} {msg}")


def load() -> dict:
    return json.loads(gzip.decompress(base64.b64decode(PAYLOAD)).decode("utf-8"))


def find_root(start: Path) -> Path | None:
    """Where backend/ and frontend/ actually live — this folder, the one above,
    or a happy-bite/ inside either. Saves caring where you ran this from."""
    for c in (start, start / "happy-bite", start.parent, start.parent / "happy-bite",
              start / "KitchenBuddy" / "happy-bite"):
        try:
            if (c / "backend" / "app").is_dir() and (c / FRONT).is_dir():
                return c.resolve()
        except OSError:
            continue
    return None


# ----------------------------------------------------------------- verifying

def js_exports(text: str) -> set[str]:
    names: set[str] = set()
    for pat in (r"export\s+(?:async\s+)?function\s+(\w+)",
                r"export\s+(?:const|let|var|class)\s+(\w+)"):
        names.update(re.findall(pat, text))
    for group in re.findall(r"export\s*\{([^}]*)\}", text):
        names.update(p.strip().split(" as ")[-1].strip() for p in group.split(",") if p.strip())
    if re.search(r"export\s+default", text):
        names.add("default")
    return names


def py_defines(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


# (the file that calls it, the module it calls, the name) — every one of these
# is a crash the moment the screen renders, and none of them shows up until you
# open that screen. Cheaper to check than to find.
CALLS = []
BACK_IMPORTS = [("backend/app/providers/llm.py", "_is_too_long")]

GONE = []

CSS_USERS = []


def verify(root: Path, data: dict) -> None:
    """Prove it's wired in, rather than assuming the writes were enough."""
    read = lambda rel: (root / rel).read_text(encoding="utf-8")  # noqa: E731
    print("\nChecking it all hangs together")

    for rel in data["files"]:
        if not (root / rel).is_file():
            bad(f"{rel} — missing")

    for rel, module, name in CALLS:
        if (root / rel).is_file() and (root / module).is_file():
            if name not in js_exports(read(module)):
                bad(f"{Path(module).name} has no {name} — {Path(rel).name} imports it")

    for rel, name in BACK_IMPORTS:
        if (root / rel).is_file() and name not in py_defines(read(rel)):
            bad(f"{Path(rel).name} has no {name}")

    # Every class a JSX file uses must be defined in a stylesheet THAT FILE
    # imports — not one a parent happened to import. A component that only works
    # where it was first dropped is broken the second time it is used.
    # Classes a component uses must be defined in a stylesheet THAT FILE
    # imports — not one a parent happened to load first.
    for rel, klass_rx in CSS_USERS:
        if not (root / rel).is_file():
            continue
        body = read(rel)
        sheets = ""
        for imp in re.findall(r'import\s+"([^"]+\.css)"', body):
            p = (root / rel).parent / imp
            if p.is_file():
                sheets += p.read_text(encoding="utf-8")
            else:
                bad(f"{Path(rel).name} imports {imp}, which isn't there")
        for klass in sorted(set(re.findall(klass_rx, body))):
            if f".{klass}" not in sheets:
                bad(f"{Path(rel).name} uses .{klass}, no stylesheet it imports defines it")

    # The phrases the servers actually use, against the detector.
    v = root / "backend" / "app" / "providers" / "llm.py"
    if v.is_file():
        # llm.py does `from ..config import Settings`, so it imports only as
        # part of its package. If that fails it SAYS so — a check that quietly
        # skips itself looks exactly like a pass.
        import importlib
        saved = dict(sys.modules)
        added = str((root / "backend").resolve())
        sys.path.insert(0, added)
        mod = None
        try:
            for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
                del sys.modules[name]
            mod = importlib.import_module("app.providers.llm")
        except Exception as e:  # noqa: BLE001
            bad(f"couldn't load llm.py to test it ({type(e).__name__}: {e})")
        finally:
            try:
                sys.path.remove(added)
            except ValueError:
                pass
        if mod and not hasattr(mod, "_is_too_long"):
            bad("llm.py has no _is_too_long")
        elif mod:
            saying_yes = [
                "The number of tokens to keep from the initial prompt is greater than "
                "the context length. Try to load the model with a larger context length",
                "This model's maximum context length is 4096 tokens",
                "Requested tokens exceed context window of 4096",
                "context_length_exceeded",
                "Please reduce the length of the messages",
            ]
            saying_no = [
                "Request timed out", "unknown field 'keep_alive'",
                "'response_format.type' : value must be one of 'text'",
                "Model 'x' is not loaded",
            ]
            for text in saying_yes:
                if not mod._is_too_long(Exception(text)):
                    bad(f"a context-window refusal was not recognised: {text[:50]}…")
            for text in saying_no:
                if mod._is_too_long(Exception(text)):
                    bad(f"an unrelated 400 was mistaken for a context problem: {text[:50]}…")
            if not problems:
                good("a request that will not fit is told apart from one that is refused")
        sys.modules.clear()
        sys.modules.update(saved)

    t = (root / "backend" / "app" / "providers" / "llm.py").read_text(encoding="utf-8")
    if "asking for half" not in t:
        bad("llm.py gives up instead of asking for a shorter reply")
    if "_too_long_words" not in t:
        bad("a context-window refusal still comes back as a raw 400")

    for rel, pattern, msg in GONE:
        if (root / rel).is_file() and re.search(pattern, read(rel)):
            bad(msg)

    for path in list((root / "backend" / "app").rglob("*.py")) \
            + list((root / "backend" / "tools").rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad(f"{path.relative_to(root)} line {e.lineno}: {e.msg}")
        except OSError:
            pass

    # Every /api/... the browser calls must be declared by a route.
    routes = []
    for rel in ("backend/app/api.py", "backend/app/api_extra.py"):
        if (root / rel).is_file():
            routes += ["/api" + r for r in re.findall(r'@router\.\w+\("([^"]+)"', read(rel))]
    shapes = [re.compile("^" + re.sub(r"\\\{[^{}]*\\\}", "[^/]+", re.escape(r)) + "$") for r in routes]
    if (root / f"{FRONT}/lib/api.js").is_file():
        for path in sorted(set(re.findall(r'["`](/api/[^"`$\s]*)', read(f"{FRONT}/lib/api.js")))):
            hit = (any(r.startswith(path) and len(r) > len(path) for r in routes)
                   if path.endswith("/") else any(s.match(path) for s in shapes))
            if not hit:
                bad(f"{path} — the browser calls it, no route declares it")

    env = root / "backend" / ".env"
    if env.is_file() and "LLM_MODEL=" not in env.read_text(encoding="utf-8"):
        bad("backend/.env has no LLM_MODEL — the model change won't take effect")

    if not problems:
        good("every file present, every import resolves, every endpoint exists, "
             "the old behaviour is gone")


# ------------------------------------------------------- the catalogue itself

# Sorting by category only works if the categories are right. A vegetable you
# weigh in hundreds of grams filed under "seasoning" will keep turning up in
# the spice list however good the sorting code is, so the data is checked too —
# on your machine, where the file actually is. Matched on the NAME, exactly,
# never on a substring: "pepper" must not catch "Black pepper".
NOT_SEASONINGS = {
    "garlic", "onion", "onions", "red onion", "red onions", "white onion",
    "spring onion", "spring onions", "green onion", "green onions",
    "shallot", "shallots", "leek", "leeks", "celery", "carrot", "carrots",
    "tomato", "tomatoes", "cherry tomatoes", "bell pepper", "bell peppers",
    "courgette", "courgettes", "zucchini", "mushroom", "mushrooms",
}


def retune_catalogue(root: Path, check: bool, save) -> None:
    """Move whole vegetables out of the seasoning shelf, in place."""
    path = root / "shared" / "catalog.json"
    if not path.is_file():
        skip("shared/catalog.json — not found, categories left alone")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        bad(f"shared/catalog.json isn't valid JSON ({e}) — left alone")
        return
    cats = {c.get("id") for c in data.get("categories") or []}
    target = "produce" if "produce" in cats else None
    moved = []
    for iid, ing in (data.get("ingredients") or {}).items():
        if not isinstance(ing, dict) or ing.get("category") != "seasoning":
            continue
        if str(ing.get("name", "")).strip().lower() in NOT_SEASONINGS:
            moved.append((iid, ing.get("name")))
            if target and not check:
                ing["category"] = target
    if not moved:
        skip("shared/catalog.json — no vegetables filed as seasoning, nothing to move")
        return
    if not target:
        bad("shared/catalog.json has no 'produce' category — "
            + ", ".join(n for _, n in moved) + " left where they are")
        return
    for iid, name in moved:
        (skip if check else good)(f"{name} ({iid}) — seasoning -> produce")
    if not check:
        save(path)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ the .env

def apply_env(root: Path, entries: list[dict], check: bool, save) -> None:
    """The model name only takes effect here. Everything else is plumbing."""
    path = root / "backend" / ".env"
    body = path.read_text(encoding="utf-8") if path.is_file() else ""
    original = body
    for e in entries:
        key, value, force = e["key"], e["value"], e.get("force", True)
        rx = re.compile(rf"^{re.escape(key)}=(.*)$", re.M)
        found = rx.search(body)
        if found and not force:
            skip(f"{key} — already set to {found.group(1).strip() or '(blank)'}, left alone")
            continue
        if found and found.group(1).strip() == value:
            skip(f"{key} — already {value}")
            continue
        if check:
            skip(f"{key} — would become {value}"
                 + (f" (was {found.group(1).strip()})" if found else " (new)"))
            continue
        was = found.group(1).strip() if found else None
        # Every occurrence: a key set twice keeps the LAST one at run time, so
        # replacing only the first would change the file and nothing else.
        body = rx.sub(f"{key}={value}", body) if found \
            else body.rstrip("\n") + f"\n{key}={value}\n"
        good(f"{key}={value}" + (f"   (was {was})" if was else ""))
    if body != original and not check:
        save(path)
        path.write_text(body if body.startswith("#") or original else body.lstrip("\n"),
                        encoding="utf-8")


# ------------------------------------------------------------------ showing

def show(root: Path, data: dict) -> int:
    print(f"\nEvery edit, against {root}\n")
    todo = 0
    for rel, edits in data["patches"].items():
        path = root / rel
        body = path.read_text(encoding="utf-8") if path.is_file() else None
        print("=" * 76)
        print(rel if body is not None else f"{rel}   (NOT FOUND)")
        print("=" * 76)
        for n, e in enumerate(edits, 1):
            if body is not None and e["mark"] in body:
                print(f"\n  [{n}] already done")
                continue
            todo += 1
            where = ""
            if body is not None:
                hits = body.count(e["anchor"])
                if hits == 1:
                    where = f" — line {body[:body.index(e['anchor'])].count(chr(10)) + 1}"
                elif hits == 0:
                    where = " — ANCHOR NOT FOUND, this file has drifted"
                else:
                    where = f" — FOUND {hits} TIMES, choose yourself"
            print(f"\n  [{n}] FIND{where}:")
            for ln in e["anchor"].rstrip("\n").split("\n"):
                print(f"      | {ln}")
            print("      REPLACE WITH:")
            for ln in e["new"].rstrip("\n").split("\n"):
                print(f"      | {ln}")
        print()
    print("=" * 76)
    print("NEW FILES this script writes")
    print("=" * 76)
    for rel in data["files"]:
        print(f"  {'already there' if (root / rel).is_file() else 'not yet     '}  {rel}")
    print("=" * 76)
    print("backend/.env")
    print("=" * 76)
    for e in data["env"]:
        print(f"  {e['key']}={e['value']}" + ("" if e.get("force", True) else "   (only if absent)"))
    print(f"\n{todo} edit(s) outstanding. Run without --show to make them.")
    return 0


# -------------------------------------------------------------------- main

def main() -> int:
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    where = next((a for a in sys.argv[1:] if not a.startswith("-")), ".")

    root = find_root(Path(where).expanduser())
    if not root:
        print(f"{RED}Can't find the app.{OFF} Put this file in your KitchenBuddy folder —")
        print("the one containing backend/ and frontend/ — and run it from there:")
        print(f"\n    cd /path/to/KitchenBuddy\n    python3 {Path(sys.argv[0]).name}\n")
        return 2

    data = load()
    if "--show" in flags:
        return show(root, data)

    backups = root / ".happybite-backup"

    if "--undo" in flags:
        saved = sorted(p for p in backups.glob("*") if p.is_dir())
        if not saved:
            print("No backup to undo.")
            return 2
        newest = saved[-1]
        print(f"Restoring from .happybite-backup/{newest.name}")
        for src in sorted(newest.rglob("*")):
            if src.is_file():
                dst = root / src.relative_to(newest)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                good(str(dst.relative_to(root)))
        print("\nPut back. The new files it added are still there; delete them if you want.")
        return 0

    check = "--check" in flags
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backups / stamp

    def save(path: Path) -> None:
        if check or not path.exists():
            return
        dst = backup / path.relative_to(root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)

    print(f"\nHappy Bite — making room instead of giving up")
    print(f"{root}")
    print(f"{'Checking. Nothing will change.' if check else f'Backups in .happybite-backup/{stamp}'}\n")

    print("New files")
    for rel, text in data["files"].items():
        dst = root / rel
        if dst.is_file() and dst.read_text(encoding="utf-8") == text:
            skip(f"{rel} — already identical")
            continue
        verb = "replaced" if dst.exists() else "added"
        if check:
            skip(f"{rel} — would be {verb}")
            continue
        save(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        good(f"{rel} — {verb}")

    print("\nAppended blocks")
    for item in data["append"]:
        dst = root / item["path"]
        if not dst.is_file():
            bad(f"{item['path']} — not found")
            continue
        body = dst.read_text(encoding="utf-8")
        if item["mark"] in body:
            skip(f"{item['path']} — already there")
            continue
        if check:
            skip(f"{item['path']} — would append")
            continue
        save(dst)
        dst.write_text(body.rstrip("\n") + "\n" + item["text"], encoding="utf-8")
        good(f"{item['path']} — appended")

    print("\nEdits to files you already have")
    for rel, edits in data["patches"].items():
        dst = root / rel
        if not dst.is_file():
            bad(f"{rel} — not found, {len(edits)} edit(s) skipped")
            continue
        body = original = dst.read_text(encoding="utf-8")
        applied = done = 0
        for e in edits:
            if e["mark"] in body:
                done += 1
                continue
            hits = body.count(e["anchor"])
            if hits != 1:
                head = e["anchor"].strip().splitlines()[0][:66]
                bad(f"{rel} — anchor appears {hits} times, skipped:  {head}")
                continue
            body = body.replace(e["anchor"], e["new"], 1)
            applied += 1
        if body != original and not check:
            save(dst)
            dst.write_text(body, encoding="utf-8")
        state = ([f"{applied} {'to apply' if check else 'applied'}"] if applied else []) + \
                ([f"{done} already in place"] if done else [])
        if state:
            (skip if check else good)(f"{rel} — {', '.join(state)}")

    print("\nThe catalogue — what counts as a seasoning")
    retune_catalogue(root, check, save)

    print("\nbackend/.env — where the model name actually takes effect")
    apply_env(root, data["env"], check, save)

    if not check:
        verify(root, data)

    print()
    if problems:
        print(f"{YEL}{len(problems)} thing(s) need a hand.{OFF} Each line above names the file.")
        print("An anchor that isn't found means that file changed since this was written:")
        print(f"run  python3 {Path(sys.argv[0]).name} --show  to see the edit and make it yourself.")
        return 1
    if check:
        print(f"{GREEN}Everything matches.{OFF} Run it again without --check to apply.")
        return 0

    print(f"{GREEN}Done, and verified.{OFF} Next:\n")
    print("  Restart the backend and try the small model again.\n")
    print("  THE REAL FIX IS IN LM STUDIO, and it takes ten seconds. Select the")
    print("  loaded model, find Context Length in its load settings, and raise it")
    print("  from 4096 to 8192 or 16384. Reload the model. Qwen 2.5 3B handles")
    print("  32768; the slider, not the model, is what refused you.\n")
    print("  A longer context costs a little memory and nothing in speed for a")
    print("  prompt that doesn't use it.\n")
    print("  This installer is the safety net underneath that: the app now asks")
    print("  for a shorter reply and tries again rather than stopping, and if it")
    print("  still won't fit it names all three settings that move it.\n")
    print(f"  Changed your mind?  python3 {Path(sys.argv[0]).name} --undo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())