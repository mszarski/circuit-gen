# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
python -m circuit_gen procgen -n 1500 -o runs/procgen --repair-fraction 0.5
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
- Schematic export (`schematic.py`) uses circuit-synth in a separate venv (`CIRCUIT_GEN_CS_PYTHON`), and verifies every export against the circuit.
