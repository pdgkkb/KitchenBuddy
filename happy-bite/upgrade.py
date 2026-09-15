#!/usr/bin/env python3
"""Happy Bite — a catalogue as wide as the corpus, a prompt that stays short.

Put this file in your KitchenBuddy folder — the one with backend/ and
frontend/ — and run it from there:

    python3 upgrade_happy_bite_13.py

Then restart the backend.

Run this WITH tools/build_catalogue.py, which adds every ingredient the corpus
knows to shared/catalog.json. On its own that import would make the app
unusable: `id_list()` puts every ingredient in the catalogue in front of the
model on EVERY question, so ten thousand of them is about ninety thousand
characters to read before a single word is written. At fifteen tokens a second
that is minutes, every time you ask anything.

So the catalogue and the prompt are separated. The catalogue keeps everything
— the app can name, track and draw an icon for any of it. The prompt gets a
shortlist: everything from the original catalogue and everything your
household has created, always, plus the most-used corpus imports up to
LLM_ID_LIMIT (400 by default, in backend/.env).

An ingredient left out of the prompt is not forgotten. A recipe that names it
still validates, the kitchen still knows what it is. It is only not suggested.

    python3 upgrade_happy_bite_13.py --check    say what would change, change nothing
    python3 upgrade_happy_bite_13.py --show     print every edit as find/replace
    python3 upgrade_happy_bite_13.py --undo     put everything back

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
    "H4sIAFIuqWoC/8VX32/bNhD+Vw7eQyTMdd2uGDAPKbACwTZg6x5aYA92ENMSLbGmSJWk4hlZ/vd9R0q2HKfbHgbMRqwfPH53PH73"
    "HfMw2Sot/WRBD49TmrQiFHV6nGxEsZOmfCna9mVhzVZVs/aAkeXDpBFuh7uJ1s2dKu+0alSYYLowRW0djxA+PPrJW3MXLID8gpQJ"
    "dE3fzOfzlWFzI/f/ynZl2OQr+snuqRHmQIV1bedhUzlZKmmCp1BLamwpNSlPvrZ7M6OPeFeIILStOr4zVFtdDmihtp0XpvS06l7P"
    "X70heS/dYQQaMXtXO2P3fkrexpdISYQzosGDOUE65Iw4Qrtlw2aA3nQJrXW2aQPPNZYvDg7ZDrFKc4yIVOkHSKzG2a6q9YG+m0+R"
    "DipqATdBOk4AbZ1FpHCXovcIX0v63EkflDVT2teqqEmE06qB2IiiVkZeIVOtlCU7aZTpgvSM5KQoAUMbubUO66O9ddFm71QI0swG"
    "rBt2CUDYKhPXZ52qlBF6lHZejzwZshUvU/JWUC2OCy3gNqRYhN6LA2LDHnyfAi5Ei9CMPoz3RDWtdQG7sjkAco/QEdxpnUc7JKsT"
    "GnPhtUzpTlZj+g6MexPJ+XgLep4VQFrPRQWUckt3oJsLWvmQMU3MZSU4GTpnaIWR1WT2ySqTpQj4s11NHpQqH+H8ARmaVTJkV8ys"
    "qynhff5I2el9Z1TA+6vqKn/MV5MRinVsPaV+N2IkMxVk47M8v6i3/yGip2maUkw8B8ff5zK5oFIVYemDm8a7235O2qw/6b01MqcX"
    "b4mnLEPXajkyvl2kYBAUvr/HQviiZBxJkrRjEJ1zCVF+KH+Uzr0txKbTAlUXazbw1A6k3sACxaLKeMWEBNWzsVcEqMdIaTAZpUCG"
    "C4VqKVBvKMReJoDHyUcmo77gJgFW6h6iwUAFVzrSHQmOahDY30K1ckbvzoUHC2DEMF5VXwwfQPVYoE+E6BmRGdRlLE4GehIOCeo4"
    "fSRVp2Q3nQ9RYwaBYVlhz0lopjGbABd9kSLNelAswIgwkqtpH1FQjTzu2QeI9B5/Cn4TGjv3rVZxKvKDbCB1qFFJTmDQMa7hkSB8"
    "kIsBiQYporGEfVHrppdip/ypIPrPmfodZQ8pbtK+SdWG2Ls8+ONk9BXhLqFiNtjmyF7E5m0jN7Y8gKI7xe18EGxiGW3hi9AVMHEd"
    "rNX+5aZTurw70aE9rHtpHcI6aen0IoTG+vAi8m6rnEfgv0IZ8bReTURZynIFxVlN0uzVZA3P4Bc2Yo+1R4NLyL0KNc8HapyCnIAX"
    "nM/6yQEgsRy1JkxsHeHIgR/gpCQtt6BtF/p2PKqC9799ZIGqLDe0Ba1HupDlawK9tU5ISSlRBiG2/6G0uCQjGUIypnuhVSm4hQ6M"
    "67M/BovHiJT9SMYZ/RzDia2NK9N3VYXikuVsrF3pXm2T+sX4oXyLkdbyNs3SKa3fPLTPEM5ZE9xhcZ7thHfNcppBzUUILhvmZTn3"
    "hnGP5G6B/piz1MzzE5L8o5BI6028QBcW3ICN/SwW9O6Xm/n81fNOY6s9Fdl02NVrWkLol7dp8G8b22g52XAq2NLQm44UzOn6esRC"
    "kpprK3rNZ1wTpsyywUmeP0k376eWpneQ09u+CZ18p5GZR9qznTxca9FsSkG7+wW94NTu7pevbvuQEqv7HOZPMbD4dLNcRCe3YxIO"
    "avR1b8MHlSmdDiOCjv0TXJUSAqvt/vw4wm027ucXe2xsqHg8657r7q6ygnftR1wgLWA2C1RWgSWyqjBwg9+sY9Kgw73+dn125vhn"
    "t8+0doAeO/x/EBAde++zeZrFcx//F5QYwQc9PgZKcx9vH/8CgoNwbywNAAA="
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
BACK_IMPORTS = [("backend/app/catalog.py", "_shortlist")]

GONE = [
    ("backend/app/catalog.py", r"for iid, ing in known\.items\(\)\)",
     "catalog.py still sends the whole catalogue to the model"),
]

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

    # Proved on a catalogue shaped like the one build_catalogue.py produces.
    # catalog.py does `from .config import ROOT`, so it only imports as part of
    # its package — hence the stand-in package below rather than a bare exec.
    # If that fails it SAYS so; a check that quietly skips itself is worse than
    # no check, because it looks like a pass.
    cat = root / "backend" / "app" / "catalog.py"
    if cat.is_file():
        import importlib, importlib.util as _u
        saved = dict(sys.modules)
        added = str((root / "backend").resolve())
        sys.path.insert(0, added)
        mod = None
        try:
            for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
                del sys.modules[name]
            mod = importlib.import_module("app.catalog")
        except Exception as e:  # noqa: BLE001
            bad(f"couldn't load catalog.py to test it ({type(e).__name__}: {e})")
        finally:
            try:
                sys.path.remove(added)
            except ValueError:
                pass
        if mod is not None and not hasattr(mod, "_shortlist"):
            bad("catalog.py has no _shortlist")
        elif mod is not None:
            known = {"egg": {"name": "Egg", "unit": "u"},
                     "u_mine": {"name": "Something I added", "unit": "g"}}
            for i in range(50):
                known[f"c_x{i}"] = {"name": f"Corpus {i}", "unit": "g",
                                    "added": "corpus", "uses": i}
            got = dict(mod._shortlist(known, 10))
            if "egg" not in got:
                bad("the shortlist drops the original catalogue")
            if "u_mine" not in got:
                bad("the shortlist drops what the household created")
            corpus = [k for k in got if k.startswith("c_x")]
            if len(corpus) != 10:
                bad(f"the shortlist kept {len(corpus)} corpus ingredients, expected 10")
            if "c_x49" not in got or "c_x0" in got:
                bad("the shortlist kept the least-used corpus ingredients")
            if len(dict(mod._shortlist(known, 0))) != len(known):
                bad("a limit of 0 should mean no cap, and does not")
            if "egg = Egg (u)" not in mod.id_list(known, 10):
                bad("id_list no longer writes the names beside the ids")
            if not problems:
                good("the shortlist keeps yours, caps the corpus, and keeps the busiest")
        sys.modules.clear()
        sys.modules.update(saved)

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

    print(f"\nHappy Bite — a wide catalogue, a short prompt")
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
    print("  Restart the backend.\n")
    print("  Then grow the catalogue, looking first:\n")
    print("     mkdir -p tools")
    print("     mv ~/Downloads/build_catalogue.py tools/")
    print("     python3 tools/build_catalogue.py --dry\n")
    print("  It prints how many it would add, what it guessed for each, and how")
    print("  many it could not place. If that reads sensibly, run it without")
    print("  --dry. It never changes an ingredient that already exists, so your")
    print("  stock and saved recipes keep pointing at the same ids.\n")
    print("  To send the model more or fewer of them, put this in backend/.env:\n")
    print("     LLM_ID_LIMIT=400\n")
    print("  Higher is a wider vocabulary and a slower answer. Measure it:")
    print("  python3 check_model.py before and after.\n")
    print(f"  Changed your mind?  python3 {Path(sys.argv[0]).name} --undo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())