"""Reading a recipe from a cooking website.

Most recipe sites publish a machine-readable copy for search engines
(schema.org Recipe, in JSON-LD). That's read first: exact title, times,
ingredient lines and steps, no guessing. Only when it's missing does the
visible page text get used — and then only through the model.

Turning "2 courgettes, thinly sliced" into `courgette, 400 g` is the part
that needs the model. Without one, a plain word match does what it can
and everything it couldn't place is kept as text, labelled as such.

The server fetches the page, so it refuses private addresses: a link box
must not become a way to probe the kitchen network."""

import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

MAX_BYTES = 3_000_000
HEADERS = {"User-Agent": "HappyBite/1.0 (+recipe import for personal use)",
           "Accept": "text/html,application/xhtml+xml"}


class ImportErrorPublic(ValueError):
    """A message safe to show to the user."""


# ---------------------------------------------------------------- fetching

def _guard(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ImportErrorPublic("That doesn't look like a web link.")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except socket.gaierror:
        raise ImportErrorPublic("That website couldn't be found.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ImportErrorPublic("Links to private or local addresses aren't allowed.")


async def fetch(url: str) -> str:
    _guard(url)

    async def check(request: httpx.Request):       # every redirect hop too
        _guard(str(request.url))

    async with httpx.AsyncClient(timeout=12, follow_redirects=True, max_redirects=5,
                                 headers=HEADERS, event_hooks={"request": [check]}) as http:
        try:
            async with http.stream("GET", url) as res:
                if res.status_code >= 400:
                    raise ImportErrorPublic(f"The website answered {res.status_code}.")
                chunks, size = [], 0
                async for chunk in res.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        break
                    chunks.append(chunk)
                return b"".join(chunks).decode(res.encoding or "utf-8", errors="replace")
        except httpx.HTTPError:
            raise ImportErrorPublic("The website didn't respond.") from None


# ---------------------------------------------------------------- reading

def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                yield from _walk(node[key])


def _is_recipe(node: dict) -> bool:
    t = node.get("@type")
    return t == "Recipe" or (isinstance(t, list) and "Recipe" in t)


def iso_minutes(v) -> int | None:
    """PT1H30M -> 90."""
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", v.strip())
    if not m:
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    total = d * 1440 + h * 60 + mi
    return total or None


def _steps(instr) -> list[str]:
    out: list[str] = []
    if isinstance(instr, str):
        return [s.strip() for s in re.split(r"\n+|(?<=\.)\s{2,}", BeautifulSoup(instr, "html.parser").get_text("\n")) if s.strip()]
    for item in instr if isinstance(instr, list) else [instr]:
        if isinstance(item, str):
            out.append(item.strip())
        elif isinstance(item, dict):
            if item.get("itemListElement"):
                out.extend(_steps(item["itemListElement"]))
            elif item.get("text"):
                out.append(BeautifulSoup(str(item["text"]), "html.parser").get_text(" ").strip())
    return [s for s in out if s]


def _image(v) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v:
        return _image(v[0])
    if isinstance(v, dict):
        return v.get("url")
    return None


def _yield(v) -> int | None:
    for item in v if isinstance(v, list) else [v]:
        m = re.search(r"\d+", str(item or ""))
        if m:
            return int(m.group())
    return None


def read_page(html: str, url: str) -> dict:
    """Structured recipe if the page has one, else a text excerpt."""
    soup = BeautifulSoup(html, "html.parser")
    site = urlparse(url).hostname or ""
    site = site[4:] if site.startswith("www.") else site

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(data):
            if _is_recipe(node):
                return {
                    "kind": "structured", "site": site, "url": url,
                    "name": BeautifulSoup(str(node.get("name", "")), "html.parser").get_text().strip(),
                    "image": _image(node.get("image")),
                    "minutes": iso_minutes(node.get("totalTime")) or (
                        (iso_minutes(node.get("prepTime")) or 0) + (iso_minutes(node.get("cookTime")) or 0)) or None,
                    "serves": _yield(node.get("recipeYield")),
                    "cuisine": node.get("recipeCuisine") if isinstance(node.get("recipeCuisine"), str) else None,
                    "ingredients": [BeautifulSoup(str(i), "html.parser").get_text(" ").strip()
                                    for i in node.get("recipeIngredient") or [] if str(i).strip()],
                    "steps": _steps(node.get("recipeInstructions")),
                }

    for junk in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        junk.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n")).strip()
    title = soup.title.get_text().strip() if soup.title else site
    og = soup.find("meta", property="og:image")
    return {"kind": "text", "site": site, "url": url, "name": title,
            "image": og.get("content") if og else None, "text": text[:14000]}


# ---------------------------------------------------------------- mapping without a model

# Everything to grams or millilitres, treated as the same thing — close
# enough for a kitchen. Spoons are volumes, not "some amount".
UNIT_G = {"g": 1, "gr": 1, "gram": 1, "grams": 1, "kg": 1000, "kilo": 1000,
          "oz": 28, "lb": 454, "ml": 1, "cl": 10, "l": 1000, "litre": 1000, "liter": 1000,
          "tbsp": 15, "tablespoon": 15, "tablespoons": 15, "tsp": 5, "teaspoon": 5,
          "teaspoons": 5, "cup": 240, "cups": 240, "pinch": 0.5}


def _stem(word: str) -> str:
    return re.sub(r"(es|s)$", "", word.lower())


def match_line(line: str, known: dict[str, dict]) -> dict | None:
    """'2 courgettes, sliced' -> {'id': 'courgette', 'qty': 400}. Plain
    word matching; returns None rather than a guess."""
    low = line.lower()
    best = None
    for iid, ing in known.items():
        words = re.sub(r"[^a-zà-ÿ ]", " ", ing["name"].lower()).split()
        for key in (w for w in words if len(w) >= 3):
            # Suffix optional: the catalogue name may be plural ("Courgettes")
            # while the line is singular ("400 g courgette"), or vice versa.
            if re.search(rf"\b{re.escape(_stem(key))}(e|es|s)?\b", low) \
                    and (not best or len(key) > len(best[1])):
                best = (iid, key)
    if not best:
        return None
    iid, ing = best[0], known[best[0]]
    unit = ing.get("unit", "g")

    m = re.match(r"\s*(\d+(?:[.,]\d+)?|\d+/\d+|½|¼|¾)\s*([a-zA-Z]+)?", line)
    if not m:
        return {"id": iid, "qty": 1 if unit == "u" else 100, "guessed": True}
    raw = m.group(1).replace(",", ".")
    qty = {"½": .5, "¼": .25, "¾": .75}.get(raw) or (
        float(raw.split("/")[0]) / float(raw.split("/")[1]) if "/" in raw else float(raw))
    word = (m.group(2) or "").lower()

    if word in UNIT_G:
        base = qty * UNIT_G[word]                     # grams or millilitres
        if unit == "u" and ing.get("perPiece"):
            return {"id": iid, "qty": max(1, round(base / ing["perPiece"]))}
        if unit == "cl":
            return {"id": iid, "qty": round(base / 10, 1)}
        if unit == "l":
            return {"id": iid, "qty": round(base / 1000, 2)}
        return {"id": iid, "qty": round(base, 1) if base < 10 else round(base)}
    # A bare count: "2 courgettes"
    if unit == "u":
        return {"id": iid, "qty": qty}
    if ing.get("perPiece"):
        return {"id": iid, "qty": round(qty * ing["perPiece"])}
    return {"id": iid, "qty": qty, "guessed": True}


def plain_recipe(page: dict, known: dict[str, dict]) -> dict:
    """Structured page -> recipe, no model. Honest about what it skipped."""
    needs, seasoning, extras = [], [], []
    for line in page.get("ingredients", []):
        hit = match_line(line, known)
        if not hit or any(n["id"] == hit["id"] for n in needs + seasoning):
            extras.append(line)
        elif known[hit["id"]].get("category") == "seasoning":
            seasoning.append({"id": hit["id"], "qty": hit["qty"], "essential": False})
        else:
            needs.append({"id": hit["id"], "qty": hit["qty"]})
    steps = []
    for text in page.get("steps", [])[:14]:
        m = re.search(r"(\d+)\s*(?:-\s*\d+\s*)?min", text)
        steps.append({"do": text, "minutes": int(m.group(1)) if m else 0})
    return {"name": page.get("name") or "Imported recipe", "minutes": page.get("minutes"),
            "serves": page.get("serves") or 4, "cuisine": page.get("cuisine"),
            "needs": needs, "seasoning": seasoning, "extras": extras, "steps": steps}
