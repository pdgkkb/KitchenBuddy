# Training a Qwen model for Happy Bite

Fine-tunes **Qwen2.5-7B-Instruct** on a recipe dataset so it learns the
dataset's own recipe schema and ingredient vocabulary. The training data is
not converted to Happy Bite's catalogue.

## Honest constraints — read first

- **This does not run where Claude built it** (no GPU, no Hugging Face
  access). Everything here runs on *your* machine.
- **Training supports NVIDIA CUDA and Apple Silicon MPS.** CUDA can train
  Qwen2.5-7B with 4-bit QLoRA. A 16 GB Mac should use the smaller
  Qwen2.5-1.5B fallback; a 7B MPS run may run out of unified memory.
- **Use Python 3.11 or 3.12.** The ML libraries don't publish wheels for
  3.13/3.14 yet, so `pip install` will fail on those.

## The dataset

The default is **`Kaiser1308/CookingRecipes`**, loaded from Hugging Face with
streaming. Its `train` split contains the complete dataset, and its columns
are `title`, `ingredients`, and `directions`. The builder preserves every
ingredient string and instruction without applying Happy Bite's catalogue or
validator.

Other options: pass `--dataset owner/name` for any HF recipe dataset with
Title/Ingredients/Instructions-like columns (the builder matches column
names case-insensitively), or `--csv path.csv` for a local file (e.g. a
Kaggle export).

## Steps

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python build_dataset.py --selftest         # offline check, no download
python build_dataset.py --peek             # preview 3 source examples, no files
python build_dataset.py                    # processes the complete Kaiser dataset
python build_dataset.py --max 8000         # optional cap
python train_qlora.py                      # CUDA or M4 MPS -> out/…-lora/
python test_model.py                       # scores replies against source schema
python export_ollama.py                    # merge -> out/…-merged/
```

Then point the app at the model — see the notes `export_ollama.py` prints
and the Qwen preset in `backend/.env.example`.

## Why the data is faithful

`build_dataset.py` normalizes only the container types needed for training:
stringified ingredient lists become arrays, and a run-on instructions
paragraph is split into instruction strings. Ingredient text, quantities,
and recipe names are preserved as provided by the source.

`test_model.py` runs the held-out prompts through the tuned model and checks
that replies contain non-empty `Title`, `Ingredients`, and `Instructions`
fields. Run it with `--base-only` first for the untuned baseline to compare.

## Files

- `build_dataset.py` — HF dataset or CSV → validated `data/*.jsonl`; `--selftest` runs offline
- `train_qlora.py` — 4-bit QLoRA fine-tune of Qwen2.5-7B-Instruct
- `test_model.py` — score the tuned model against the app's validator
- `export_ollama.py` — merge adapters, write an Ollama `Modelfile`
- `requirements.txt`, `.env.example`