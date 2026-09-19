# Kitchen Data Engine (prototype)

A standalone retrieval engine for cooking data: it downloads recipe,
substitution and nutrition datasets from Hugging Face, embeds them with
[`BAAI/bge-small-en-v1.5`](https://huggingface.co/BAAI/bge-small-en-v1.5) and
stores them in a local [ChromaDB](https://www.trychroma.com/) database you can
search from Python.

> **Not used by the Happy Bite app.** The app's recipe retrieval is
> `happy-bite/backend/app/rag.py` (SQLite full-text search, no embeddings), and
> nothing under `happy-bite/` imports this folder. It is a separate experiment
> kept for reference.

## Files

```
data/
├── README_DATA.md
└── kitchen-data-engine/
    ├── ingest.py           downloads the datasets and builds ./kitchen_db
    ├── kitchen_engine.py   KitchenDataEngine: loads the database and searches it
    ├── kitchen_data.py     RecipeSessionManager (step-by-step state) and TTSTextSanitizer
    ├── requirements.txt    torch, chromadb, datasets, sentence-transformers, tqdm
    ├── test_engine.py      empty for now: no tests written yet
    └── kitchen_db/         created by ingest.py, not in git
```

## Requirements

- Python 3.10, 3.11 or 3.12
- About 15 GB of free disk space for the downloads, embeddings and database
- Optional acceleration: an NVIDIA GPU (CUDA) or Apple Silicon (MPS). The
  scripts pick `cuda`, then `mps`, then `cpu` on their own.

## Setup

Run everything from inside `data/kitchen-data-engine/`: the scripts read and
write `./kitchen_db` relative to where you run them.

macOS / Linux:

```bash
cd data/kitchen-data-engine
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
cd data\kitchen-data-engine
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

## Build the database

```bash
python ingest.py
```

This creates three collections in `./kitchen_db`:

| Collection | Source dataset | Rows |
|---|---|---|
| `substitutions` | `oraclemangle/historical-culinary-substitutions` (`substitutions.jsonl`) | all (~25k) |
| `nutrition` | `omid5/usda-fdc-foods-cleaned` | first 50,000 |
| `recipes` | `Kaiser1308/CookingRecipes` | first 50,000 |

Expect a long first run: every row is downloaded and embedded.

## Use it from Python

```python
from kitchen_engine import KitchenDataEngine

engine = KitchenDataEngine()          # loads the embedder and ./kitchen_db once

def answer(user_speech_text: str) -> None:
    # 1. Retrieve context. The query picks the collection:
    #    "calories/protein/fat/carbs/nutrition/macros" -> nutrition,
    #    "substitute/instead/replace/alternative"      -> substitutions,
    #    anything else                                 -> recipes.
    context = engine.search(user_speech_text, top_k=2)

    # 2. Give it to your model (my_llm_model is yours to supply).
    system_prompt = f"Use this background culinary data to answer: {context['documents']}"
    reply = my_llm_model.generate(system_prompt, user_speech_text)

    # 3. Strip markdown and spell out fractions before speaking it.
    my_tts_model.speak(engine.sanitize_for_tts(reply))
```

`search` returns `{"domain": "Recipe" | "Substitution" | "Nutrition", "documents": [...], "metadatas": [...]}`.

`engine.session` is a `RecipeSessionManager` for walking through a recipe:
`start_recipe({"title": ..., "ingredients": [...], "directions": [...]})`, `get_current_step()`, `next_step()`, `previous_step()`,
`get_ingredients()`, `clear_session()`.
