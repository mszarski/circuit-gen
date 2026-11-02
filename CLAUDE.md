# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `HANDOFF.md` first** for current state, latest results, running jobs, history and next steps. This file holds only the durable rules.

## Project Overview

`circuit_gen/` turns a text description into a routed PCB: an LLM (default: open model `Qwen/Qwen3.8-27B` via Hugging Face Inference Providers; or Claude with `--backend claude`) writes SKiDL circuit code (with KiCad library search and check tools), then KiCad + FreeRouting place, route, DRC-check and export Gerbers. See `circuit_gen/README.md` for the full pipeline diagram, benchmark history and training results.

The repo also contains a model-training side: programmatic training data (`procgen.py`), LLM-designed data (`datagen.py`), and SFT/GRPO scripts run as Hugging Face Jobs (`train/`).

## Commands

```bash
circuit_gen/setup.sh                       # KiCad 7, ngspice, FreeRouting 2.1, SKiDL (Ubuntu 24.04, uses apt-get)
pytest circuit_gen/tests/                  # toolchain-dependent tests skip when tools are missing
pytest circuit_gen/tests/test_spec.py::<test_name> -v

python -m circuit_gen build circuit_gen/examples/blinker_555.py -o runs/blinker   # no model involved
python -m circuit_gen design "<description>" -o runs/x   # needs HF_TOKEN (ANTHROPIC_API_KEY for --backend claude)
python -m circuit_gen eval circuit_gen/benchmarks/boards.json -o runs/eval --samples 4 --jobs 6 [--resume]
python -m circuit_gen eval --examples -o runs/examples   # layout half only, no API calls
python -m circuit_gen compare runs/eval_a runs/eval_b
python -m circuit_gen procgen -n 1500 -o runs/procgen --repair-fraction 0.5 [--families a,b --jobs 4]
python -m circuit_gen rldata runs/procgen_v3/boards.jsonl -o runs/rl [--holdout-families ...]
```

Other subcommands: `datagen`, `export`, `schematic`, `rleval` (see `__main__.py`). Outputs go in `runs/` (gitignored).

## Architecture

- **`generate.py`** – the agentic design loop (`generate_circuit`): tool calls `search_symbols`, `get_symbol`, `search_footprints`, `check_circuit`, `simulate`; repair loop on Python/ERC/check errors; nudges (submit after 10 exploring turns, repeated-call notice, cut-off reasoning retry). `MAX_TURNS = 40`.
- **`spec.py`** – spec-first (default): a spec writer turns the description into `require(...)` lines that are then frozen. Then a coverage review (`CIRCUIT_GEN_SPEC_REVIEW`), at most one dispute/revision round, and a stress test after the first pass (each part scaled x2 / x0.5; any change no requirement notices goes back to the spec writer once).
- **`backends.py`** – `HuggingFaceBackend` (Inference Providers or any OpenAI-compatible `CIRCUIT_GEN_HF_BASE_URL`) and `ClaudeBackend`, behind `make_backend()`.
- **`runner.py`** – runs SKiDL code in a subprocess, wrapped with a PRELUDE (library paths, `require()`) and EPILOGUE (ERC, dump to `circuit.json`).
- **`checks.py`** – structural rules: footprints exist and match pins, no single-pin nets, ICs decoupled, inputs have a DC path, push-pull outputs not tied to rails.
- **`spice.py`** – maps parts to generic ngspice models; evaluates requirements at nominal and 24 tolerance corners, and the hidden benchmark tests at nominal.
- **`pipeline.py`** – `build()`: circuit → checks → simulation → `layout/board.py` → `layout/place.py` (greedy) → `layout/route.py` (DSN → FreeRouting → SES → KiCad DRC → Gerbers/SVG). Every stage records into `report.json`.
- **`evaluate.py`** – benchmark runner; flags false accepts (model declared done, hidden tests failed) and infra errors (API/network failures, not counted against the model).
- **`rl.py`** – single-shot prompts + reward: 0.2 runs, +0.2 checks (less 0.05/error), +0.6 x fraction of hidden tests.
- **`config.py`** – all tool paths and model defaults, each overridable by a `CIRCUIT_GEN_*` env var.

## Working with the user

- **Paid compute needs a go-ahead first.** HF Jobs and big Inference Provider eval runs need approval before launch; the user approves per experiment (e.g. "yes run the SFT + GRPO job when data is ready"). Say roughly what it costs / how long (H200 about 2-2.5 h, about $13 per 2 h).
- **Commit and push finished work** to the working branch; don't leave changes uncommitted (the user once had to remind). **No PRs unless asked.**
- **No model identifiers in commits, code comments, PRs or other pushed artifacts.** Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Terse instructions** ("go", "do 1 and 3", "push further", "status"). When proposing next steps, give a short **numbered list** with a recommendation; the user picks by number.
- **Honest assessments, not cheerleading.** The user challenged whether training was doing anything ("maybe we aren't actually doing much here?"). Lead with the direct answer, back it with numbers, flag contamination and noise (with 2 samples per board, per-board moves under about 0.3 are noise).
- **Results as tables** with before/after numbers; keep the `circuit_gen/README.md` results section updated with each experiment.
- **Keep the RL code** (`rl.py`, `train/`) in the repo and in the uploaded code tarball (explicit request).
- **Keep Qwen3.8-27B as the default designer** and keep strengthening the checks and benchmark (user approved).
- **Watch background jobs and report when they finish.** The user resolves HF billing/credit issues themselves; afterwards, re-check that running jobs are still alive.

## Key decisions (keep unless the user says otherwise)

- **Spec-first with frozen requirements.** When the designer wrote its own requirements it loosened the ones it was failing (8/35 false accepts vs 0). Letting the designer dispute the spec dropped pass rate to 40%; a dispute now goes back to the spec writer once.
- **Spec writer at `reasoning_effort=low`** (`CIRCUIT_GEN_SPEC_EFFORT`): at default effort the 27B hit the provider's 40,960-token cap. **Self-review** (`CIRCUIT_GEN_SPEC_REVIEW=1`) is the default; a second-model reviewer was not better.
- **A test may only check what the description states** (description fairness); hidden tests are checked from both sides (reference passes, mutants fail). 1-test-per-board results overstated pass rates.
- **KiCad + SKiDL + FreeRouting + ngspice, not EasyEDA** (closed, cloud-only, no headless scripting). EasyEDA/LCSC is only a possible parts source via `easyeda2kicad`; not implemented, and those hosts are blocked from the cloud env.
- **circuit-synth only for schematic export**; its multi-unit/instance bugs are worked around in `schematic.py` (`relabel`, `_fix_instances`).
- **Procgen families added 2026-10-31/11-01 mirror benchmark topologies** (only exact parameters excluded), so the benchmark no longer measures 8B generalisation. Report **held-out-family** scores (`rldata --holdout-families`) for any training claim.

## Dead ends (don't repeat)

- Agentic SFT of Qwen3-8B on tool-using sessions: learned the session format, not debugging (too many short templated procgen sessions vs few varied ones).
- More GRPO steps on the single-shot procgen reward: overfits procgen, doesn't raise the benchmark, lowered the hard tier twice.
- Assuming SFT is harmless for unseen parts: it narrows the model (RS-485 dropped 80% -> 2% of tests passed after SFT).
- More prompt rules against false accepts: rate stays at 1.5-4.5% regardless; the misses are spec-coverage gaps.
- Base 8B as its own spec writer in evals: writes unsatisfiable specs. Use a strong spec model (`--spec-model deepseek-ai/DeepSeek-V4.1-Flash`).
- 555 PWM above ~2 kHz or below 20% duty, and buck inputs above 24 V: unstable in the models; families are capped accordingly.
- HF Jobs L40S flavor never got scheduled; use A100 or H200 (the `l40sx1` example in `train/eval_job.sh` is stale).

## Conventions and gotchas

- **Benchmark leakage**: benchmark boards (`benchmarks/boards.json`) must never appear in training data. Every procgen family explicitly excludes its benchmark board's parameters, and `generate()` skips any brief matching a benchmark board. Keep this when adding families.
- **Benchmark integrity**: every board needs a reference in `benchmarks/reference/` and at least two hidden tests the reference passes; plausible wrong designs in `tests/test_benchmark.py` must fail them. The model never sees the hidden tests.
- `pcbnew` is imported lazily inside layout functions so circuit stages work without KiCad's Python module. Don't import it at module top level.
- `conftest.py` uses `find_spec("skidl")` rather than importing it: importing skidl writes log files. It also disables spec review by default for tests (scripted spec writers answer once).
- SKiDL is installed with `--no-deps` (its layout helpers don't build on Python 3.13); its real deps are listed in `requirements.txt`.
- Generated code runs in a subprocess on this machine — isolation of SKiDL global state, not a security sandbox.
- Switching regulators need `analysis="tran"`; DC/AC analysis of them is a clear error by design. ngspice uses Gear integration (trapezoidal rang on the 555 latch).
- `train/*_job.sh` run on Hugging Face Jobs and fetch the code from `code/circuit_gen.tar.gz` in `$DATASET`, not from git — re-upload the tarball after changing code a job depends on.
- Results in the README are dated and tied to a sample count; with 2 samples per board, single-board differences are mostly noise. Record new runs in the README in the same style.
- Schematic export (`schematic.py`) uses circuit-synth in a separate venv (`CIRCUIT_GEN_CS_PYTHON`), and verifies every export against the circuit. It emits KiCad 9 files; only KiCad 7 installs here.
- Benchmark leakage check is exact-match after `procgen.normalize()` (`_benchmark_briefs`); three duplicates once leaked into SFT data.
- Requirement tests: `i(NET)` is a magnitude and needs a source on that net; loads are resistances (`{"to": NET, "r": R}`), never currents. `freq/avg/ripple/duty/iavg` need `analysis="tran"`; averages use the last quarter, `freq` the 2nd->3rd rising crossings. Requirements run at tolerance corners, so set `tolerance=` (untoleranced parts default wide).
- `CG_BUCK` is a behavioural LM2596, valid only in CCM. `CG_OPAMP` *does* draw output current from its supply pins; the README "known limits" saying otherwise is stale.
- Providers: `reasoning_effort` goes in extra_body and is dropped on 400/422 (`_effort_supported`). `chat_template_kwargs={"enable_thinking": False}` works for Qwen3-8B but is rejected for Qwen3.8-27B. Qwen3's thinking-off template adds an empty `<think></think>`: build training data in the same thinking mode it is evaluated in.
- **Never `pkill -f <pattern>`** (it matches and kills the Claude shell); kill by PID. Likewise `while pgrep -f ...` wait loops match themselves and never end.
- Dates inside `circuit_gen/README.md` headings are unreliable; trust `git log`.
- `.claude/hooks/session-start.sh` (remote sessions only) installs `circuit_gen/requirements.txt` and runs `circuit_gen/setup.sh`.

## HF Jobs and training

- Everything is private under HF account `mszarski`. Dataset `mszarski/circuit-gen-sft`: `code/circuit_gen.tar.gz` (code jobs run), `rl/` (current RL split, **overwritten by each experiment**; regenerate others with `rldata`), `procgen*/`, `rl_runs/<run>/` (per-stage results), `eval/`, `llm/`. Models are `mszarski/qwen3-8b-circuit-*`. `claude_teacher_*` jobs on the account belong to another project.
- `runs/` is gitignored and lost with the container; keep durable copies in the dataset.
- Launch: re-upload the tarball (`tar czf circuit_gen.tar.gz --exclude=__pycache__ --exclude='*.pyc' circuit_gen; hf upload mszarski/circuit-gen-sft circuit_gen.tar.gz code/circuit_gen.tar.gz --repo-type dataset`), then `hf jobs run --detach --flavor h200 --timeout 4h --secrets HF_TOKEN -e DATASET=... -e OUTPUT_REPO=... -e RUN=... ubuntu:24.04 bash -c "$(cat circuit_gen/train/grpo_job.sh)"`. Monitor with `hf jobs inspect/logs`; fetch with `hf download ... --include "rl_runs/<run>/*"`.
- `ubuntu:24.04` has no `nvcc`: set `VLLM_USE_FLASHINFER_SAMPLER=0` and apt-install `gcc g++` for vLLM.
- Trackio crashed on a private Space: use `report_to="none"`, checkpoints to `/data` (bucket mount). transformers 5.x removed `warmup_ratio` (`warmup_steps` takes a float ratio).
- TRL 1.16: `completion_only_loss=True` for SFT, per-example `chat_template_kwargs` for thinking off, `mask_truncated_completions=True` in GRPO, vLLM colocate at `vllm_gpu_memory_utilization=0.35`.
- `score_many` spawns processes that re-import the script: every train/eval script needs an `if __name__ == "__main__"` guard.
- Job scripts upload partial results after each stage (a timeout once lost everything). Run evals one at a time (parallel runs OOM'd).
