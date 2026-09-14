#!/usr/bin/env python3
"""Install the Happy Bite upgrade into a KitchenBuddy checkout.

    python3 apply.py --check /path/to/KitchenBuddy     # say what would happen
    python3 apply.py         /path/to/KitchenBuddy     # do it
    python3 apply.py --undo  /path/to/KitchenBuddy     # put it back

What it does, and what it refuses to do:

* New files are copied in. An existing file of the same name is backed up first.
* Existing files are edited by exact anchored replacement. Every anchor must
  appear EXACTLY ONCE. If it appears twice, or not at all, that patch is
  reported and SKIPPED — nothing is guessed, nothing is half-applied.
* Everything it overwrites goes to .happybite-backup/<timestamp>/ first, and
  --undo restores the most recent one.
* Re-running is safe: a patch whose marker is already in the file is left alone.

The exit code is 0 when everything applied, 1 when anything was skipped, so it
is honest to a script as well as to you.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import patches  # noqa: E402

# ---------------------------------------------------------------- new files
# Source under files/  ->  destination inside the repo. Anything already there
# is backed up, not clobbered.

NEW = [
    "backend/app/providers/media.py",
    "backend/app/main.py",
    "backend/app/api_extra.py",
    "backend/app/imagestore.py",
    "backend/app/prefetch.py",
    "backend/app/receipt.py",
    "backend/app/warmup.py",
    "backend/setup_local.sh",
    "frontend/src/lib/speechqueue.js",
    "frontend/src/components/PhotoStrip.jsx",
    "frontend/src/components/ReceiptButton.jsx",
    "frontend/src/styles/happy-extra.css",
]

# Text appended to a file only if its marker isn't already in it.
APPEND = [
    ("backend/requirements.txt", "# --- added by the Happy Bite upgrade", """

# --- added by the Happy Bite upgrade -------------------------------------
# These are what the app was ALREADY trying to import. Without them the local
# providers raised ModuleNotFoundError on first use and the browser showed a
# bare exception name; the server now checks for them at start-up instead.

# Local pictures (IMAGE_PROVIDER=local) — Stable Diffusion Turbo via diffusers
torch>=2.2
diffusers>=0.27
transformers>=4.40
accelerate>=0.30
safetensors>=0.4
pillow>=10.0

# Local speaking (SPEECH_PROVIDER=local, TTS_ENGINE=kokoro)
kokoro>=0.7
soundfile>=0.12
misaki[en]>=0.7

# Reading till receipts — the character recogniser. Needs the tesseract program
# too:  brew install tesseract tesseract-lang
pytesseract>=0.3.10
"""),
]


def fail(msg: str) -> None:
    print(f"  \033[31m!\033[0m {msg}")


def ok(msg: str) -> None:
    print(f"  \033[32m+\033[0m {msg}")


def skip(msg: str) -> None:
    print(f"  \033[33m=\033[0m {msg}")


def find_root(given: Path) -> Path | None:
    """The folder that holds backend/ and frontend/ — with or without the
    happy-bite/ wrapper, since the repo root has it and a checkout might not."""
    for candidate in (given, given / "happy-bite"):
        if (candidate / "backend" / "app").is_dir() and (candidate / "frontend" / "src").is_dir():
            return candidate
    return None


def show(root: Path) -> int:
    """Every edit, written out, with whether it's needed in this checkout.

    Copying the new files across by hand is only half the job — they are
    imported by nothing until these edits are made. This prints them so the
    other half can be done by hand too, if you'd rather see each change than
    run a script over your repo.
    """
    print(f"\nEdits to existing files in {root}")
    print("The 12 NEW files are listed at the end. Neither half works without the other.\n")
    todo = 0
    for rel, edits in patches.P.items():
        path = root / rel
        body = path.read_text(encoding="utf-8") if path.is_file() else None
        print("=" * 78)
        print(rel if body is not None else f"{rel}   (NOT FOUND)")
        print("=" * 78)
        for n, e in enumerate(edits, 1):
            if body is not None and e["mark"] in body:
                print(f"\n  [{n}] already done\n")
                continue
            todo += 1
            where = ""
            if body is not None:
                hits = body.count(e["anchor"])
                if hits == 1:
                    line = body[: body.index(e["anchor"])].count("\n") + 1
                    where = f" — at line {line}"
                elif hits == 0:
                    where = " — ANCHOR NOT FOUND, this file has drifted"
                else:
                    where = f" — ANCHOR FOUND {hits} TIMES, pick the right one yourself"
            print(f"\n  [{n}] FIND{where}:")
            for ln in e["anchor"].rstrip("\n").split("\n"):
                print(f"      | {ln}")
            print("      REPLACE WITH:")
            for ln in e["new"].rstrip("\n").split("\n"):
                print(f"      | {ln}")
        print()
    print("=" * 78)
    print("NEW FILES — copy these from files/ into the same path under the repo")
    print("=" * 78)
    for rel in NEW:
        here = (root / rel).is_file()
        print(f"  {'already there' if here else 'MISSING     '}  {rel}")
    for rel, marker, _ in APPEND:
        path = root / rel
        done = path.is_file() and marker in path.read_text(encoding="utf-8")
        print(f"  {'already there' if done else 'MISSING     '}  {rel}  (appended block)")
    print(f"\n{todo} edit(s) still to make. `python3 apply.py <repo>` does all of them,")
    print("backs up what it touches, and `--undo` puts it back.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the Happy Bite upgrade.")
    ap.add_argument("repo", help="path to the KitchenBuddy checkout")
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    ap.add_argument("--undo", action="store_true", help="restore the newest backup")
    ap.add_argument("--show", action="store_true",
                    help="print every edit as find/replace so you can do it by hand")
    args = ap.parse_args()

    given = Path(args.repo).expanduser().resolve()
    root = find_root(given)
    if not root:
        print(f"Not a KitchenBuddy checkout: {given}")
        print("Expected backend/app and frontend/src under it (or under happy-bite/).")
        return 2

    backups = root / ".happybite-backup"

    if args.undo:
        saved = sorted(p for p in backups.glob("*") if p.is_dir())
        if not saved:
            print("No backup to undo.")
            return 2
        newest = saved[-1]
        print(f"Restoring from {newest.name}")
        for src in sorted(newest.rglob("*")):
            if src.is_file():
                dst = root / src.relative_to(newest)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                ok(str(dst.relative_to(root)))
        return 0

    if args.show:
        return show(root)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = backups / stamp
    problems = 0

    def save(path: Path) -> None:
        if args.check or not path.exists():
            return
        dst = backup / path.relative_to(root)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)

    print(f"\nKitchenBuddy at {root}")
    print(f"{'Checking' if args.check else 'Applying'} — backups in .happybite-backup/{stamp}\n")

    # ---------------------------------------------------------------- files
    print("New and replaced files")
    for rel in NEW:
        src = HERE / "files" / rel
        dst = root / rel
        if not src.is_file():
            fail(f"{rel} — missing from this bundle")
            problems += 1
            continue
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            skip(f"{rel} — already identical")
            continue
        verb = "replace" if dst.exists() else "add"
        if args.check:
            skip(f"{rel} — would {verb}")
            continue
        save(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if rel.endswith(".sh"):
            dst.chmod(0o755)
        ok(f"{rel} — {verb}d")

    # -------------------------------------------------------------- appends
    print("\nAppended blocks")
    for rel, marker, text in APPEND:
        dst = root / rel
        if not dst.is_file():
            fail(f"{rel} — not found")
            problems += 1
            continue
        body = dst.read_text(encoding="utf-8")
        if marker in body:
            skip(f"{rel} — already there")
            continue
        if args.check:
            skip(f"{rel} — would append")
            continue
        save(dst)
        dst.write_text(body.rstrip("\n") + "\n" + text, encoding="utf-8")
        ok(f"{rel} — appended")

    # -------------------------------------------------------------- patches
    print("\nPatches")
    for rel, edits in patches.P.items():
        dst = root / rel
        if not dst.is_file():
            fail(f"{rel} — not found, {len(edits)} patch(es) skipped")
            problems += len(edits)
            continue
        body = dst.read_text(encoding="utf-8")
        original = body
        applied = done = 0
        for e in edits:
            if e["mark"] in body:
                done += 1
                continue
            hits = body.count(e["anchor"])
            if hits != 1:
                head = e["anchor"].strip().splitlines()[0][:72]
                fail(f"{rel} — anchor found {hits} times, skipped:  {head}")
                problems += 1
                continue
            body = body.replace(e["anchor"], e["new"], 1)
            applied += 1
        if body != original and not args.check:
            save(dst)
            dst.write_text(body, encoding="utf-8")
        state = []
        if applied:
            state.append(f"{applied} {'would apply' if args.check else 'applied'}")
        if done:
            state.append(f"{done} already in place")
        if state:
            (skip if args.check else ok)(f"{rel} — {', '.join(state)}")

    # --------------------------------------------------------------- report
    print()
    if problems:
        print(f"\033[33m{problems} item(s) need a hand.\033[0m An anchor that isn't found "
              "means that file has\nchanged since this patch was written — open it, find the "
              "text quoted above,\nand make the edit by hand. Nothing was half-applied.")
    elif args.check:
        print("\033[32mEverything matches.\033[0m Run again without --check to apply it.")
    else:
        print("\033[32mDone.\033[0m Next:")
        print("    cd backend && source .venv/bin/activate && ./setup_local.sh")
        print("    uvicorn app.main:app --reload --port 8000")
        print("    open http://localhost:8000/api/health/models")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
