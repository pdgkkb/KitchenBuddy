"""The list of dishes still waiting for a photograph.

Why this exists: SD-Turbo costs about 2.5 GB of resident memory, and on a 16 GB
Mac that is 2.5 GB the language model wants. Loading both at once is what turns
a twenty-second recipe into a seven-minute one, because the machine starts
swapping. So the server no longer generates pictures at all — it writes down
which dishes want one, and a separate program (`tools/make_photos.py`) does the
work later, on its own, when nothing else is competing for memory.

The queue is a plain JSON file beside the picture index, so both sides can be
read with `cat` and neither needs the other to be running.

    media/photo-queue.json   [{id, name, cuisine, description, asked}, …]
    media/recipes.json       {id: {urls, name, updated}}       (the results)

Nothing here is load-bearing. A queue that can't be written is a printed line
and a recipe with no photo, never an error the cook sees.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

QUEUE_NAME = "photo-queue.json"
MAX_QUEUED = 200          # a runaway loop shouldn't fill the disk with JSON


def path_for(media_path: Path) -> Path:
    return Path(media_path) / QUEUE_NAME


def read(media_path: Path) -> list[dict]:
    try:
        raw = json.loads(path_for(media_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in raw if isinstance(r, dict) and r.get("id")] if isinstance(raw, list) else []


def write(media_path: Path, rows: list[dict]) -> None:
    try:
        p = path_for(media_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows[:MAX_QUEUED], ensure_ascii=False, indent=1),
                     encoding="utf-8")
    except OSError as e:
        print("photo queue not written:", e)


def add(media_path: Path, recipe: dict) -> bool:
    """Note that this dish wants a picture. Returns True if it was new.

    The newest ask wins on name/description — a recipe renamed in the browser
    should be painted under its new name, not the one it had when first asked.
    """
    rid = str(recipe.get("id") or "").strip()
    if not rid:
        return False
    rows = read(media_path)
    row = {
        "id": rid,
        "name": str(recipe.get("name") or "")[:120],
        "cuisine": (str(recipe.get("cuisine")) if recipe.get("cuisine") else None),
        "description": (str(recipe.get("description"))[:400] if recipe.get("description") else None),
        "asked": int(time.time()),
    }
    existing = next((r for r in rows if r.get("id") == rid), None)
    if existing:
        existing.update({k: v for k, v in row.items() if k != "asked"})
        write(media_path, rows)
        return False
    rows.append(row)
    write(media_path, rows)
    return True


def remove(media_path: Path, recipe_id: str) -> None:
    rows = read(media_path)
    kept = [r for r in rows if r.get("id") != recipe_id]
    if len(kept) != len(rows):
        write(media_path, kept)


def waiting(media_path: Path, recipe_id: str) -> bool:
    return any(r.get("id") == recipe_id for r in read(media_path))
