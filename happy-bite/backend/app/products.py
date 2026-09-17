"""What a till-receipt line is, looked up in Open Food Facts.

A receipt doesn't print "Lait demi-écrémé Carrefour 1 L". It prints
"CRF LT DEMI ECR 1L", and a small model asked to map that onto three thousand
ingredient ids is guessing. This is the RETRIEVAL half of receipt reading: the
line is matched against real French products, so the model is shown "this is
probably Lait demi-écrémé, category semi-skimmed milks, which is `milk`" and
has to confirm rather than invent.

The index is built once, offline, by `import_openfoodfacts.py` into
backend/media/products.sqlite3. Without it every function here answers "no
idea" and receipts are read exactly as before.

WHY NOT ORDINARY FULL-TEXT SEARCH
---------------------------------
Word search needs whole words, and a receipt has almost none. So:

  - The index is FTS5 with the TRIGRAM tokenizer: "poul" finds "poulet",
    "ecr" finds "écrémé" (accents are folded on both sides, see `fold`).
  - Abbreviations with no vowels are consonant skeletons of a word: CRF is
    CaRreFour, BLC is BLanC, FRMG is FRoMaGe. No substring search finds those,
    so they are matched while RE-SCORING the candidates (`_word_match`), and
    the commonest ones are also spelled out up front (`ABBREVIATIONS`) so
    they can find candidates in the first place.
  - Trigram posting lists for "lait" are enormous, and bm25 has to score every
    match. So the search never ranks in SQLite: it asks for rows containing
    ALL the useful fragments (rows are stored most-scanned first, so the first
    ones are the common products), drops fragments until something comes
    back, and ranks the few hundred candidates in Python.

The same `fold` / `words` / `singular` are used by the importer. Change them
and rebuild the index, or the two sides stop agreeing.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

# Bump when import_openfoodfacts.py changes what it writes; `ProductIndex`
# refuses an index built by a different version rather than half-work.
INDEX_VERSION = "1"

# French till abbreviations that no substring or skeleton match can recover.
# Kept short on purpose: most abbreviations are prefixes (POUL, EPIN, CHARL) or
# skeletons (CRF, FRMG) and are found without being listed.
ABBREVIATIONS = {
    "lt": "lait", "h": "huile", "pdt": "pomme terre", "pl": "plein", "ex": "extra",
    "blc": "blanc", "crf": "carrefour", "frmg": "fromage", "crm": "creme", "yrt": "yaourt",
    "jbn": "jambon", "bf": "boeuf", "vx": "vieux", "pt": "petit", "ptes": "pates", "cf": "confiture",
    "chx": "choux", "fr": "frais", "sce": "sauce", "vdg": "viande", "ss": "sans", "bte": "boite",
    "surg": "surgele", "tom": "tomate", "tr": "tranche", "gd": "grand", "moy": "moyen",
    "oeuf": "oeuf", "oeufs": "oeuf", "ste": "sainte", "st": "saint", "bq": "", "lot": "", "vrac": "",
    "cat1": "", "cat": "", "pce": "", "pcs": "", "sachet": "", "sach": "", "bt": "",
}

# Supermarket chains and their own labels. On a receipt the chain is the shop
# at the top; in Open Food Facts it is often in the product NAME ("Baguette Maya
# Leclerc"). Either way it says who sold the thing, never what it is.
STORES = set("""
leclerc carrefour auchan intermarche casino monoprix franprix lidl aldi cora netto spar vival
picard biocoop naturalia leader price grand frais super hyper market marche systeme u repere
eco eco+ auchan simpl g20 match colruyt delhaize migros coop
""".split())

# Quantities, prices and pack sizes: 1L, 750, 2X125, X6, 2K5, 75CL, 1,09.
_QTY = re.compile(r"^(?:\d+(?:[.,]\d+)?(?:k\d+|kg|g|gr|l|cl|ml|x\d+|x|p|d|pc|pcs)?|x\d+|\d+x\d+)$")
_VOWELS = set("aeiouy")


def fold(text: str) -> str:
    """Lower case, no accents, "œ" as "oe": what both sides compare."""
    text = str(text or "").lower().replace("œ", "oe").replace("æ", "ae")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def singular(word: str) -> str:
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("oes", "sses", "ches", "shes", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    if word.endswith("x") and word[-2:] in ("ux", "ix"):       # choux, prix
        return word[:-1]
    return word


def words(text: str) -> list[str]:
    return [singular(w) for w in re.findall(r"[a-z0-9]+", fold(text).replace("'s", ""))]


def skeleton(word: str) -> str:
    """First letter, then the consonants: "carrefour" -> "crrfr"."""
    return word[:1] + "".join(c for c in word[1:] if c not in _VOWELS and c.isalpha())


def line_terms(raw: str) -> list[str]:
    """A receipt line -> the words worth matching, abbreviations spelled out
    where the table knows them, sizes and prices dropped."""
    out: list[str] = []
    for w in re.findall(r"[a-z0-9]+(?:[.,]\d+)?", fold(raw)):
        if _QTY.match(w) or w.isdigit():
            continue
        if w in ABBREVIATIONS:
            out += ABBREVIATIONS[w].split()
            continue
        if len(w) >= 2:
            out.append(singular(w))
    return list(dict.fromkeys(out))


def _word_match(term: str, word: str) -> float:
    """How well one receipt term stands for one product word, 0 to 1."""
    if term == word:
        return 1.0
    if len(term) >= 3 and word.startswith(term):
        return 0.9                                     # POUL -> poulet
    if len(term) >= 3 and not (_VOWELS & set(term)) and term[0] == word[0]:
        rest = iter(skeleton(word)[1:])                # CRF -> carrefour, BLC -> blanc
        if all(c in rest for c in term[1:]):
            return 0.75
    if len(word) >= 3 and term.startswith(word):
        return 0.6                                     # LIQUIDE on one receipt, LIQ on the last
    return 0.0


def similarity(terms: list[str], product_words: list[str]) -> float:
    """Share of the line explained by the product, with a small pull towards
    products that are not much more than what the line says."""
    if not terms or not product_words:
        return 0.0
    got = [max((_word_match(t, w) for w in product_words), default=0.0) for t in terms]
    covered = sum(got) / len(terms)
    used = sum(1 for w in set(product_words) if any(_word_match(t, w) >= 0.6 for t in terms))
    return 0.85 * covered + 0.15 * (used / len(set(product_words)))


class ProductIndex:
    """Read-only access to products.sqlite3. Opens lazily; absent is fine."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._db: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self.why: str | None = None

    @property
    def available(self) -> bool:
        return self._open() is not None

    def _open(self) -> sqlite3.Connection | None:
        if self._db is not None:
            return self._db
        if not self.path.is_file():
            self.why = (f"No product database at {self.path}. Build it with: "
                        "python3 import_openfoodfacts.py")
            return None
        try:
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            version = db.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
            if not version or version[0] != INDEX_VERSION:
                self.why = "The product database is from another version. Rebuild it: python3 import_openfoodfacts.py"
                db.close()
                return None
        except sqlite3.Error as e:
            self.why = f"The product database can't be opened ({e})."
            return None
        self._db, self.why = db, None
        return db

    def _candidates(self, terms: list[str], limit: int = 300) -> list[tuple]:
        db = self._open()
        usable = [t for t in terms if len(t) >= 3]
        if db is None or not usable:
            return []
        # Longest fragments first: they are the rarest, and dropped last.
        usable.sort(key=len, reverse=True)
        with self._lock:
            for n in range(len(usable), 0, -1):
                query = " AND ".join('"' + t.replace('"', "") + '"' for t in usable[:n])
                try:
                    rows = db.execute(
                        "SELECT p.name, p.brand, p.quantity, p.category, p.iid, p.fit, p.scans, p.words "
                        "FROM search JOIN products p ON p.rowid = search.rowid "
                        "WHERE search MATCH ? LIMIT ?", (query, limit)).fetchall()
                except sqlite3.Error:
                    rows = []
                if rows:
                    return rows
        return []

    def lookup(self, raw: str, top: int = 3) -> dict:
        """One receipt line -> {"iid", "score", "products": [...]}.

        `score` is how well the best products explain the line (0-1), multiplied
        by how sure the importer was of their ingredient id. `iid` is the id the
        best-matching products agree on, or None."""
        terms = line_terms(raw)
        rows = self._candidates(terms)
        if not rows:
            return {"iid": None, "score": 0.0, "products": []}

        scored = []
        for name, brand, quantity, category, iid, fit, scans, product_words in rows:
            # "E. LECLERC" at the top of the receipt is the shop, and a Leclerc
            # baguette matched it perfectly — on the brand alone. A product must
            # match on what it IS, not only on who made it.
            own = set(product_words.split()) - set(words(brand)) - STORES
            if not any(_word_match(t, w) >= 0.6 for t in terms for w in own):
                continue
            s = similarity(terms, product_words.split())
            if s >= 0.35:
                # Popularity breaks ties, never outweighs a better match.
                scored.append((s + 0.01 * math.log1p(scans or 0), s, name, brand, quantity, category, iid, fit))
        scored.sort(reverse=True)

        votes: dict[str, float] = {}
        for _, s, *_rest, iid, fit in scored[:15]:
            if iid:
                votes[iid] = max(votes.get(iid, 0.0), s * fit)
        best = max(votes, key=votes.get) if votes else None
        return {
            "iid": best,
            "score": round(votes.get(best, 0.0), 2) if best else 0.0,
            "products": [{"name": name, "brand": brand, "quantity": quantity, "category": category,
                          "iid": iid, "match": round(s, 2)}
                         for _, s, name, brand, quantity, category, iid, _fit in scored[:top]],
        }


def remembered(raw: str, corrections: dict) -> tuple[str, str | None, float] | None:
    """The closest line the household has already corrected by hand, as
    (their line, the id they chose or None for "not food", similarity)."""
    terms = line_terms(raw)
    best = None
    for seen, iid in (corrections or {}).items():
        if not isinstance(seen, str) or (iid is not None and not isinstance(iid, str)):
            continue
        s = min(similarity(terms, line_terms(seen)), similarity(line_terms(seen), terms))
        if s >= 0.7 and (best is None or s > best[2]):
            best = (seen, iid, round(s, 2))
    return best


_PACK = re.compile(r"\b(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(kg|g|cl|ml|l)?\b", re.I)
_SIZE = re.compile(r"\b(\d+(?:[.,]\d+)?|\d+k\d+)\s*(kg|k|g|gr|cl|ml|l)?\b", re.I)
_COUNT = re.compile(r"\bx\s*(\d{1,2})\b", re.I)


def quantity(raw: str, unit: str) -> float | None:
    """The amount printed on a receipt line, in the ingredient's unit, when
    the line says one. Used when there is no model to read the line."""
    text = fold(raw)
    count = _COUNT.search(text)
    if unit == "u":
        return float(count.group(1)) if count else None
    grams = {"kg": 1000, "k": 1000, "g": 1, "gr": 1}
    litres = {"l": 1, "cl": 0.01, "ml": 0.001}
    pack = _PACK.search(text)
    if pack:
        n, size, u = int(pack.group(1)), float(pack.group(2).replace(",", ".")), (pack.group(3) or "g").lower()
        found = [(n * size, u)]
    else:
        found = []
        for m in _SIZE.finditer(text):
            number, u = m.group(1), (m.group(2) or "").lower()
            if "k" in number:                                   # 2K5 = 2.5 kg
                number, u = number.replace("k", "."), "kg"
            if not u and not re.fullmatch(r"\d{3,4}", number):  # a bare 750 is grams; a bare 1 is nothing
                continue
            found.append((float(number.replace(",", ".")), u or "g"))
    for amount, u in found:
        if unit == "g" and u in grams:
            return amount * grams[u]
        if unit in litres and u in litres:
            return round(amount * litres[u] / litres[unit], 3)
    return None
