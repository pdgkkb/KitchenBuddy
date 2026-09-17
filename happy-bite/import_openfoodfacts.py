#!/usr/bin/env python3
"""Happy Bite — build the product database receipts are matched against.

Reads the Open Food Facts product export (one 1.3 GB gzip, public domain data
under the Open Database License) and writes backend/media/products.sqlite3:
every product sold in the chosen countries, with its name, brand, size, and the
catalogue ingredient id it most likely is. `backend/app/products.py` searches
it when a receipt is read; see that file for how the search works.

    python3 import_openfoodfacts.py --download     fetch the export, then build
    python3 import_openfoodfacts.py                build from the export already downloaded
    python3 import_openfoodfacts.py --limit 200000 a quick look: read only the first rows
    python3 import_openfoodfacts.py --country en:france --country en:belgium

The export is not needed afterwards; delete it to get the space back. Rebuild
when the catalogue gains ingredients, so new ids get products pointing at them.

HOW A PRODUCT GETS ITS INGREDIENT ID
------------------------------------
From its categories, not its name. Open Food Facts files "Lait demi-écrémé
UHT Lactel" under en:milks, en:semi-skimmed-milks, en:uht-semi-skimmed-milks,
and those English names line up with the catalogue's. The most specific
category that names a catalogue ingredient wins, and `fit` records how direct
the match was:

    1.0   the category IS an ingredient        semi-skimmed-milks -> milk
    0.8   its last two words are               extra-virgin-olive-oils -> oliveoil
    0.6   its last word is                     frozen-spinachs -> spinach

A product with no category that matches keeps no id. It is still stored: a
line that matches "Ras el hanout Ducros" is better shown to the model as that
than as nothing.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
from app.products import INDEX_VERSION, fold, words  # noqa: E402

URL = "https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz"
SOURCE = ROOT / "backend" / "media" / "openfoodfacts-products.csv.gz"
OUT = ROOT / "backend" / "media" / "products.sqlite3"

G, R, Y, D, O = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = O = ""

# Category words too broad to be an ingredient. "en:vegetables" must not turn
# frozen spinach into the catalogue's "Vegetable".
GENERIC = set("""
food product preparation plant based beverage drink snack dish meal sweet salty dessert
vegetable fruit legume dairy meat fish seafood cereal grocery frozen fresh canned organic
condiment spread topping mix other unknown and of with in from for the de
""".split())


# The catalogue has the same food twice: the hand-written ingredient your stock
# and recipes use (`milk`, "Semi-skimmed milk") and a twin generated from the
# recipe corpus (`c_milk`, "Milk"). Open Food Facts calls it "milks", which is
# the twin's name — and milk put away as `c_milk` is milk no recipe can find.
# These are the Open Food Facts category names that mean a hand-written one.
ALIASES = {
    "courgette": ["zucchini", "courgette"], "tomato": ["tomato"], "onion": ["onion", "yellow onion"],
    "pepper": ["bell pepper", "sweet pepper"], "milk": ["milk", "semi skimmed milk", "cow milk", "uht milk"],
    "cream": ["creme fraiche", "fresh cream", "thick cream"], "goat": ["goat cheese", "fresh goat cheese"],
    "parmesan": ["parmesan", "parmigiano reggiano"], "yoghurt": ["plain yogurt", "natural yogurt"],
    "chicken": ["chicken", "chicken breast", "chicken fillet", "chicken breast fillet"],
    "beef": ["beef", "beef steak", "steak"], "pork": ["pork", "pork belly"], "cod": ["cod", "cod fillet"],
    "rice": ["rice", "basmati rice"], "pasta": ["pasta", "dry pasta"],
    "flour": ["flour", "wheat flour", "all purpose flour"],
    "oliveoil": ["olive oil", "virgin olive oil", "extra virgin olive oil"],
    "chickpea": ["chickpea"], "oats": ["oat", "rolled oat", "oat flake"], "salt": ["salt", "sea salt", "table salt"],
    "blackpepper": ["black pepper", "ground black pepper"], "paprika": ["paprika", "smoked paprika"],
    "miso": ["miso", "miso paste"], "chilli": ["chilli flake", "chili flake"], "limejuice": ["lime", "lime juice"],
}


def catalogue_names() -> dict[str, str]:
    """Normalised ingredient name -> id. The hand-written ingredients win a
    clash with the corpus-generated ones (c_...), and ALIASES win over both."""
    cat = json.loads((ROOT / "shared" / "catalog.json").read_text("utf-8"))
    out: dict[str, str] = {}
    for iid, ing in sorted(cat["ingredients"].items(), key=lambda kv: kv[0].startswith("c_")):
        key = " ".join(words(ing.get("name", "")))
        if key and not set(key.split()) <= GENERIC:
            out.setdefault(key, iid)
    for iid, names in ALIASES.items():
        if iid in cat["ingredients"]:
            for name in names:
                out[" ".join(words(name))] = iid
    return out


def ingredient_for(tags: list[str], names: dict[str, str]) -> tuple[str | None, float]:
    best: tuple[str | None, float] = (None, 0.0)
    specific_first = [t[3:] for t in reversed(tags) if t.startswith("en:")]
    for depth, tag in enumerate(specific_first):
        # "cereals-and-potatoes" is a shelf, not a potato: it made granola bars
        # and crisps into potatoes.
        if "-and-" in tag:
            continue
        ws = words(tag.replace("-", " "))
        for phrase, fit in ((ws, 1.0), (ws[-2:], 0.8), (ws[-1:], 0.6)):
            # A category's last word only speaks for the product near the most
            # specific end of the list; further up it names the aisle.
            if not phrase or (fit < 1.0 and (len(ws) <= len(phrase) or depth > 1)):
                continue
            iid = names.get(" ".join(phrase))
            score = fit * 0.93 ** depth
            if iid and score > best[1]:
                best = (iid, round(score, 2))
                break
    return best if best[1] >= 0.55 else (None, 0.0)


def download(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {URL}\n  -> {dest}  (about 1.3 GB; resumes if interrupted)")
    for attempt in range(1, 21):
        # curl, because it resumes (-C -) and gives up on a stalled connection
        # instead of hanging for ever — which this server has been seen to do.
        code = subprocess.call(["curl", "-L", "--fail", "-C", "-", "--speed-limit", "20000",
                                "--speed-time", "60", "-o", str(part), URL])
        if code == 0:
            part.replace(dest)
            return
        print(f"{Y}  download interrupted (curl exit {code}), resuming — attempt {attempt + 1}{O}")
        time.sleep(5)
    raise SystemExit(f"{R}The download kept failing. Run the same command again to resume.{O}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", type=Path, default=SOURCE, help="the .csv.gz export")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--download", action="store_true", help="fetch the export first if it isn't there")
    ap.add_argument("--country", action="append",
                    help="Open Food Facts country tag, repeatable (default en:france); 'all' for every country")
    ap.add_argument("--limit", type=int, default=0, help="read only the first N rows")
    a = ap.parse_args()

    if not a.source.is_file():
        if not a.download:
            print(f"{R}No export at {a.source}{O}\n  Run:  python3 import_openfoodfacts.py --download")
            return 2
        download(a.source)

    countries = [c.lower() for c in (a.country or ["en:france"])]
    names = catalogue_names()
    print(f"{len(names)} catalogue ingredients to point products at; countries: {', '.join(countries)}")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".building.sqlite3")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;
        CREATE TABLE staging (key TEXT PRIMARY KEY, name TEXT, brand TEXT, quantity TEXT,
                              category TEXT, iid TEXT, fit REAL, scans INTEGER, words TEXT, text TEXT);
    """)

    started = time.time()
    read = kept = mapped = 0
    with gzip.open(a.source, "rt", encoding="utf-8", errors="replace", newline="") as f:
        header = f.readline().rstrip("\n").split("\t")
        col = {name: i for i, name in enumerate(header)}
        need = ["product_name", "abbreviated_product_name", "generic_name", "brands", "quantity",
                "categories_tags", "main_category_en", "countries_tags", "unique_scans_n"]
        missing = [n for n in need if n not in col]
        if missing:
            print(f"{R}The export's columns have changed; missing: {', '.join(missing)}{O}")
            return 2
        batch = []
        for line in f:
            read += 1
            if a.limit and read > a.limit:
                break
            if read % 250_000 == 0:
                print(f"{D}  {read:,} rows read, {kept:,} kept, {time.time() - started:.0f} s{O}", flush=True)
            # Cheap test on the raw line before splitting two hundred columns.
            if "all" not in countries and not any(c in line for c in countries):
                continue
            row = line.rstrip("\n").split("\t")
            if len(row) != len(header):
                continue
            if "all" not in countries and not any(c in row[col["countries_tags"]].split(",") for c in countries):
                continue
            name = html.unescape(row[col["product_name"]]).strip()
            tags = [t for t in row[col["categories_tags"]].split(",") if t]
            if not name or not tags:
                continue
            brand = html.unescape(row[col["brands"]].split(",")[0]).strip()
            short = html.unescape(row[col["abbreviated_product_name"]]).strip()
            generic = html.unescape(row[col["generic_name"]]).strip()
            iid, fit = ingredient_for(tags, names)
            try:
                scans = int(float(row[col["unique_scans_n"]] or 0))
            except ValueError:
                scans = 0
            text = " ".join(dict.fromkeys(x for x in (name, short, generic, brand) if x))
            batch.append((f"{fold(name)}|{fold(brand)}", name[:120], brand[:60], row[col["quantity"]][:30],
                          row[col["main_category_en"]][:60], iid, fit, scans,
                          " ".join(dict.fromkeys(words(text))), fold(text)[:300]))
            kept += 1
            mapped += bool(iid)
            if len(batch) >= 5000:
                _flush(db, batch)
        _flush(db, batch)

    unique = db.execute("SELECT COUNT(*) FROM staging").fetchone()[0]
    print(f"{read:,} rows read; {kept:,} products kept ({mapped:,} with an ingredient id), "
          f"{unique:,} after merging duplicates. Indexing…", flush=True)

    # Most-scanned first, so rowid order is popularity order: the search takes
    # the first rows that match rather than ranking all of them.
    db.executescript(f"""
        CREATE TABLE products (name TEXT, brand TEXT, quantity TEXT, category TEXT,
                               iid TEXT, fit REAL, scans INTEGER, words TEXT);
        INSERT INTO products (rowid, name, brand, quantity, category, iid, fit, scans, words)
            SELECT ROW_NUMBER() OVER (ORDER BY scans DESC, iid IS NULL, name),
                   name, brand, quantity, category, iid, fit, scans, words FROM staging;
        CREATE VIRTUAL TABLE search USING fts5(text, tokenize = 'trigram', content = '');
        INSERT INTO search (rowid, text)
            SELECT ROW_NUMBER() OVER (ORDER BY scans DESC, iid IS NULL, name), text FROM staging;
        DROP TABLE staging;
        INSERT INTO search (search) VALUES ('optimize');
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('version', '{INDEX_VERSION}'),
                                ('countries', '{",".join(countries)}'),
                                ('built', '{time.strftime("%Y-%m-%d")}');
    """)
    db.commit()
    db.execute("VACUUM")
    db.close()
    os.replace(tmp, a.out)
    size = a.out.stat().st_size / 1e6
    print(f"{G}Wrote {a.out} — {unique:,} products, {size:.0f} MB, {time.time() - started:.0f} s.{O}\n"
          "Restart the backend and receipts are matched against it.")
    return 0


def _flush(db: sqlite3.Connection, batch: list) -> None:
    # The same product entered twice (same name, same brand): keep the
    # better-known entry, and an id if either has one.
    db.executemany("""
        INSERT INTO staging VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET
            scans = MAX(scans, excluded.scans),
            iid = COALESCE(CASE WHEN excluded.fit > fit THEN excluded.iid END, iid, excluded.iid),
            fit = MAX(fit, excluded.fit)
    """, batch)
    batch.clear()


if __name__ == "__main__":
    raise SystemExit(main())
