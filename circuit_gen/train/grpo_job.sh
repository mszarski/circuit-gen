#!/bin/bash
# SFT warm-up then GRPO on single-shot circuit design inside one Hugging Face Job:
# installs the toolchain the reward needs (KiCad libraries, SKiDL, ngspice),
# scores the base model, imitates the training boards' verified code (merged
# into a new base), scores that, trains a GRPO LoRA on it with circuit_gen's
# checks as the reward, scores the result, and uploads everything. Code and RL
# data come from $DATASET (code/ and rl/). The final model is BASE_MODEL plus
# OUTPUT_REPO/sft (merged) plus the GRPO adapter at OUTPUT_REPO's root.
#
#   hf jobs run --flavor h200 --timeout 4h --secrets HF_TOKEN \
#       -e DATASET=user/circuit-gen-sft -e OUTPUT_REPO=user/qwen3-8b-circuit-grpo \
#       ubuntu:24.04 bash -c "$(cat circuit_gen/train/grpo_job.sh)"
set -euo pipefail

BASE_MODEL="${BASE_MODEL:-Qwen/Qwen3-8B}"
RUN="${RUN:-grpo-$(date +%Y%m%d-%H%M)}"
EVAL_LIMIT="${EVAL_LIMIT:-120}"
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq --no-install-recommends kicad-symbols kicad-footprints kicad ngspice \
    python3-venv python3-dev gcc g++ curl ca-certificates > /dev/null
python3 -m venv --system-site-packages /venv
export PATH=/venv/bin:$PATH
pip install -q "trl[vllm,peft]==1.16.0" datasets "huggingface_hub>=1.0"

mkdir -p /work && cd /work
hf download "$DATASET" code/circuit_gen.tar.gz --repo-type dataset --local-dir . > /dev/null
tar xzf code/circuit_gen.tar.gz
hf download "$DATASET" --repo-type dataset --include "rl/*" --local-dir . > /dev/null
pip install -q -r circuit_gen/requirements.txt
pip install -q --no-deps skidl==2.3.0 InSpice==1.7.0.7
export PYTHONPATH=/work RL_DATA=/work/rl BASE_MODEL
# No CUDA toolkit (nvcc) in this image, so skip FlashInfer's JIT-compiled sampler.
export VLLM_USE_FLASHINFER_SAMPLER=0

mkdir -p results
upload() {
    hf upload "$DATASET" results "rl_runs/$RUN" --repo-type dataset --commit-message "$RUN" \
        > /dev/null || true
}
python3 circuit_gen/train/rl_eval.py rl/eval.jsonl results/base_eval.json \
    --model "$BASE_MODEL" --limit "$EVAL_LIMIT" | tee -a results/summary.txt
python3 circuit_gen/train/rl_eval.py rl/bench.jsonl results/base_bench.json \
    --model "$BASE_MODEL" | tee -a results/summary.txt
upload

export SFT_DIR=/work/sft_merged
python3 circuit_gen/train/grpo.py sft 2>&1 | tee results/sft.log | grep -E "'loss'|Error|SFT done" || true
python3 circuit_gen/train/rl_eval.py rl/eval.jsonl results/sft_eval.json \
    --model "$SFT_DIR" --limit "$EVAL_LIMIT" | tee -a results/summary.txt
python3 circuit_gen/train/rl_eval.py rl/bench.jsonl results/sft_bench.json \
    --model "$SFT_DIR" | tee -a results/summary.txt
upload

GRPO_BASE="$SFT_DIR" python3 circuit_gen/train/grpo.py grpo 2>&1 | tee results/train.log \
    | grep -E "reward call|Error|GRPO done" || true
ADAPTER=$( [ -d /data/grpo/final ] && echo /data/grpo/final || echo grpo/final )
python3 circuit_gen/train/rl_eval.py rl/eval.jsonl results/tuned_eval.json \
    --model "$SFT_DIR" --adapter "$ADAPTER" --limit "$EVAL_LIMIT" | tee -a results/summary.txt
python3 circuit_gen/train/rl_eval.py rl/bench.jsonl results/tuned_bench.json \
    --model "$SFT_DIR" --adapter "$ADAPTER" | tee -a results/summary.txt
upload
cat results/summary.txt
