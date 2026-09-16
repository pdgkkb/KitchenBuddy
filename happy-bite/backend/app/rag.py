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

WHY A SEARCH USED TO TAKE THREE SECONDS
---------------------------------------
Every word of the query went into one big OR, and bm25 has to score EVERY row
that matches before it can sort. The queries this app sends are a sentence plus
the whole stock list, so they always contained "salt" (in 52% of 2.1M recipes),
"the" (52%), "pepper", "oil", "water" — a million rows ranked per request. A
recipe request waited 3.3 s on retrieval alone; a chat question 1.4 s.

Now `_terms` throws away words that can't tell one recipe from another (grammar,
units, "days left", and anything in more than a quarter of the corpus), keeps
the rarest few, and `_search` asks for recipes containing ALL of them, dropping
the most common one until enough come back. Few rows to rank, so it's tens of
milliseconds, and the hits are better: a recipe that uses chicken AND rice AND
black beans, rather than whichever one says "chicken" most often.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import unicodedata
from collections import OrderedDict
from pathlib import Path

# Bump this when _build changes the shape of what it stores, or an existing
# index will be reused for ever — the size/mtime check only notices a changed
# SOURCE FILE, never changed code.
INDEX_VERSION = "2"

# Words that say nothing about WHICH recipe: grammar, how people ask for food,
# and the units and "(2 days left)" the recipe endpoint appends to the stock.
STOPWORDS = frozenset("""
a an and are as at be but by can could do does for from had has have how i if in into is it its
just me my no not of on or our so some something that the their them then there these this to too
up us was we what when where which who why will with would you your
want wants like make made making cook cooking use using instead substitute replace swap
should now next get need ok okay yes still done ready right well
please thanks thank tonight today dinner lunch breakfast meal quick easy good nice
recipe recipes idea ideas extra wishes turn agreed conversation user assistant
long much many days day left g kg mg ml cl l u oz lb lbs cup cups tsp tbsp
teaspoon teaspoons tablespoon tablespoons
""".split())

# A word in more than this share of the corpus picks out nothing, and it is the
# expensive kind: bm25 ranks every row that contains it.
COMMON_SHARE = 0.25

# How many words must all appear in a hit, at first. The search then drops the
# most common of them, one at a time, until enough recipes come back.
MAX_TERMS = 5

# Words in at least this many recipes have their count stored in the index
# (a few thousand of them). Counting "salt" live means walking a million-entry
# posting list — seconds on a cold disk. A rarer word is cheap to count live.
COUNTED_FROM = 2000


class RecipeRetriever:
    def __init__(self, source: Path, index: Path, enabled: bool = True, top_k: int = 4):
        self.source = Path(source)
        self.index = Path(index)
        self.enabled = bool(enabled)
        self.top_k = max(1, min(8, int(top_k or 4)))
        self.ready = False
        self.building = False
        self.error: str | None = None
        # One connection, kept open, used by one search at a time. Searches are
        # now tens of milliseconds, so queueing behind the lock costs nothing.
        self._db: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._rows = 0
        self._doc_counts: dict[str, int] = {}          # word -> recipes containing it
        self._common: dict[str, int] = {}              # the stored counts, see COUNTED_FROM
        self._hits: OrderedDict[tuple, list[tuple]] = OrderedDict()

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
            return
        finally:
            self.building = False
        try:
            self._load_word_counts()
        except Exception as exc:  # noqa: BLE001 — search still works, just slower the first time
            print(f"rag: couldn't store word counts ({type(exc).__name__}: {exc})")

    def _load_word_counts(self) -> None:
        """Counted once per index (~10 s on 2.1M recipes, in this background
        thread), then read back in milliseconds on every later start. Searches
        made before it finishes count their words live."""
        with sqlite3.connect(self.index) as db:
            db.execute("CREATE TABLE IF NOT EXISTS word_counts (term TEXT PRIMARY KEY, doc INTEGER NOT NULL)")
            if not db.execute("SELECT 1 FROM word_counts LIMIT 1").fetchone():
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.recipes_vocab "
                           "USING fts5vocab(main, recipes_fts, row)")
                db.execute("INSERT INTO word_counts SELECT term, doc FROM temp.recipes_vocab "
                           "WHERE doc >= ?", (COUNTED_FROM,))
                db.commit()
            self._common = dict(db.execute("SELECT term, doc FROM word_counts"))

    def _build(self, db: sqlite3.Connection) -> None:
        db.execute("DROP TABLE IF EXISTS recipes")
        db.execute("DROP TABLE IF EXISTS recipes_fts")
        db.execute("DROP TABLE IF EXISTS word_counts")          # counts of the old rows
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
        limit = max(1, min(24, int(limit)))
        with self._lock:
            db = self._connection()
            terms = self._terms(db, query)
            if not terms:
                return []
            key = (tuple(terms), limit)
            rows = self._hits.get(key)
            if rows is None:
                found: dict[int, tuple] = {}
                # All the words first; then without the most common one, and so
                # on. Each pass ranks only the rows containing every word in it.
                for n in range(len(terms), 0, -1):
                    match = " AND ".join('"' + t + '"' for t in terms[:n])
                    for rowid, *row in db.execute(
                        "SELECT rowid, title, ingredients, instructions FROM recipes_fts "
                        "WHERE recipes_fts MATCH ? ORDER BY bm25(recipes_fts) LIMIT ?",
                        (match, limit),
                    ):
                        found.setdefault(rowid, tuple(row))
                    if len(found) >= limit:
                        break
                rows = list(found.values())[:limit]
                self._hits[key] = rows
                if len(self._hits) > 128:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
        return [{"title": title, "ingredients": ingredients, "instructions": instructions}
                for title, ingredients, instructions in rows]

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            db = sqlite3.connect(self.index, check_same_thread=False)
            # How many recipes contain a word, read straight from the FTS index.
            # A TEMP table: nothing is written to the index file.
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.recipes_vocab "
                       "USING fts5vocab(main, recipes_fts, row)")
            # Rows are only ever appended, so the last id is the count — and,
            # unlike count(*), it doesn't read the whole table off the disk.
            self._rows = db.execute("SELECT max(rowid) FROM recipes").fetchone()[0] or 0
            self._db = db
        return self._db

    def _terms(self, db: sqlite3.Connection, query: str) -> list[str]:
        """The few words worth searching on, rarest first."""
        # Letters only, accents folded the way FTS5's unicode61 tokeniser folds
        # them — "crème fraîche" is indexed as "creme fraiche", "olive_oil" as two.
        words = re.findall(r"[^\W\d_]+", _fold(str(query).lower()))
        words = [w for w in dict.fromkeys(words) if len(w) > 1 and w not in STOPWORDS][:24]
        counted = sorted((self._doc_count(db, w), w) for w in words)
        counted = [(n, w) for n, w in counted if n]        # a word no recipe has matches nothing
        narrow = [w for n, w in counted if n <= COMMON_SHARE * self._rows]
        return (narrow or [w for _, w in counted[:1]])[:MAX_TERMS]

    def _doc_count(self, db: sqlite3.Connection, word: str) -> int:
        n = self._common.get(word)
        if n is not None:
            return n
        # Not stored: rare (or the stored counts aren't loaded yet). Counting
        # walks the word's posting list, which is short for a rare word.
        n = self._doc_counts.get(word)
        if n is None:
            row = db.execute("SELECT doc FROM temp.recipes_vocab WHERE term = ?", (word,)).fetchone()
            n = row[0] if row else 0
            if len(self._doc_counts) > 20000:
                self._doc_counts.clear()
            self._doc_counts[word] = n
        return n


def _clean(value) -> str:
    """One line, with its own newlines flattened so the separator stays honest."""
    return re.sub(r"\s+", " ", str(value)).strip()


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))