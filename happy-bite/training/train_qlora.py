"""Fine-tune a Qwen model on the source recipe examples.

On NVIDIA, this uses 4-bit QLoRA for the 7B model. On Apple Silicon, it
uses LoRA without bitsandbytes quantization; the default 1.5B model fits
more comfortably in a 16 GB Mac's unified memory.

    python build_dataset.py --csv full_dataset.csv   # make data/*.jsonl
    python train_qlora.py                            # this — ~1–2 h
    python test_model.py                             # check it learned
    python export_ollama.py                          # merge + package

Output: adapters in out/qwen-happybite-lora/.
"""
from __future__ import annotations

import os
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer

HERE = Path(__file__).resolve().parent
CUDA = torch.cuda.is_available()
MPS = torch.backends.mps.is_available()
BASE = os.getenv("BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct" if CUDA
                 else "Qwen/Qwen2.5-1.5B-Instruct")
OUT = HERE / "out" / "qwen-happybite-lora"


def main() -> None:
    if not CUDA and not MPS:
        raise SystemExit("No CUDA or MPS device. Training needs an NVIDIA GPU or Apple Silicon.")
    if MPS and not CUDA:
        print(f"Using Apple MPS with {BASE} (no bitsandbytes quantization).")

    data = HERE / "data"
    if not (data / "train.jsonl").exists():
        raise SystemExit("No data/train.jsonl — run build_dataset.py first.")

    tok = AutoTokenizer.from_pretrained(BASE, token=os.getenv("HF_TOKEN") or None)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    if CUDA:
        quant = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
        model = AutoModelForCausalLM.from_pretrained(
            BASE, quantization_config=quant, torch_dtype=torch.bfloat16,
            device_map="auto", token=os.getenv("HF_TOKEN") or None)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            BASE, torch_dtype=torch.float16, token=os.getenv("HF_TOKEN") or None)
        model.to("mps")
    model.config.use_cache = False

    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"])

    ds = load_dataset("json", data_files={
        "train": str(data / "train.jsonl"), "eval": str(data / "val.jsonl")})

    def render(batch):
        return {"text": [tok.apply_chat_template(m, tokenize=False,
                                                 add_generation_prompt=False)
                         for m in batch["messages"]]}
    ds = ds.map(render, batched=True, remove_columns=ds["train"].column_names)

    cfg = SFTConfig(
        output_dir=str(OUT), num_train_epochs=2,
        per_device_train_batch_size=1, gradient_accumulation_steps=16,
        learning_rate=2e-4, lr_scheduler_type="cosine", warmup_ratio=0.03,
        logging_steps=10, eval_strategy="steps", eval_steps=100,
        save_steps=200, save_total_limit=2, bf16=CUDA, fp16=False,
        max_seq_length=2048, packing=True, dataset_text_field="text",
        gradient_checkpointing=True, report_to="none")

    trainer = SFTTrainer(model=model, args=cfg, peft_config=lora,
                         train_dataset=ds["train"], eval_dataset=ds["eval"],
                         processing_class=tok)
    trainer.train()
    trainer.save_model(str(OUT))
    tok.save_pretrained(str(OUT))
    print(f"\nAdapters saved to {OUT}\nNext: python test_model.py")


if __name__ == "__main__":
    main()
