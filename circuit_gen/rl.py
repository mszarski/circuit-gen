"""
Reinforcement learning on single-shot circuit design.

The task: given a board description and a parts sheet (symbols, pin numbers
and names, footprints), write the complete SKiDL code in one reply, without
tools. The reward comes from the same checks the design loop uses, plus the
board's hidden requirement tests, so it can be computed for any completion:

    0.0   no code, or the code does not run
    0.2   the code runs (SKiDL builds a netlist)
    +0.2  ERC and the structural checks pass (connections, bias paths,
          decoupling, footprints that match their symbols)
    +0.6  times the fraction of hidden requirement tests that pass in ngspice

Prompts come from procgen boards (their verified require(...) lines are the
hidden tests, never shown to the model); the benchmark is held out and can be
scored the same way as an out-of-distribution check.
"""

import hashlib
import json
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

from . import library

# The house parts library for the single-shot task: what the procgen families
# use, plus a few common parts the benchmark boards need.
PARTS = [
    ("Device", "R", "Resistor_SMD:R_0805_2012Metric"),
    ("Device", "C", "Capacitor_SMD:C_0805_2012Metric"),
    ("Device", "C_Polarized", "Capacitor_SMD:CP_Elec_10x10.5"),
    ("Device", "L", "Inductor_SMD:L_12x12mm_H8mm"),
    ("Device", "LED", "LED_SMD:LED_0805_2012Metric"),
    ("Device", "D_Zener", "Diode_SMD:D_SOD-123"),
    ("Diode", "1N4148W", "Diode_SMD:D_SOD-123"),
    ("Diode", "SS34", "Diode_SMD:D_SMA"),
    ("Connector", "Conn_01x02_Pin", "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"),
    ("Connector", "Conn_01x03_Pin", "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"),
    ("Connector", "Conn_01x04_Pin", "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"),
    ("Amplifier_Operational", "TL072", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"),
    ("Amplifier_Operational", "LM358", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"),
    ("Amplifier_Operational", "MCP6002-xSN", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"),
    ("Amplifier_Operational", "LM324", "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"),
    ("Timer", "NE555D", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"),
    ("74xx", "74HC00", "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"),
    ("74xx", "74HC14", "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm"),
    ("Transistor_BJT", "BC847", "Package_TO_SOT_SMD:SOT-23"),
    ("Transistor_BJT", "MMBT3904", "Package_TO_SOT_SMD:SOT-23"),
    ("Transistor_FET", "AO3400A", "Package_TO_SOT_SMD:SOT-23"),
    ("Transistor_FET", "AO3401A", "Package_TO_SOT_SMD:SOT-23"),
    ("Transistor_FET", "IRLZ44N", "Package_TO_SOT_THT:TO-220-3_Vertical"),
    ("Regulator_Linear", "AMS1117-3.3", "Package_TO_SOT_SMD:SOT-223-3_TabPin2"),
    ("Regulator_Linear", "LM7805_TO220", "Package_TO_SOT_THT:TO-220-3_Vertical"),
    ("Regulator_Linear", "L7809", "Package_TO_SOT_THT:TO-220-3_Vertical"),
    ("Regulator_Linear", "L7812", "Package_TO_SOT_THT:TO-220-3_Vertical"),
    ("Regulator_Switching", "LM2596S-5", "Package_TO_SOT_SMD:TO-263-5_TabPin3"),
]


def parts_sheet():
    """One line per part: how to instantiate it, and its pins (number name, by unit)."""
    lines = []
    for lib, name, fp in PARTS:
        info = library.get_symbol(lib, name)
        pins = info["pins"] if isinstance(info, dict) else info.pins
        units = {}
        for p in pins:
            units.setdefault(p.get("unit", 0), []).append(f"{p['num']}:{p['name']}")
        pin_text = "; ".join((f"unit {u}: " if len(units) > 1 else "") + " ".join(v)
                             for u, v in sorted(units.items()))
        lines.append(f'Part("{lib}", "{name}", footprint="{fp}")  pins {pin_text}')
    return "\n".join(lines)


EXAMPLE = '''vcc, gnd, out = Net("VCC"), Net("GND"), Net("OUT")
vcc.drive = gnd.drive = POWER
R = lambda value, **kw: Part("Device", "R", value=value, footprint="Resistor_SMD:R_0805_2012Metric", **kw)
C = lambda value, **kw: Part("Device", "C", value=value, footprint="Capacitor_SMD:C_0805_2012Metric", **kw)
j = Part("Connector", "Conn_01x03_Pin", footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
j[1, 2, 3] += vcc, out, gnd
# Divider: OUT = 5 V * 10k / (10k + 10k) = 2.5 V
vcc & R("10k", tolerance=0.01) & out & R("10k", tolerance=0.01) & gnd
out & C("100n") & gnd'''

SYSTEM = f"""You are an electronics design engineer. Given a board description, write \
SKiDL (Python) code for the complete circuit in one ```python block: parts \
with footprints, and nets with exactly the names the description gives. \
Part, Net and POWER are already imported. Use only the parts below, with the \
pin numbers listed (pin names can be used where they are unique). Connect \
every pin of every unit you use; give an unused op-amp section a follower \
connection to a fixed voltage, and tie unused logic inputs to a supply. \
Decouple each IC's supply with 100n. Set tolerance= (e.g. 0.01) on parts \
whose value sets a stated number. Put the key calculations in comments.

Parts:
{{parts}}

Example (a 2.5 V divider from 5 V):
```python
{EXAMPLE}
```"""


def system_prompt():
    return SYSTEM.format(parts=parts_sheet())


def extract_code(text):
    """The last ```python block of a reply (or the reply itself if it has no block)."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S)
    if blocks:
        return blocks[-1]
    return text if "Part(" in (text or "") else None


def board_tests(board):
    """Hidden tests from a board: its own require(...) lines (procgen) or its tests (benchmark)."""
    if board.get("tests"):
        return [{**t, "expect": list(t["expect"])} for t in board["tests"]]
    from .spec import parse_requirements
    lines = [l for l in board["code"].splitlines() if l.startswith("require(")]
    tests = []
    for r in parse_requirements("\n".join(lines)):
        tests.append({"name": r.get("name") or r["measure"], "measure": r["measure"],
                      "analysis": r.get("analysis", "op"), "freq": r.get("freq", 1000),
                      "duration": r.get("duration", 5.0), "sources": r.get("sources") or {},
                      "loads": r.get("loads") or {},
                      "expect": [float(r["low"]), float(r["high"])]})
    return tests


def score(completion, tests, workdir=None, corners=False):
    """Reward for one completion. Returns (reward, details)."""
    from .checks import check_circuit
    from .runner import run_circuit_code
    from .spice import simulate
    details = {"code": False, "ran": False, "checks": False, "tests_passed": 0,
               "tests": len(tests)}
    code = extract_code(completion)
    if not code:
        return 0.0, details
    details["code"] = True
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        run = run_circuit_code(code, Path(tmp), timeout=60)
    if not run.ok:
        details["error"] = (run.error or "")[:300]
        return 0.0, details
    details["ran"] = True
    reward = 0.2
    report = check_circuit(run.circuit, requirements=False)
    if report.ok and not run.erc_errors:
        details["checks"] = True
        reward += 0.2
    if tests:
        sim = simulate(run.circuit, tests, corners=corners)
        passed = sum(r.passed for r in sim.results)
        details["tests_passed"] = passed
        details["failed"] = [r.describe() for r in sim.results if not r.passed][:4]
        reward += 0.6 * passed / len(tests)
    return round(reward, 4), details


def _score_job(args):
    completion, tests, corners = args
    try:
        return score(completion, tests, corners=corners)
    except Exception as e:                      # a crash in scoring is the completion's fault
        return 0.0, {"error": f"{type(e).__name__}: {e}"[:300]}


def score_many(completions, tests_list, jobs=8, timeout=180, corners=False):
    """Score completions in parallel worker processes. Returns a list of (reward, details)."""
    work = [(c, t, corners) for c, t in zip(completions, tests_list)]
    out = [(0.0, {"error": "scoring timed out"})] * len(work)
    import multiprocessing
    # Spawned workers: forking a process that has initialised CUDA (a trainer) is unsafe.
    with ProcessPoolExecutor(max_workers=jobs, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(_score_job, w) for w in work]
        for i, f in enumerate(futures):
            try:
                out[i] = f.result(timeout=timeout)
            except (FutureTimeout, Exception) as e:
                out[i] = (0.0, {"error": f"{type(e).__name__}"})
    return out


def _split(board_id, eval_fraction):
    h = int(hashlib.sha256(board_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "eval" if h < eval_fraction else "train"


def build_dataset(boards_path, out_dir, eval_fraction=0.1, benchmark=None):
    """train.jsonl / eval.jsonl (procgen) and bench.jsonl (benchmark) of prompts with tests."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    system = system_prompt()
    (out_dir / "system_prompt.txt").write_text(system)
    rows = {"train": [], "eval": []}
    for line in open(boards_path):
        board = json.loads(line)
        tests = board_tests(board)
        if not tests:
            continue
        rows[_split(board["id"], eval_fraction)].append({
            "id": board["id"], "family": board.get("family"),
            "prompt": [{"role": "system", "content": system},
                       {"role": "user", "content": f"Design this board:\n\n{board['description']}"}],
            "tests": json.dumps(tests)})
    if benchmark:
        rows["bench"] = [{
            "id": b["id"], "family": b.get("tier", "standard"),
            "prompt": [{"role": "system", "content": system},
                       {"role": "user", "content": f"Design this board:\n\n{b['description']}"}],
            "tests": json.dumps(board_tests(b))} for b in json.loads(Path(benchmark).read_text())]
    for name, items in rows.items():
        with open(out_dir / f"{name}.jsonl", "w") as f:
            for r in items:
                f.write(json.dumps(r) + "\n")
    return {name: len(items) for name, items in rows.items()}


def evaluate(rows, complete, samples=1, jobs=8, corners=False):
    """Mean reward and pass rate of `complete(messages) -> text` over prompt rows."""
    completions, tests, ids = [], [], []
    for row in rows:
        for _ in range(samples):
            completions.append(complete(row["prompt"]))
            tests.append(json.loads(row["tests"]))
            ids.append(row["id"])
    scored = score_many(completions, tests, jobs=jobs, corners=corners)
    rewards = [r for r, _ in scored]
    full = [r >= 0.999 for r in rewards]
    return {"samples": len(rewards), "mean_reward": sum(rewards) / max(len(rewards), 1),
            "full_pass": sum(full) / max(len(full), 1),
            "ran": sum(d.get("ran", False) for _, d in scored) / max(len(scored), 1),
            "results": [{"id": i, "reward": r, **d} for i, (r, d) in zip(ids, scored)],
            "completions": completions}
