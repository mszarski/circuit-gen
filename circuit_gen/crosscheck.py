"""
Cross-check accepted designs against the other samples' frozen specs.

Each eval sample writes its own spec and is accepted when its design passes
it, so a spec that misreads the description lets a wrong design through (a
false accept). Independent samples rarely misread the same way. Here every
accepted design is re-simulated against every other sample's requirements for
the same board, and a requirement counts as *consensus* when at least
`support` of the other accepted designs pass it. A design that fails a
consensus requirement is flagged.

Offline and free (ngspice only), on finished `eval` directories:

    python -m circuit_gen crosscheck runs/eval_a [runs/eval_b ...] [--support 0.6]

Samples are pooled per board across the directories given.
"""

import json
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import spice


def load_samples(run_dirs):
    """Eval samples that produced a circuit with frozen requirements."""
    samples = []
    for run in run_dirs:
        for result in sorted(Path(run).glob("*/s*/result.json")):
            d = result.parent
            circuit_path = d / "circuit.json"
            if not circuit_path.exists():
                continue
            circuit = json.loads(circuit_path.read_text())
            if not circuit.get("requirements"):
                continue
            r = json.loads(result.read_text())
            cls = ("false_accept" if r.get("false_accept")
                   else "pass" if r.get("success") else "fail")
            samples.append({"dir": str(d), "board": r["id"], "cls": cls, "circuit": circuit})
    return samples


def _failed(job):
    circuit, tests, corners = job
    try:
        report = spice.simulate(circuit, tests, corners=corners)
        return sorted({t.name for t in report.results if not t.passed})
    except Exception as e:                        # a design another spec cannot even run
        return [f"error: {e}"[:200]]


def run_checks(samples, jobs=4, corners=False):
    """{(design dir, spec dir): failed requirement names} for each accepted design."""
    work, keys = [], []
    for s in samples:
        if s["cls"] == "fail":
            continue
        for o in samples:
            if o["board"] == s["board"]:
                work.append((s["circuit"], spice.requirement_tests(o["circuit"]), corners))
                keys.append((s["dir"], o["dir"]))
    with ProcessPoolExecutor(jobs) as ex:
        return dict(zip(keys, ex.map(_failed, work, chunksize=4)))


def flag(samples, fails, support=0.6):
    """{design dir: [reasons]} for accepted designs that fail a consensus requirement."""
    by_board = defaultdict(list)
    for s in samples:
        by_board[s["board"]].append(s)
    names = {s["dir"]: {t["name"] for t in spice.requirement_tests(s["circuit"])} for s in samples}
    flagged = {}
    for board, group in by_board.items():
        accepted = [s["dir"] for s in group if s["cls"] != "fail"]
        for design in accepted:
            reasons = []
            for spec in (s["dir"] for s in group):
                if spec == design:
                    continue
                others = [d for d in accepted if d not in (design, spec) and (d, spec) in fails]
                if not others:
                    continue
                for name in sorted(names[spec] & set(fails.get((design, spec), []))):
                    agree = sum(name not in fails[(d, spec)] for d in others) / len(others)
                    if agree >= support:
                        reasons.append(f"{name} (from {Path(spec).parent.name}/{Path(spec).name}, "
                                       f"{agree:.0%} of other designs pass it)")
            if reasons:
                flagged[design] = reasons
    return flagged


def report(run_dirs, support=0.6, jobs=4, corners=False):
    samples = load_samples(run_dirs)
    fails = run_checks(samples, jobs=jobs, corners=corners)
    flagged = flag(samples, fails, support)
    accepted = [s for s in samples if s["cls"] != "fail"]
    n_fa = sum(s["cls"] == "false_accept" for s in accepted)
    n_ok = len(accepted) - n_fa
    caught = sum(s["cls"] == "false_accept" and s["dir"] in flagged for s in accepted)
    extra = sum(s["cls"] == "pass" and s["dir"] in flagged for s in accepted)
    lines = [f"{len(accepted)} accepted designs ({n_fa} false accepts), support >= {support:.0%}",
             f"false accepts flagged: {caught}/{n_fa}",
             f"good designs flagged:  {extra}/{n_ok}" + (f" ({extra / n_ok:.0%})" if n_ok else ""),
             ""]
    for s in accepted:
        if s["dir"] in flagged:
            tag = "FALSE ACCEPT" if s["cls"] == "false_accept" else "good"
            lines.append(f"{tag:12s} {s['dir']}: {flagged[s['dir']][0]}"
                         + (f" (+{len(flagged[s['dir']]) - 1} more)" if len(flagged[s['dir']]) > 1 else ""))
    return "\n".join(lines)
