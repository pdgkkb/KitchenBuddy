#!/usr/bin/env python3
"""Happy Bite — is the reference corpus any good?

shared/recipes.json is not decoration: it is the corpus the RAG hands the
model before it writes anything. A fault in here is taught, not just shown.
This reads it as a cook would and complains in plain words.

    python3 audit.py [path/to/recipes.json]

It writes nothing.
"""
import json, re, sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "shared/recipes.json")
if not path.is_file():
    for guess in ("shared/recipes.json", "happy-bite/shared/recipes.json", "recipes.json"):
        if Path(guess).is_file():
            path = Path(guess); break
if not path.is_file():
    sys.exit(f"No recipe book at {path}. shared/recipes.json has been removed from the project; "
             "pass the path of a recipes file to audit one.")
d = json.loads(path.read_text(encoding="utf-8"))

PROSE = {"oliveoil": "oil", "blackpepper": "pepper", "limejuice": "lime",
         "fishsauce": "fish sauce", "coconutmilk": "coconut milk",
         "garammasala": "masala", "goat": "cheese"}

faults, notes = [], []
for r in d["recipes"]:
    rid, serves = r["id"], r.get("serves", 4)
    have = ({n["id"] for n in r.get("needs") or []}
            | {s["id"] for s in r.get("seasoning") or []})
    used = set()
    for st in r.get("steps") or []:
        for u in st.get("uses", []):
            if u not in have:
                faults.append(f"{rid}: a step uses '{u}', which this recipe does not contain")
            used.add(u)

    for iid in sorted(have - used):
        word = PROSE.get(iid, iid)
        if any(word[:5] in (st["do"] + " " + st.get("why", "")).lower() for st in r.get("steps") or []):
            notes.append(f"{rid}: '{iid}' is named in the text but has no 'uses' entry")
        else:
            faults.append(f"{rid}: '{iid}' is on the shopping list and NEVER used")

    # Wall clock, not the naive sum: a step that says "while" or "start these
    # first" runs underneath the ones after it. Without this the check cries
    # wolf on every recipe that boils potatoes and cooks something else.
    clock, pending = 0, 0
    for st in r.get("steps") or []:
        # A step that says "start this first" puts something on and walks
        # away; everything after it runs while that timer ticks. Anything
        # else is hands-on and costs its own minutes. Without this the check
        # cried wolf on every recipe that boils potatoes and cooks alongside.
        if re.search(r"start th(is|ese) first", st["do"], re.I):
            pending = max(pending, st.get("minutes") or 0)
        else:
            clock += st.get("minutes") or 0
            pending = max(pending - (st.get("minutes") or 0), 0)
    clock += pending
    claimed = r.get("minutes") or 0
    if claimed and clock > claimed * 1.2:
        faults.append(f"{rid}: the card says {claimed} min; the steps need {clock}")
    elif claimed and clock and claimed > clock * 1.7:
        notes.append(f"{rid}: the card says {claimed} min but the steps only need {clock}")

    for i, st in enumerate(r.get("steps") or [], 1):
        n = len([p for p in re.split(r"(?<=[.!?])\s+", st["do"].strip()) if p])
        if n > 2:
            notes.append(f"{rid}: step {i} packs {n} sentences — one action per step")
        heat = st.get("heat", "")
        if (st.get("minutes") or 0) > 40 and "Low" not in heat and "Oven" not in heat:
            faults.append(f"{rid}: step {i} runs {st.get('minutes')} min on {heat or 'no stated heat'}")

    # Only about meat and fish. Onions do not have sides.
    MEAT = {"beef", "pork", "chicken", "lamb", "cod", "salmon", "duck", "veal"}
    seared = [st for st in r.get("steps") or []
              if re.search(r"\b(sear|brown|fry|grill|pan[- ]fry)", st["do"], re.I)
              and st.get("heat", "") in ("High", "Medium-high")
              and (set(st.get("uses", [])) & MEAT or have & MEAT)]
    if seared and not any(re.search(r"\b(side|turn them|flip)", st["do"], re.I) for st in r.get("steps") or []):
        faults.append(f"{rid}: meat is seared but no step ever turns it")

    for n in r.get("needs") or []:
        if (n.get("qty") or 0) > 250 * serves and n["qty"] > 10:
            notes.append(f"{rid}: {n.get('id')} is {n.get('qty')} for {serves} — check the unit")

print(f"{path}  —  {len(d['recipes'])} recipes\n")
print("FAULTS  (a cook would get this wrong)")
print("\n".join("  x " + f for f in faults) or "  none")
print("\nNOTES  (worth a look)")
print("\n".join("  - " + n for n in notes) or "  none")
sys.exit(1 if faults else 0)