"""
Single-shot reward of a model (optionally with a LoRA adapter) on RL prompts,
generated with vLLM offline (thinking off) and scored by circuit_gen.rl.

    python rl_eval.py PROMPTS.jsonl OUT.json [--adapter DIR] [--limit N] [--samples K]
"""

import argparse
import json
import random

from vllm import LLM, SamplingParams
from vllm.lora.request import LoRARequest

from circuit_gen.rl import evaluate


def main():
    # Scoring runs in spawned workers, which re-import this file: keep side effects in main().
    p = argparse.ArgumentParser()
    p.add_argument("prompts")
    p.add_argument("out")
    p.add_argument("--model", default="Qwen/Qwen3-8B")
    p.add_argument("--adapter", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--samples", type=int, default=2)
    p.add_argument("--jobs", type=int, default=16)
    args = p.parse_args()

    rows = [json.loads(l) for l in open(args.prompts)]
    if args.limit and len(rows) > args.limit:
        rows = random.Random(0).sample(rows, args.limit)

    llm = LLM(model=args.model, enable_lora=bool(args.adapter), max_lora_rank=32,
              max_model_len=8192, gpu_memory_utilization=0.85)
    params = SamplingParams(temperature=0.6, top_p=0.95, max_tokens=3072, n=args.samples, seed=0)
    lora = LoRARequest("circuit", 1, args.adapter) if args.adapter else None
    outputs = llm.chat([r["prompt"] for r in rows], params, lora_request=lora,
                       chat_template_kwargs={"enable_thinking": False})
    texts = [o.text for out in outputs for o in out.outputs]
    it = iter(texts)
    result = evaluate(rows, lambda _: next(it), samples=args.samples, jobs=args.jobs)
    json.dump({"model": args.model, "adapter": args.adapter, **result}, open(args.out, "w"), indent=1)
    print(f"{args.adapter or args.model} on {args.prompts}: mean reward {result['mean_reward']:.3f}, "
          f"full pass {result['full_pass']:.0%}, ran {result['ran']:.0%} ({result['samples']} samples)")


if __name__ == "__main__":
    main()
