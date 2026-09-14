#!/usr/bin/env python3
"""Check that the upgrade is actually, completely wired in.

    python3 verify.py /path/to/KitchenBuddy

"It applied cleanly" and "it works" are different claims. apply.py proves the
first: every anchor was found and every file was written. This proves the parts
of the second that can be proved without running anything —

  1  every file that should exist, exists
  2  every module our files import can be resolved, and exports what we ask for
  3  every api.* / store method our components call is actually defined
  4  every CSS class our JSX uses is defined in a stylesheet THAT FILE IMPORTS
     (an import in a parent component is not good enough to rely on)
  5  no reference survives to something a patch removed
  6  every Python file compiles

It reads the repo; it changes nothing. Exit code 0 means clean.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

FRONT = "frontend/src"

# --- 1. what must exist ------------------------------------------------------

MUST_EXIST = [
    "backend/app/providers/media.py", "backend/app/main.py", "backend/app/api_extra.py",
    "backend/app/imagestore.py", "backend/app/prefetch.py", "backend/app/receipt.py",
    "backend/app/warmup.py", "backend/setup_local.sh",
    f"{FRONT}/lib/speechqueue.js", f"{FRONT}/components/PhotoStrip.jsx",
    f"{FRONT}/components/ReceiptButton.jsx", f"{FRONT}/styles/happy-extra.css",
]

# --- 3. cross-module calls the new code depends on ---------------------------
# (file that calls it, module it calls into, the name)

CALLS = [
    (f"{FRONT}/components/PhotoStrip.jsx",    f"{FRONT}/lib/api.js", "makeRecipeImages"),
    (f"{FRONT}/components/PhotoStrip.jsx",    f"{FRONT}/lib/api.js", "recipeImages"),
    (f"{FRONT}/components/PhotoStrip.jsx",    f"{FRONT}/lib/api.js", "clearRecipeImages"),
    (f"{FRONT}/components/ReceiptButton.jsx", f"{FRONT}/lib/api.js", "readReceipt"),
    (f"{FRONT}/screens/Receipt.jsx",          f"{FRONT}/lib/api.js", "readReceipt"),
    (f"{FRONT}/screens/CookMode.jsx",         f"{FRONT}/lib/api.js", "prefetchStep"),
    (f"{FRONT}/screens/CookMode.jsx",         f"{FRONT}/lib/api.js", "quickAnswer"),
    (f"{FRONT}/sheets/RecipeSheet.jsx",       f"{FRONT}/lib/api.js", "makeRecipeImages"),
    (f"{FRONT}/lib/speechqueue.js",           f"{FRONT}/lib/voice.js", "markSpoken"),
    (f"{FRONT}/components/Chat.jsx",          f"{FRONT}/lib/speechqueue.js", "feed"),
    (f"{FRONT}/screens/CookMode.jsx",         f"{FRONT}/lib/speechqueue.js", "arm"),
    (f"{FRONT}/screens/CookMode.jsx",         f"{FRONT}/lib/speechqueue.js", "disarm"),
    (f"{FRONT}/screens/CookMode.jsx",         f"{FRONT}/lib/speechqueue.js", "cancel"),
    (f"{FRONT}/screens/CookMode.jsx",         f"{FRONT}/state/actions.jsx", "useApplyAction"),
]

# Store methods the action layer calls, and where they must be defined.
STORE_METHODS = [("addCategory", f"{FRONT}/state/kitchen.jsx"),
                 ("addCustom", f"{FRONT}/state/kitchen.jsx"),
                 ("setPhoto", f"{FRONT}/state/kitchen.jsx"),
                 ("setReceipt", f"{FRONT}/state/kitchen.jsx")]

# --- 4. every class in our stylesheet, and who may use it --------------------

CSS_FILE = f"{FRONT}/styles/happy-extra.css"
CSS_USERS = [
    f"{FRONT}/components/PhotoStrip.jsx",
    f"{FRONT}/components/ReceiptButton.jsx",
    f"{FRONT}/screens/CookMode.jsx",
    f"{FRONT}/screens/Shopping.jsx",
    f"{FRONT}/sheets/RecipeSheet.jsx",
]

# --- 5. things a patch should have removed ----------------------------------

GONE = [
    (f"{FRONT}/screens/Kitchen.jsx", r"\bCAT\[",
     "Kitchen.jsx still indexes the old frozen CAT map — catOf() replaced it with catById()"),
    (f"{FRONT}/screens/CookMode.jsx", r"useChat\(context,\s*null\)\s*;",
     "CookMode still calls useChat without onAction — kitchen actions won't fire while cooking"),
    (f"{FRONT}/screens/Receipt.jsx", r"setTimeout\(load,\s*700\)",
     "Receipt.jsx still replays the sample receipt instead of reading the photo"),
]

# --- 6. backend imports -------------------------------------------------------

BACKEND_IMPORTS = [
    ("backend/app/main.py", "backend/app/providers/media.py", "make_images_checked"),
    ("backend/app/main.py", "backend/app/providers/media.py", "make_speech_checked"),
    ("backend/app/main.py", "backend/app/imagestore.py", "RecipeImages"),
    ("backend/app/main.py", "backend/app/prefetch.py", "Prefetcher"),
    ("backend/app/main.py", "backend/app/api_extra.py", "router"),
    ("backend/app/api_extra.py", "backend/app/receipt.py", "ocr_available"),
    ("backend/app/api_extra.py", "backend/app/receipt.py", "read"),
]

RED, GREEN, YEL, OFF = "\033[31m", "\033[32m", "\033[33m", "\033[0m"
problems: list[str] = []


def bad(msg: str) -> None:
    problems.append(msg)
    print(f"  {RED}x{OFF} {msg}")


def good(msg: str) -> None:
    print(f"  {GREEN}+{OFF} {msg}")


def note(msg: str) -> None:
    print(f"  {YEL}·{OFF} {msg}")


def find_root(given: Path) -> Path | None:
    for c in (given, given / "happy-bite"):
        if (c / "backend" / "app").is_dir() and (c / FRONT).is_dir():
            return c
    return None


def js_exports(text: str) -> set[str]:
    """Names a JS module exports. Covers the forms this codebase uses."""
    names: set[str] = set()
    for pat in (r"export\s+(?:async\s+)?function\s+(\w+)",
                r"export\s+(?:const|let|var|class)\s+(\w+)",
                r"export\s+default\s+(?:async\s+)?function\s+(\w+)"):
        names.update(re.findall(pat, text))
    for group in re.findall(r"export\s*\{([^}]*)\}", text):
        for part in group.split(","):
            piece = part.strip().split(" as ")[-1].strip()
            if piece:
                names.add(piece)
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
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    root = find_root(Path(sys.argv[1]).expanduser().resolve())
    if not root:
        print(f"Not a KitchenBuddy checkout: {sys.argv[1]}")
        return 2

    read = lambda rel: (root / rel).read_text(encoding="utf-8")  # noqa: E731
    print(f"\nVerifying {root}\n")

    # 1 ------------------------------------------------------------------
    print("Files in place")
    for rel in MUST_EXIST:
        (good if (root / rel).is_file() else bad)(
            rel if (root / rel).is_file() else f"{rel} — MISSING")

    # 2 ------------------------------------------------------------------
    print("\nImports resolve")
    for rel in MUST_EXIST + [f"{FRONT}/screens/CookMode.jsx", f"{FRONT}/components/Chat.jsx",
                             f"{FRONT}/screens/Shopping.jsx", f"{FRONT}/sheets/RecipeSheet.jsx",
                             f"{FRONT}/screens/Receipt.jsx", f"{FRONT}/state/kitchen.jsx"]:
        path = root / rel
        if not path.is_file() or path.suffix not in (".js", ".jsx"):
            continue
        for spec in re.findall(r'from\s+"(\.[^"]+)"|import\s+"(\.[^"]+)"', read(rel)):
            target = spec[0] or spec[1]
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                bad(f"{rel} imports {target} — no such file")
    if not problems:
        good("every relative import in the touched files points at a real file")

    # 3 ------------------------------------------------------------------
    print("\nCross-module calls")
    for caller, module, name in CALLS:
        if not (root / caller).is_file() or not (root / module).is_file():
            bad(f"{caller} or {module} missing")
            continue
        if name not in js_exports(read(module)):
            bad(f"{Path(module).name} does not export {name}() — called from {Path(caller).name}")
    for name, where in STORE_METHODS:
        if not re.search(rf"\b{name}\s*[({{:]", read(where)):
            bad(f"{Path(where).name} has no {name}")
    if not any("does not export" in p or " has no " in p for p in problems):
        good(f"all {len(CALLS) + len(STORE_METHODS)} cross-module calls resolve")

    # 4 ------------------------------------------------------------------
    print("\nStylesheet reaches the markup that needs it")
    css = read(CSS_FILE) if (root / CSS_FILE).is_file() else ""
    defined = set(re.findall(r"\.([a-z][a-z0-9-]+)", css))
    for rel in CSS_USERS:
        if not (root / rel).is_file():
            continue
        body = read(rel)
        used = {c for blob in re.findall(r'className=[{"\s\w+()?:&|.\[\]-]*?"([^"]*)"', body)
                for c in blob.split() if c in defined}
        if not used:
            continue
        imports_css = 'styles/happy-extra.css"' in body
        if imports_css:
            good(f"{Path(rel).name} uses {len(used)} of our classes and imports the stylesheet")
        else:
            bad(f"{Path(rel).name} uses {sorted(used)} but does NOT import happy-extra.css")
    orphan = defined - {c for rel in CSS_USERS if (root / rel).is_file()
                        for blob in re.findall(r'"([^"]*)"', read(rel)) for c in blob.split()}
    if orphan:
        note(f"defined but unused: {', '.join(sorted(orphan))}")

    # 5 ------------------------------------------------------------------
    print("\nOld code actually replaced")
    for rel, pattern, msg in GONE:
        if (root / rel).is_file() and re.search(pattern, read(rel)):
            bad(msg)
        else:
            good(msg.split(" — ")[0].replace(" still ", " no longer "))

    # 6 ------------------------------------------------------------------
    print("\nBackend")
    for rel in sorted(p.relative_to(root).as_posix() for p in (root / "backend/app").rglob("*.py")):
        try:
            ast.parse(read(rel))
        except SyntaxError as e:
            bad(f"{rel} line {e.lineno}: {e.msg}")
    for caller, module, name in BACKEND_IMPORTS:
        if not (root / module).is_file():
            bad(f"{module} missing")
        elif name not in py_defines(read(module)):
            bad(f"{Path(module).name} has no {name} — imported by {Path(caller).name}")
    if not any(rel in p for p in problems for rel in ("backend/",)):
        good("every backend module compiles and every cross-import resolves")

    # 7 ------------------------------------------------------------------
    # A call the browser makes to a route nobody declared is a 404 you only find
    # by pressing the button, so check the two sides against each other.
    print("\nEvery endpoint the browser calls exists on the server")
    routes = []
    for rel in ("backend/app/api.py", "backend/app/api_extra.py"):
        if (root / rel).is_file():
            routes += ["/api" + r for r in re.findall(r'@router\.\w+\("([^"]+)"', read(rel))]
    # A declared {param} matches any single segment.
    shapes = [re.compile("^" + re.sub(r"\\\{[^{}]*\\\}", "[^/]+", re.escape(r)) + "$")
              for r in routes]

    def declared(path: str) -> bool:
        if path.endswith("/"):          # `/api/x/${id}` — the id was interpolated away
            return any(r.startswith(path) and len(r) > len(path) for r in routes)
        return any(s.match(path) for s in shapes)

    for path in sorted(set(re.findall(r'["`](/api/[^"`$\s]*)', read(f"{FRONT}/lib/api.js")))):
        shown = path.rstrip("/") + "/{...}" if path.endswith("/") else path
        (good if declared(path) else bad)(
            shown if declared(path) else f"{shown} — no route declares it")

    # ---------------------------------------------------------------------
    print()
    if problems:
        print(f"{RED}{len(problems)} problem(s).{OFF} Each line above names the file and what's wrong.")
        return 1
    print(f"{GREEN}Everything is wired in.{OFF}")
    print("Still unproven by this script (it runs nothing): that SD-Turbo makes a")
    print("picture, that Kokoro speaks, that tesseract reads your receipts. For those:")
    print("    curl -s localhost:8000/api/health/models | python3 -m json.tool")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
