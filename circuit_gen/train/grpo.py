"""
SFT warm-up and GRPO on single-shot circuit design, rewarded by circuit_gen's checks.

Each prompt is a board description plus the parts sheet (circuit_gen/rl.py);
the model writes the complete SKiDL code in one reply, thinking off. The
reward is circuit_gen.rl.score: 0.2 if the code runs, up to 0.2 more for ERC
and the structural checks (less 0.05 per error), +0.6 times the fraction of
the board's hidden requirement tests that pass in ngspice. Scoring needs
SKiDL, the KiCad libraries and ngspice, so this runs inside
train/grpo_job.sh, not a bare `hf jobs uv run`.

    python grpo.py sft    # LoRA on verified reference code (rl/sft.jsonl), merged into
                          # SFT_DIR; the adapter is pushed to OUTPUT_REPO under sft/
    python grpo.py grpo   # GRPO LoRA on GRPO_BASE (the merged SFT model, or BASE_MODEL)

Environment: RL_DATA, BASE_MODEL, GRPO_BASE, SFT_DIR, SFT_EPOCHS, OUTPUT_REPO,
MAX_STEPS, NUM_GENERATIONS, LR, SCORE_JOBS.
"""

import json
import os
import sys

from datasets import load_dataset
from peft import LoraConfig

from circuit_gen.rl import score_many

DATA = os.environ.get("RL_DATA", "runs/rl")
BASE_MODEL = os.environ.get("BASE_MODEL", "Qwen/Qwen3-8B")
GRPO_BASE = os.environ.get("GRPO_BASE", BASE_MODEL)
SFT_DIR = os.environ.get("SFT_DIR", "sft_merged")
SFT_EPOCHS = float(os.environ.get("SFT_EPOCHS", "1"))
OUTPUT_REPO = os.environ.get("OUTPUT_REPO", "")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "150"))
NUM_GENERATIONS = int(os.environ.get("NUM_GENERATIONS", "8"))
LR = float(os.environ.get("LR", "2e-5"))
SCORE_JOBS = int(os.environ.get("SCORE_JOBS", str(max(2, (os.cpu_count() or 4) - 2))))
OUT_DIR = "/data/grpo" if os.path.isdir("/data") else "grpo"

stats = {"calls": 0}


def lora():
    return LoraConfig(r=32, lora_alpha=64, lora_dropout=0.0, bias="none", task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                      "gate_proj", "up_proj", "down_proj"])


def circuit_reward(prompts, completions, tests, **kwargs):
    """circuit_gen.rl.score for each completion, scored in parallel processes."""
    texts = [c[-1]["content"] if isinstance(c, list) else c for c in completions]
    scored = score_many(texts, [json.loads(t) for t in tests], jobs=SCORE_JOBS)
    rewards = [r for r, _ in scored]
    stats["calls"] += 1
    if stats["calls"] % 5 == 0:
        ran = sum(d.get("ran", False) for _, d in scored) / len(scored)
        clean = sum(d.get("checks", False) for _, d in scored) / len(scored)
        full = sum(r >= 0.999 for r in rewards) / len(rewards)
        print(f"reward call {stats['calls']}: mean {sum(rewards) / len(rewards):.3f}, "
              f"ran {ran:.0%}, checks clean {clean:.0%}, full pass {full:.0%}", flush=True)
    return rewards


def sft():
    """Imitation warm-up: LoRA on the training boards' verified code, merged into SFT_DIR."""
    from transformers import AutoTokenizer
    from trl import SFTConfig, SFTTrainer
    data = load_dataset("json", data_files=f"{DATA}/sft.jsonl", split="train").shuffle(seed=0)
    data = data.select_columns(["prompt", "completion", "chat_template_kwargs"])
    config = SFTConfig(
        output_dir=f"{OUT_DIR}-sft",
        num_train_epochs=SFT_EPOCHS,
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4,
        learning_rate=1e-4,
        lr_scheduler_type="cosine",
        warmup_steps=0.03,
        max_length=4096,
        completion_only_loss=True,            # learn the code, not the prompt
        bf16=True,
        gradient_checkpointing=True,
        logging_steps=10,
        save_strategy="no",
        report_to="none",
    )
    trainer = SFTTrainer(model=BASE_MODEL, train_dataset=data, args=config, peft_config=lora())
    trainer.train()
    trainer.save_model(f"{OUT_DIR}-sft/adapter")
    if OUTPUT_REPO:
        from huggingface_hub import HfApi
        api = HfApi()
        api.create_repo(OUTPUT_REPO, private=True, exist_ok=True)
        api.upload_folder(repo_id=OUTPUT_REPO, folder_path=f"{OUT_DIR}-sft/adapter",
                          path_in_repo="sft", commit_message="SFT warm-up adapter")
    merged = trainer.model.merge_and_unload()
    merged.save_pretrained(SFT_DIR)
    AutoTokenizer.from_pretrained(BASE_MODEL).save_pretrained(SFT_DIR)
    print(f"SFT done: merged model in {SFT_DIR}", flush=True)


def grpo():
    from trl import GRPOConfig, GRPOTrainer
    train = load_dataset("json", data_files=f"{DATA}/train.jsonl", split="train").shuffle(seed=0)
    config = GRPOConfig(
        output_dir=OUT_DIR,
        max_steps=MAX_STEPS,
        learning_rate=LR,
        lr_scheduler_type="constant_with_warmup",
        warmup_steps=5,
        per_device_train_batch_size=NUM_GENERATIONS,     # one prompt's group per micro-batch
        gradient_accumulation_steps=4,                    # 4 prompts per optimizer step
        num_generations=NUM_GENERATIONS,
        max_completion_length=3072,
        temperature=1.0,
        chat_template_kwargs={"enable_thinking": False},
        mask_truncated_completions=True,                  # a cut-off reply teaches nothing
        use_vllm=True,
        vllm_mode="colocate",
        vllm_gpu_memory_utilization=0.35,
        vllm_max_model_length=8192,
        bf16=True,
        gradient_checkpointing=True,
        logging_steps=1,
        save_steps=50,
        save_total_limit=2,
        push_to_hub=bool(OUTPUT_REPO),
        hub_model_id=OUTPUT_REPO or None,
        hub_private_repo=True,
        hub_strategy="every_save",
        report_to="none",
    )
    trainer = GRPOTrainer(model=GRPO_BASE, reward_funcs=circuit_reward, args=config,
                          train_dataset=train, peft_config=lora())
    trainer.train()
    trainer.save_model(f"{OUT_DIR}/final")
    if OUTPUT_REPO:
        trainer.push_to_hub()
    print(f"GRPO done on {GRPO_BASE}: https://huggingface.co/{OUTPUT_REPO}", flush=True)


if __name__ == "__main__":
    # Scoring runs in spawned workers, which re-import this file: keep side effects here.
    {"sft": sft, "grpo": grpo}[sys.argv[1] if len(sys.argv) > 1 else "grpo"]()
