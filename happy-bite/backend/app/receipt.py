"""Reading a real till receipt — the parser the README admitted didn't exist.

Two models, neither of which needs to see a picture:

  1. Tesseract turns the photograph into text. It is the character recogniser,
     and it is very good at the one thing a receipt is: dark monospace on pale
     paper.
  2. The language model you already run turns that text into catalogue lines.
     This is the part that needs judgement — "PLT FERM X6" is eggs, "CRM FRAICHE
     30CL" is cream, and the €2.30 beside it is not a quantity.

Splitting it this way means the local Qwen is enough. No vision model, no cloud.

Everything the model returns is untrusted, same as recipes: an id that isn't in
the catalogue is dropped to null so the existing correction UI asks you, and a
quantity that doesn't parse becomes a single unit. The household's remembered
corrections are applied in the browser afterwards, exactly as with the sample.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import shutil
import sys
from pathlib import Path

# Lines that are never food. Cheap to check, and it keeps the model's input
# short enough that a 7B answers in one pass.
NOISE = re.compile(
    r"^\s*("
    r"total|sous[- ]?total|sub ?total|tva|t\.v\.a|vat|montant|net a payer|net à payer|"
    r"carte|card|cb|especes|espèces|cash|monnaie|rendu|change|merci|thank|"
    r"ticket|caisse|vendeur|siret|tel|t[ée]l|www|http|facture|remise|reduction|"
    r"réduction|fidelite|fidélité|points|solde|date|heure|caissier|client"
    r")\b", re.I)

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
  is money."""

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
        out.append(s[:80])
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ step 2: mapping

async def read(data: bytes, llm, known: dict[str, dict], languages: str = "fra+eng") -> dict:
    """Photograph -> the receipt shape the app's correction screen already uses."""
    lines = tidy(to_text(data, languages))
    if not lines:
        raise ReceiptError("That looks like a photo of something else — no product "
                           "lines were found on it.")
    if not llm:
        raise ReceiptError("The assistant is switched off, so the lines can't be "
                           "matched to ingredients. Turn it on in backend/.env.")

    ids = ", ".join(f"{i} ({v.get('unit', 'g')})" for i, v in list(known.items())[:600])
    user = ("Ingredient ids: " + ids + "\n\nReceipt text:\n" + "\n".join(lines))
    try:
        out = await llm.json(SYSTEM, user, SCHEMA, 2000)
    except Exception as e:  # noqa: BLE001
        raise ReceiptError(f"The assistant couldn't read the receipt "
                           f"({type(e).__name__}).") from None
    return clean(out, known)


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
