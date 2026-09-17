"""Reading a real till receipt — the parser the README admitted didn't exist.

Two models, neither of which needs to see a picture:

  1. Tesseract turns the photograph into text. It is the character recogniser,
     and it is very good at the one thing a receipt is: dark monospace on pale
     paper.
  2. The language model you already run turns that text into catalogue lines.
     This is the part that needs judgement — "PLT FERM X6" is eggs, "CRM FRAICHE
     30CL" is cream, and the €2.30 beside it is not a quantity.

Splitting it this way means the local Qwen is enough. No vision model, no cloud.

Between the two sits RETRIEVAL (app/products.py). Each line is looked up among
real products from Open Food Facts, and near-matches of lines this household
already corrected by hand, and the model is shown both. It no longer has to
know that CRF is Carrefour; it has to agree that "Lait demi-écrémé, Carrefour,
filed under semi-skimmed milk" is what "CRF LT DEMI ECR 1L" means. With the
model switched off, or failing, the lookup alone still produces a receipt —
every line it isn't sure of is left for the person to check.

Everything the model returns is untrusted, same as recipes: an id that isn't in
the catalogue is dropped to null so the existing correction UI asks you, and a
quantity that doesn't parse becomes a single unit. The household's remembered
corrections are applied in the browser afterwards, exactly as with the sample.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import re
import shutil
import sys
from pathlib import Path

from .catalog import id_list
from .products import STORES, line_terms, quantity, remembered, similarity, words

# Lines that are never food. Cheap to check, and it keeps the model's input
# short enough that a 7B answers in one pass.
NOISE = re.compile(
    r"^\s*("
    r"total|sous[- ]?total|sub ?total|tva|t\.v\.a|vat|montant|net a payer|net à payer|"
    r"carte|card|cb|especes|espèces|cash|monnaie|rendu|change|merci|thank|"
    r"ticket|caisse|vendeur|siret|tel|t[ée]l|www|http|facture|remise|reduction|"
    r"réduction|fidelite|fidélité|points|solde|date|heure|caissier|client"
    r")\b", re.I)

ADDRESS = re.compile(r"^\s*\d*\s*(rue|avenue|av|bd|boulevard|place|route|chemin|allee|allée|quai|zac|za|centre commercial)\b"
                     r"|\b\d{5}\b(?!\s*[.,]\d)", re.I)

PRICE = re.compile(r"\d+[.,]\d{2}\s*(?:€|eur|\$|£)?\s*$", re.I)

SYSTEM = """You read supermarket till receipts. You are given the raw OCR text of
one receipt and the list of ingredient ids the kitchen app knows.

For every line that is FOOD OR DRINK, return one entry:
  raw   the line as printed, tidied of prices and barcodes but otherwise as-is
  id    the matching ingredient id, or null if nothing in the list is clearly it
  qty   how much was bought, in the unit shown beside that id. A "x3" or "3 @"
        means three of them; "500G" means 500; a weight line like "0,486 kg" on
        a loose item means 486. When the line gives no amount, use one typical
        shop quantity for that product.
  confidence  0 to 1 — how sure you are of the id. Below 0.7 the user is asked.

Rules:
- Skip totals, VAT, payment, loyalty, the shop's address and phone, and anything
  that is not something you eat or drink.
- Abbreviations are the norm on receipts. Expand them: PLT/OEUFS = eggs,
  CRM = cream, LT/LAIT = milk, FRMG = cheese, PDT = potatoes, CRGT = courgette.
- NEVER invent an id. If it is food but no id fits, return id null and keep the
  raw line — the user will map it and the app will remember.
- The price is not a quantity. A number with two decimals at the end of a line
  is money.

Some lines come with evidence underneath:
  corrected before  this household mapped a line like it by hand. Follow it
                    unless the line is plainly a different product.
  database          real products whose names match the line, with the
                    ingredient id each is filed under. Strong evidence when the
                    product clearly IS the line; weak when it only shares a word.
The evidence narrows the choice; the id must still come from the id list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "store": {"type": "string", "description": "Shop name, if it's on the receipt."},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw": {"type": "string"},
                    "id": {"type": ["string", "null"]},
                    "qty": {"type": "number"},
                    "confidence": {"type": "number"},
                },
                "required": ["raw"],
            },
        },
    },
    "required": ["lines"],
}


class ReceiptError(RuntimeError):
    """Something a human can act on — shown in the app as written."""


# ------------------------------------------------------------------ step 1: OCR

# The Windows installer puts tesseract here and does not add it to PATH.
WINDOWS_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def ocr_available() -> str | None:
    """None when we can read pictures, otherwise why not."""
    windows = sys.platform == "win32"
    install = ("winget install UB-Mannheim.TesseractOCR" if windows
               else "brew install tesseract tesseract-lang")
    if importlib.util.find_spec("PIL") is None:
        return "Reading receipts needs Pillow:  pip install pillow"
    if importlib.util.find_spec("pytesseract") is None:
        return ("Reading receipts needs Tesseract. Install both halves:  "
                f"{install}  &&  pip install pytesseract")
    try:
        import pytesseract
        if windows and not shutil.which("tesseract") and WINDOWS_TESSERACT.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(WINDOWS_TESSERACT)
        pytesseract.get_tesseract_version()
    except Exception:
        return f"The tesseract program isn't on PATH. Install it:  {install}"
    return None


def to_text(data: bytes, languages: str = "fra+eng") -> str:
    why = ocr_available()
    if why:
        raise ReceiptError(why)
    import pytesseract
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(data))
    except Exception:
        raise ReceiptError("That file isn't an image the server can open.") from None

    img = ImageOps.exif_transpose(img).convert("L")     # phone photos arrive rotated
    # A receipt photographed on a table is usually small in frame; upscaling
    # before thresholding measurably improves recognition of 6pt print.
    if max(img.size) < 1600:
        scale = 1600 / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    img = ImageOps.autocontrast(img)

    try:
        text = pytesseract.image_to_string(img, lang=languages)
    except Exception:
        text = pytesseract.image_to_string(img)          # language pack missing
    if not text.strip():
        raise ReceiptError("Nothing readable in that photo. Lay the receipt flat, "
                           "fill the frame, and keep your shadow off it.")
    return text


def tidy(text: str, limit: int = 120) -> list[str]:
    """OCR text -> candidate product lines. Cuts the obvious rubbish so the
    model gets a short, clean list instead of two pages of shop address."""
    out: list[str] = []
    for line in text.splitlines():
        s = re.sub(r"\s+", " ", line).strip(" .*|_-")
        if len(s) < 3 or NOISE.search(s):
            continue
        if not re.search(r"[A-Za-zÀ-ÿ]{3}", s):          # no real word on it
            continue
        if ADDRESS.search(s):                            # the shop's street and town
            continue
        out.append(s[:80])
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ step 2: mapping

async def read(data: bytes, llm, known: dict[str, dict], languages: str = "fra+eng",
               products=None, corrections: dict | None = None, ids_limit: int = 0) -> dict:
    """Photograph -> the receipt shape the app's correction screen already uses."""
    lines = tidy(to_text(data, languages))
    if not lines:
        raise ReceiptError("That looks like a photo of something else — no product "
                           "lines were found on it.")
    return await match(lines, llm, known, products, corrections, ids_limit)


async def match(lines: list[str], llm, known: dict[str, dict], products=None,
                corrections: dict | None = None, ids_limit: int = 0) -> dict:
    """Receipt lines -> receipt. Separate from `read` so it can be tried on
    typed-in lines, without a photograph or tesseract."""
    looked = await asyncio.to_thread(_look_up, lines, known, products, corrections or {})
    if not llm:
        if not any(h["iid"] or h["memory"] for h in looked):
            raise ReceiptError("The assistant is switched off and the product database "
                               "isn't built, so the lines can't be matched to ingredients. "
                               "Turn the assistant on in backend/.env, or run "
                               "python3 import_openfoodfacts.py --download.")
        return from_lookup(lines, looked, known)

    keep = {h["iid"] for h in looked if h["iid"]} | {h["memory"][1] for h in looked if h["memory"] and h["memory"][1]}
    keep |= {p["iid"] for h in looked for p in h["products"] if p["iid"]}
    # The whole catalogue is three thousand entries — far more than a small
    # model's window. What the lookup found is always in; the rest fills the
    # budget. An ingredient left out still arrives as a null id to correct.
    ids = id_list(known, keep=keep, limit=ids_limit or 6000)
    user = "Ingredient ids: " + ids + "\n\nReceipt lines:\n" + "\n".join(
        _evidence(i, line, h) for i, (line, h) in enumerate(zip(lines, looked), 1))
    try:
        out = await llm.json(SYSTEM, user, SCHEMA, 2000)
    except Exception as e:  # noqa: BLE001
        if any(h["iid"] or h["memory"] for h in looked):
            print(f"receipt: the model failed ({type(e).__name__}: {str(e)[:160]}) — using the lookup alone")
            return from_lookup(lines, looked, known)
        raise ReceiptError(f"The assistant couldn't read the receipt "
                           f"({type(e).__name__}).") from None
    receipt = merge(clean(out, known), lines, looked, known)
    if receipt["store"] == "Receipt":
        receipt["store"] = _store(lines) or "Receipt"
    return receipt


# ------------------------------------------------------------------ retrieval

# A remembered correction is followed without asking only when the line is all
# but the same one; below that it is evidence for the model, nothing more.
SAME_LINE = 0.85


def _look_up(lines: list[str], known: dict, products, corrections: dict) -> list[dict]:
    ready = products is not None and products.available
    out = []
    for line in lines:
        hit = products.lookup(line) if ready else {"iid": None, "score": 0.0, "products": []}
        if hit["iid"] not in known:
            hit["iid"], hit["score"] = None, 0.0
        for p in hit["products"]:
            if p["iid"] not in known:
                p["iid"] = None
        memory = remembered(line, corrections)
        if memory and memory[1] is not None and memory[1] not in known:
            memory = None
        out.append({**hit, "memory": memory})
    return out


def _evidence(n: int, line: str, h: dict) -> str:
    text = f"{n}. {line}"
    if h["memory"]:
        seen, iid, _ = h["memory"]
        text += f"\n   corrected before: \"{seen}\" -> {iid or 'not food'}"
    for p in h["products"][:2]:
        about = ", ".join(x for x in (p["brand"], p["quantity"], p["category"]) if x)
        text += f"\n   database: {p['name']}" + (f" ({about})" if about else "") + f" -> {p['iid'] or 'no id'}"
    return text


def _source_of(raw: str, lines: list[str]) -> int | None:
    """Which OCR line a model row came from — it tidies the text, so compare
    the words, not the strings."""
    terms = line_terms(raw)
    best, score = None, 0.5
    for i, line in enumerate(lines):
        s = similarity(terms, line_terms(line))
        if s > score:
            best, score = i, s
    return best


def _store(lines: list[str]) -> str | None:
    """The chain's name, when one of the first lines is it: "E. LECLERC"."""
    for line in lines[:4]:
        if set(words(line)) & STORES and len(line) <= 30:
            return line.title()
    return None


def _default_qty(iid: str | None, known: dict) -> float:
    return 1.0 if (iid and known[iid].get("unit") == "u") else 250.0


def _fix_quantity(row: dict, line: str, known: dict) -> None:
    """Gemma put away 2.31 courgettes and 7.45 cl of olive oil: the prices. A
    size printed on the line (1L, 2K5, 2X125, X6) is read by rule and wins;
    a quantity that is just the line's price is thrown out."""
    if not row.get("id"):
        return
    printed = quantity(line, known[row["id"]].get("unit", "g"))
    if printed:
        row["qty"] = round(printed, 2)
        return
    price = PRICE.search(line)
    if price and abs(float(re.sub(r"[^\d,.]", "", price.group()).replace(",", ".")) - row["qty"]) < 0.005:
        row["qty"] = _default_qty(row["id"], known)


def merge(receipt: dict, lines: list[str], looked: list[dict], known: dict) -> dict:
    """The model's answer, checked against the lookup it was shown.

    Agreement raises confidence; a strong database match the model ignored
    fills a null id but is left for the person to confirm; a disagreement with
    a strong match is never trusted on its own."""
    for row in receipt["lines"]:
        i = _source_of(row["raw"], lines)
        if i is None:
            continue
        h = looked[i]
        _fix_quantity(row, lines[i], known)
        memory = h["memory"]
        if memory and memory[2] >= SAME_LINE:
            row["id"], row["confidence"] = memory[1], (0.95 if memory[1] else 0.0)
            if not memory[1]:
                row["rejected"] = True
            continue
        if not h["iid"] or h["score"] < 0.5:
            continue
        if row["id"] is None:
            row["id"], row["confidence"] = h["iid"], round(min(0.7, h["score"]), 2)
            row["qty"] = _default_qty(h["iid"], known)
            _fix_quantity(row, lines[i], known)
        elif row["id"] == h["iid"]:
            row["confidence"] = round(max(row["confidence"], min(0.95, 0.5 + h["score"] / 2)), 2)
        elif h["score"] >= 0.8:
            row["confidence"] = min(row["confidence"], 0.6)
        if h["products"]:
            row["product"] = " · ".join(x for x in (h["products"][0]["name"], h["products"][0]["brand"]) if x)[:80]
    return receipt


def from_lookup(lines: list[str], looked: list[dict], known: dict) -> dict:
    """A receipt from the lookup alone, for when there is no model. Never
    confident enough to skip the person on anything it merely guessed."""
    out = []
    for line, h in zip(lines, looked):
        raw = PRICE.sub("", line).strip(" .-")[:80]
        memory = h["memory"]
        if memory and memory[2] >= SAME_LINE:
            iid, conf = memory[1], (0.95 if memory[1] else 0.0)
        elif h["iid"] and h["score"] >= 0.45:
            # Below the app's 0.75: without a model to agree, a match is a
            # suggestion, and every one waits under "Needs checking".
            iid, conf = h["iid"], round(min(0.7, h["score"]), 2)
        else:
            iid, conf = None, 0.0
        row = {"raw": raw, "id": iid, "confidence": conf,
               "qty": round((quantity(line, known[iid].get("unit", "g")) if iid else None)
                            or _default_qty(iid, known), 2)}
        if memory and memory[2] >= SAME_LINE and not memory[1]:
            row["rejected"] = True
        if h["products"]:
            row["product"] = " · ".join(x for x in (h["products"][0]["name"], h["products"][0]["brand"]) if x)[:80]
        out.append(row)
    return {"store": _store(lines) or "Receipt", "lines": out, "source": "photo", "method": "lookup"}


def clean(out: dict, known: dict[str, dict]) -> dict:
    """Model output is untrusted. Same discipline as clean_recipe: drop what we
    don't recognise rather than repair it."""
    store = str((out or {}).get("store") or "").strip()[:40] or "Receipt"
    lines = []
    seen: set[str] = set()
    for row in (out or {}).get("lines") or []:
        if not isinstance(row, dict):
            continue
        raw = re.sub(r"\s+", " ", str(row.get("raw") or "")).strip()[:80]
        raw = PRICE.sub("", raw).strip(" .-")
        if not raw or raw.lower() in seen:
            continue
        seen.add(raw.lower())

        iid = row.get("id")
        iid = iid if isinstance(iid, str) and iid in known else None

        try:
            qty = float(row.get("qty"))
        except (TypeError, ValueError):
            qty = 0.0
        if qty != qty or qty <= 0 or qty > 100_000:        # NaN or nonsense
            qty = 1.0 if (iid and known[iid].get("unit") == "u") else 250.0

        try:
            conf = float(row.get("confidence"))
        except (TypeError, ValueError):
            conf = 0.5
        conf = 0.0 if not iid else max(0.0, min(1.0, conf))

        lines.append({"raw": raw, "id": iid, "qty": round(qty, 2), "confidence": round(conf, 2)})
        if len(lines) >= 80:
            break

    if not lines:
        raise ReceiptError("No food lines came back from that receipt.")
    return {"store": store, "lines": lines, "source": "photo"}


def preview(text: str) -> str:
    return json.dumps(tidy(text)[:20], ensure_ascii=False)
