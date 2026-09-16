"""Small, local recipe retrieval over the large source corpus.

The corpus is JSONL in the training format. SQLite FTS5 gives us a persistent
index with no extra service or Python dependency; the index is rebuilt only
when the source file changes and is populated in a background thread at start.

WHAT CHANGED
------------
Ingredient and instruction lines are now stored NEWLINE-separated rather than
joined with spaces. They used to be flattened into one string, which is fine
for full-text search — FTS5 tokenises on whitespace either way — and useless
for anything that needs to read the list back. `app/template.py` scores a
candidate by how many of its ingredient LINES the kitchen already has, and it
cannot do that against "4 chicken thighs 2 tablespoons olive oil 1 large onion".

Because the rebuild is keyed on the source file's size and mtime, changing the
shape of the stored rows would not on its own trigger one: you would get the
new code reading an old index for ever. Hence `INDEX_VERSION` in the meta
table. Bump it whenever `_build` changes what it writes.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path

# Bump this when _build changes the shape of what it stores, or an existing
# index will be reused for ever — the size/mtime check only notices a changed
# SOURCE FILE, never changed code.
INDEX_VERSION = "2"


class RecipeRetriever:
    def __init__(self, source: Path, index: Path, enabled: bool = True, top_k: int = 4):
        self.source = Path(source)
        self.index = Path(index)
        self.enabled = bool(enabled)
        self.top_k = max(1, min(8, int(top_k or 4)))
        self.ready = False
        self.building = False
        self.error: str | None = None

    async def start(self) -> None:
        if self.enabled:
            asyncio.create_task(asyncio.to_thread(self._prepare))

    async def search(self, query: str, limit: int | None = None) -> list[dict]:
        if not self.enabled or not self.ready or not str(query or "").strip():
            return []
        return await asyncio.to_thread(self._search, query, limit or self.top_k)

    def _prepare(self) -> None:
        self.building = True
        try:
            self.index.parent.mkdir(parents=True, exist_ok=True)
            source_stat = self.source.stat()
            with sqlite3.connect(self.index) as db:
                db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                old = dict(db.execute("SELECT key, value FROM meta"))
                current = {
                    "size": str(source_stat.st_size),
                    "mtime": str(source_stat.st_mtime_ns),
                    "version": INDEX_VERSION,
                }
                if any(old.get(k) != v for k, v in current.items()):
                    self._build(db)
                    db.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", current.items())
                    db.commit()
            self.ready = True
        except Exception as exc:  # retrieval is an enhancement, never a startup failure
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.building = False

    def _build(self, db: sqlite3.Connection) -> None:
        db.execute("DROP TABLE IF EXISTS recipes")
        db.execute("DROP TABLE IF EXISTS recipes_fts")
        db.execute("CREATE TABLE recipes (id INTEGER PRIMARY KEY, title TEXT, ingredients TEXT, instructions TEXT)")
        db.execute("CREATE VIRTUAL TABLE recipes_fts USING fts5(title, ingredients, instructions, content='recipes', content_rowid='id')")
        rows = []
        with self.source.open(encoding="utf-8") as source:
            for line in source:
                try:
                    record = json.loads(line)
                    assistant = next(m for m in reversed(record.get("messages", []))
                                     if m.get("role") == "assistant")
                    recipe = json.loads(assistant.get("content", "{}"))
                    title = str(recipe.get("Title") or "").strip()
                    # Newline-separated, so the lines can be read back one at a
                    # time. FTS5 tokenises on whitespace, so search is unchanged.
                    ingredients = "\n".join(_clean(x) for x in recipe.get("Ingredients") or [])
                    instructions = "\n".join(_clean(x) for x in recipe.get("Instructions") or [])
                    if title and (ingredients or instructions):
                        rows.append((title[:200], ingredients[:4000], instructions[:6000]))
                except (AttributeError, KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if len(rows) >= 1000:
                    self._insert_batch(db, rows)
                    rows.clear()
        if rows:
            self._insert_batch(db, rows)
        db.execute("INSERT INTO recipes_fts(recipes_fts) VALUES ('rebuild')")

    @staticmethod
    def _insert_batch(db: sqlite3.Connection, rows: list[tuple[str, str, str]]) -> None:
        db.executemany("INSERT INTO recipes(title, ingredients, instructions) VALUES (?, ?, ?)", rows)

    def _search(self, query: str, limit: int) -> list[dict]:
        terms = re.findall(r"[\w']+", str(query).lower(), flags=re.UNICODE)
        terms = list(dict.fromkeys(t for t in terms if len(t) > 1))
        if not terms:
            return []
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:24])
        with sqlite3.connect(self.index) as db:
            rows = db.execute(
                "SELECT title, ingredients, instructions FROM recipes_fts "
                "WHERE recipes_fts MATCH ? ORDER BY bm25(recipes_fts) LIMIT ?",
                (match, max(1, min(24, int(limit)))),
            ).fetchall()
        return [{"title": title, "ingredients": ingredients, "instructions": instructions}
                for title, ingredients, instructions in rows]


def _clean(value) -> str:
    """One line, with its own newlines flattened so the separator stays honest."""
    return re.sub(r"\s+", " ", str(value)).strip()