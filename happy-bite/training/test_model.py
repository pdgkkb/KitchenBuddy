"""Check the fine-tune actually learned the source recipe format.

Loads the model (base + your adapters, or a merged folder), runs the
held-out val prompts through it, and checks each reply for the source schema:
Title, Ingredients, and Instructions. It reports how many replies are valid
and prints one example.
A stock model usually fails this; a good fine-tune should pass most.

    python test_model.py               # base + out/…-lora adapters
    python test_model.py --merged out/qwen2.5-happybite-merged
    python test_model.py --base-only   # baseline: the un-tuned model

Needs a GPU (or a lot of patience on CPU with --n 3).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "backend"))
from app.providers.llm import parse_json, LLMError        # noqa: E402

BASE = os.getenv("BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct" if torch.cuda.is_available()
                 else "Qwen/Qwen2.5-1.5B-Instruct")
ADAPTERS = HERE / "out" / "qwen-happybite-lora"


def load(args):
    tok_src = args.merged or (str(ADAPTERS) if ADAPTERS.exists() and not args.base_only else BASE)
    tok = AutoTokenizer.from_pretrained(tok_src, token=os.getenv("HF_TOKEN") or None)
    cuda = torch.cuda.is_available()
    mps = torch.backends.mps.is_available()
    dtype = torch.bfloat16 if cuda else torch.float16 if mps else torch.float32
    dev = "auto" if cuda else "mps" if mps else None
    if args.merged:
        model = AutoModelForCausalLM.from_pretrained(args.merged, torch_dtype=dtype, device_map=dev)
    else:
        model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=dtype, device_map=dev,
                                                     token=os.getenv("HF_TOKEN") or None)
        if ADAPTERS.exists() and not args.base_only:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, str(ADAPTERS))
    model.eval()
    return tok, model


def generate(tok, model, messages) -> str:
    text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    ids = tok(text, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**ids, max_new_tokens=900, do_sample=False,
                             pad_token_id=tok.pad_token_id or tok.eos_token_id)
    return tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)


    def valid_source_recipe(value) -> bool:
        return (isinstance(value, dict)
            and isinstance(value.get("Title"), str) and bool(value["Title"].strip())
            and isinstance(value.get("Ingredients"), list) and bool(value["Ingredients"])
            and all(isinstance(item, str) and item.strip() for item in value["Ingredients"])
            and isinstance(value.get("Instructions"), list) and bool(value["Instructions"])
            and all(isinstance(item, str) and item.strip() for item in value["Instructions"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merged", default=None, help="Path to a merged model folder")
    ap.add_argument("--base-only", action="store_true", help="Skip adapters (baseline)")
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()

    val = HERE / "data" / "val.jsonl"
    if not val.exists():
        raise SystemExit("No data/val.jsonl — run build_dataset.py first.")
    rows = [json.loads(l) for l in val.read_text().splitlines()][: args.n]

    tok, model = load(args)
    ok, shown = 0, False
    for r in rows:
        prompt = [m for m in r["messages"] if m["role"] != "assistant"]
        reply = generate(tok, model, prompt)
        try:
            recipe = parse_json(reply)
        except (LLMError, ValueError):
            recipe = None
        if valid_source_recipe(recipe):
            ok += 1
            if not shown:
                shown = True
                print("\nExample valid recipe:")
                print(json.dumps(recipe, indent=2, ensure_ascii=False)[:900])
    print(f"\n{ok}/{len(rows)} replies matched the source recipe schema "
          f"({100*ok//max(1,len(rows))}%).")


if __name__ == "__main__":
    main()
