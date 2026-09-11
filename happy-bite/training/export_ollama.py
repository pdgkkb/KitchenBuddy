"""Merge the LoRA adapters into Qwen2.5-7B and package for serving.

After this you have a standalone model the Happy Bite server talks to via
its existing OpenAI-compatible path. Two ways to serve it:

  A) vLLM (simplest on the GPU box, no conversion):
        pip install vllm
        vllm serve ./out/qwen2.5-happybite-merged --served-model-name happybite
     backend/.env:
        LLM_PROVIDER=openai
        LLM_MODEL=happybite
        OPENAI_BASE_URL=http://<gpu-host>:8000/v1
        OPENAI_API_KEY=none

  B) Ollama (nice on a home machine, needs a GGUF):
        # convert out/…-merged to GGUF with llama.cpp's convert_hf_to_gguf.py,
        # save it beside this file as qwen2.5-happybite.gguf, then:
        ollama create happybite -f Modelfile
     backend/.env:
        LLM_PROVIDER=openai
        LLM_MODEL=happybite
        OPENAI_BASE_URL=http://localhost:11434/v1
        OPENAI_API_KEY=ollama
"""
from __future__ import annotations

import os
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

HERE = Path(__file__).resolve().parent
BASE = os.getenv("BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct" if torch.cuda.is_available()
                 else "Qwen/Qwen2.5-1.5B-Instruct")
ADAPTERS = HERE / "out" / "qwen-happybite-lora"
MERGED = HERE / "out" / "qwen2.5-happybite-merged"


def main() -> None:
    if not ADAPTERS.exists():
        raise SystemExit("No adapters at out/qwen-happybite-lora — train first.")

    print("Loading base model to merge…")
    base = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float16,
        token=os.getenv("HF_TOKEN") or None)
    merged = PeftModel.from_pretrained(base, str(ADAPTERS)).merge_and_unload()

    MERGED.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(MERGED), safe_serialization=True)
    AutoTokenizer.from_pretrained(str(ADAPTERS)).save_pretrained(str(MERGED))

    (HERE / "Modelfile").write_text(
        "FROM ./qwen2.5-happybite.gguf\n"
        "PARAMETER temperature 0.4\nPARAMETER top_p 0.9\nPARAMETER num_ctx 8192\n",
        encoding="utf-8")
    print(f"Merged model at {MERGED}")
    print("Serve with vLLM (A) or convert to GGUF for Ollama (B).")


if __name__ == "__main__":
    main()
