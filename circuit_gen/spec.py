"""
Spec-first design: write the requirements before the circuit.

A separate model call sees only the description and writes require(...)
lines. They are parsed without executing anything (only literal require
calls are accepted) and frozen: the design phase must meet them unchanged,
so the designer cannot loosen a requirement its circuit fails.
"""

import ast
import json
import re

SPEC_PROMPT = """\
You write acceptance tests for a circuit board before anyone designs it. \
Given the description, write require(...) lines that a correct board must \
pass and an incorrect one would fail. You do not design the circuit.

Each line is checked in ngspice at nominal values and across component \
tolerances (resistors 5%, capacitors 10%), so set ranges the description \
implies, not tighter: "about 100" allows roughly +/-25%, while "3.3 V +/-5%" \
means exactly that.

    require(measure, low, high, sources={...}, loads={...}, analysis="op", freq=1000, name="...")

- measure: v(NET), i(NET) (magnitude of the current through the voltage \
source on NET, always >= 0), gain(OUT,IN) with \
analysis="ac" and an "ac" source on IN. With analysis="tran" and duration= \
in seconds: freq(NET), an oscillation frequency in Hz (run at least four \
cycles); avg(NET), ripple(NET) (peak to peak) and duty(NET) (fraction of the \
time high, 0 to 1) of a voltage, and iavg(NET), the average current magnitude \
in the source on NET, all over the last quarter of the run after start-up (a \
switching regulator needs a few milliseconds, e.g. duration=0.008). In AC analysis every measure is a \
magnitude: gain() is never negative, so an inverting gain of -10 is 9 to 11.
- sources: voltage sources from nets to ground, e.g. {"VCC": 5, "IN": "ac"}. \
A net without a source is left floating (useful for "input left open" cases).
- loads: resistors from a net to ground, e.g. {"OUT": 10000}, or between \
nets, e.g. {"LOAD_NEG": {"to": "V12", "r": 12}}.
- Use only the net names the description gives; internal nets do not exist yet.
- Simulation uses generic models of passives, diodes, LEDs, op-amps, fixed \
linear regulators, fixed-output LM2596 buck regulators, MOSFETs, BJTs, 555 \
timers and CMOS logic gates (including Schmitt-trigger inputs). \
Behaviour that depends on an unmodelled part (e.g. an MCU) cannot be \
checked; skip it.
- Boards built around an IC without a model (MCUs, interface transceivers \
such as RS-485, CAN or USB PHYs, sensors, memories): specify only the \
circuitry around it (pull-ups, termination, dividers, LED currents, supply \
filtering), never the IC's own function, or the designer will be pushed to \
replace the IC with discrete parts.
- All values are in SI base units: amperes, volts, ohms, hertz, seconds \
(10 mA is 0.01, 4.7 kHz is 4700).

Make every range discriminating: a circuit that gets the property wrong must \
fall outside it. Derive each range from the stated value and component \
tolerances, not from limits that almost any circuit meets (such as "anywhere \
between 1.5 V and the supply" for a node with a definite role). Check a \
response wherever the description says it holds (across a stated band, not \
at one point), and check every named quality (a filter type, Q, "unity", a \
current that a part needs to work) with a number.

Make the set consistent:
- Every requirement must be satisfiable together with all the others by one \
circuit; two requirements with identical sources and loads cannot expect \
different values.
- A source fixes its net's voltage, so do not also require v() of that net, \
and remember that a source overrides whatever the circuit would do there.
- A DC (op) analysis shows one static state. For oscillators and blinkers, \
require freq(NET) with analysis="tran", not high and low v() levels. For \
switching regulators every requirement must be transient: avg() and ripple() \
of the output and iavg() of the input, with duration=0.008; DC analysis \
cannot show a switcher regulating.
- Loads are resistances in ohms: a 10 mA load at 5 V is 500.

Cover:
- every number the description gives, at each end of any stated range of \
conditions (e.g. both supply voltages of "9 V to 15 V");
- what must not happen: blocking, off states, an input left open;
- qualitative claims turned into numbers: "second-order", "small drop", \
"good rejection", "able to drive a 10k load";
- outputs under the load the description implies.

Reply with only the require(...) lines, one per call, in a ```python block."""

FIELDS = ("measure", "low", "high")
OPTIONAL = {"sources": {}, "loads": {}, "analysis": "op", "freq": 1000, "name": None,
            "duration": 5.0}


class SpecError(ValueError):
    pass


_UNQUOTED = re.compile(r"require\(\s*(v|i|gain|freq|avg|ripple|duty|iavg)\(\s*([^)]*?)\s*\)")


_MEASURE = re.compile(r"(v|i|freq|avg|ripple|duty|iavg)\([^,()]+\)|gain\([^,()]+,[^,()]+\)")


def _quote_measures(code):
    """Quote measures models write bare, e.g. require(v(+5V), ...) or require(freq('OUT'), ...)."""
    def fix(m):
        inner = re.sub(r"[\s'\"]", "", m.group(2))
        return f'require("{m.group(1)}({inner})"'
    return _UNQUOTED.sub(fix, code)


def _nets_in(req):
    nets = set(req.get("sources") or {})
    for net, load in (req.get("loads") or {}).items():
        nets.add(net)
        if isinstance(load, dict) and load.get("to"):
            nets.add(load["to"])
    m = re.match(r"\s*\w+\((.*)\)\s*$", str(req["measure"]))
    if m:
        nets.update(n.strip() for n in m.group(1).split(",") if n.strip())
    return nets


def named_nets(description):
    """Nets listed in the description's "Name the ... nets A, B and C." sentences, or None."""
    sentences = re.findall(r"[Nn]ame the[^.]*?(?:\.(?=\s|$)|$)", description)
    if not sentences:
        return None
    nets = set()
    for sentence in sentences:
        for token in re.findall(r"(?<![A-Za-z0-9_+-])[-+]?[A-Z0-9_][A-Za-z0-9_+/-]*", sentence):
            if re.search(r"[A-Z+]", token):
                nets.add(token.rstrip(",.;"))
    return nets


def check_nets(reqs, description):
    """Requirements may only use nets the description names (plus ground)."""
    named = named_nets(description)
    for r in reqs:
        for net in _nets_in(r):
            if net.upper() in ("GND", "0"):
                continue
            if named is not None:
                ok = net in named
            else:
                ok = re.search(rf"(?<![A-Za-z0-9_+-]){re.escape(net)}(?![A-Za-z0-9_])", description)
            if not ok:
                raise SpecError(f"net {net!r} is not named in the description; requirements may "
                                f"only use the nets the description names: {r.get('name') or r['measure']}")


def parse_requirements(text):
    """Parse require(...) calls from model output. Raises SpecError on anything else."""
    if "```" in text:
        blocks = text.split("```")
        code = max((b for b in blocks[1::2]), key=lambda b: b.count("require("), default="")
        code = code.split("\n", 1)[1] if code.startswith(("python", "py")) else code
    else:
        code = text
    code = _quote_measures(code)
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SpecError(f"requirements are not valid Python: {e}") from e
    reqs = []
    for node in tree.body:
        call = node.value if isinstance(node, ast.Expr) else None
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "require"):
            raise SpecError(f"only require(...) calls are allowed, got: {ast.unparse(node)[:80]}")
        if call.args and not isinstance(call.args[0], ast.Constant):
            raise SpecError("the measure must be a quoted string naming its nets, such as "
                            f"'v(OUT)' or 'freq(OUT)': {ast.unparse(call)[:80]}")
        try:
            args = [ast.literal_eval(a) for a in call.args]
            kwargs = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
        except ValueError as e:
            raise SpecError(f"require arguments must be literals: {ast.unparse(call)[:80]}") from e
        req = dict(OPTIONAL)
        req.update(zip(FIELDS, args))
        req.update(kwargs)
        missing = [f for f in FIELDS if f not in req]
        if missing:
            raise SpecError(f"require(...) missing {', '.join(missing)}: {ast.unparse(call)[:80]}")
        if not _MEASURE.fullmatch(str(req["measure"]).replace(" ", "")):
            raise SpecError("the measure must be v(), i(), gain(OUT,IN), or a transient "
                            "freq(), avg(), ripple(), duty() or iavg(): "
                            f"{ast.unparse(call)[:80]}")
        if float(req["low"]) > float(req["high"]):
            raise SpecError(f"low above high in: {ast.unparse(call)[:80]}")
        if float(req["low"]) == float(req["high"]):
            raise SpecError("a range of a single value cannot be met across component "
                            "tolerances; give the band the description allows (3.3 V +/-5% is "
                            f"3.135 to 3.465, 'off' is 0 to 0.1): {ast.unparse(call)[:80]}")
        for net, load in (req.get("loads") or {}).items():
            ohms = load.get("r") if isinstance(load, dict) else load
            if not isinstance(ohms, (int, float)) or ohms < 1:
                raise SpecError(f"loads are resistances in ohms, got {load!r} on {net}; for a "
                                f"10 mA load at 5 V use 500: {ast.unparse(call)[:80]}")
        if str(req["measure"]).startswith("i(") and float(req["high"]) < 0:
            raise SpecError("i() is a magnitude and never negative: "
                            f"{ast.unparse(call)[:80]}")
        if str(req["measure"]).startswith("gain(") and float(req["high"]) < 0:
            raise SpecError("gain() is a magnitude and never negative; for an inverting "
                            f"gain of -10 require 9 to 11: {ast.unparse(call)[:80]}")
        reqs.append(req)
    if not reqs:
        raise SpecError("no require(...) lines found")
    return reqs


def to_code(reqs):
    """Render requirements as require(...) lines to append to circuit code."""
    lines = ["", "# ---- frozen requirements (written before the design) ----"]
    for r in reqs:
        parts = [repr(r["measure"]), repr(float(r["low"])), repr(float(r["high"]))]
        for key in ("sources", "loads"):
            if r.get(key):
                parts.append(f"{key}={r[key]!r}")
        if r.get("analysis", "op") == "ac":
            parts += [f"analysis='ac'", f"freq={float(r.get('freq', 1000))!r}"]
        elif r.get("analysis") == "tran":
            parts += [f"analysis='tran'", f"duration={float(r.get('duration', 5.0))!r}"]
        if r.get("name"):
            parts.append(f"name={r['name']!r}")
        lines.append(f"require({', '.join(parts)})")
    return "\n".join(lines) + "\n"


def write_spec(description, backend, workdir, attempts=3):
    """Ask the model for requirements only. Returns (requirements, code) or raises SpecError."""
    backend.start(SPEC_PROMPT, f"Board description:\n\n{description}", [])
    last_error = None
    for _ in range(attempts):
        turn = backend.step()
        with open(workdir / "spec_transcript.jsonl", "a") as f:
            f.write(json.dumps({"stop": turn.stop, **turn.record}, default=str) + "\n")
        if turn.stop in ("refusal", "max_tokens"):
            last_error = SpecError(f"spec turn ended with {turn.stop}")
            continue
        try:
            reqs = parse_requirements(turn.text)
            check_nets(reqs, description)
        except SpecError as e:
            last_error = e
            backend.add_user_message(f"{e}. Reply with only require(...) lines.")
            continue
        code = to_code(reqs)
        (workdir / "spec.py").write_text(code)
        return reqs, code
    raise last_error


REVISE_NOTE = """The designer could not meet your requirements and objected:

Requirement: {requirement}
Objection: {reason}

Current requirements:
{spec}

If the objection is right, reply with the complete corrected list of \
require(...) lines. If it is wrong, reply with the same list unchanged. Do \
not weaken a requirement the description actually demands."""


def revise_spec(description, backend, workdir, spec_code, requirement, reason, attempts=3):
    """Let the spec writer answer a designer's objection. Returns (requirements, code)."""
    note = REVISE_NOTE.format(requirement=requirement, reason=reason, spec=spec_code.strip())
    return _respec(description, backend, workdir, note, "spec_revised.py", attempts)


STRESS_NOTE = """A design now meets all of your requirements. To test them, each resistor, \
capacitor and inductor in it was scaled by 2 and by 0.5, one at a time. These \
changes left every requirement passing:

{findings}

Current requirements:
{spec}

If any of these parts sets behaviour the description asks for (a gain, a \
frequency response, a threshold, a current), the requirements are too weak \
there: reply with the complete list, adding or tightening requirements so such \
an error would fail. Parts that rightly do not matter (decoupling, pull-ups, \
bias resistors with wide margins) need nothing. Do not demand more than the \
description does. If nothing needs to change, reply with the same list."""


REVIEW_NOTE = """Review these requirements against the description before any design is \
made:

{spec}

1. List every property the description states: each number, each named quality \
(a filter type, "unity", "flat", "rail to rail", "off when floating", ...) and \
what each named net is.
2. For each, name the requirement that checks it, and say whether its range \
would reject a plausible circuit that gets that property wrong: think of a \
specific wrong circuit (a wrong resistor, a wrong node, a different Q, a \
response that is right at one frequency but not across the band) and say \
what the requirement would measure for it. A property with no requirement, \
or a range wide enough to accept the wrong circuit, is a gap.
3. Reply with the complete corrected list of require(...) lines in a python \
block: the same list if there are no gaps. Keep ranges as wide as the \
description implies; do not demand more than it says."""


def review_spec(description, backend, workdir, spec_code, attempts=3):
    """Ask the spec writer to check its requirements cover everything the description states."""
    note = REVIEW_NOTE.format(spec=spec_code.strip())
    return _respec(description, backend, workdir, note, "spec_reviewed.py", attempts)


def strengthen_spec(description, backend, workdir, spec_code, findings, attempts=3):
    """Ask the spec writer to cover component changes its requirements missed."""
    note = STRESS_NOTE.format(findings="\n".join(f"- {f}" for f in findings),
                              spec=spec_code.strip())
    return _respec(description, backend, workdir, note, "spec_strengthened.py", attempts)


def _respec(description, backend, workdir, note, filename, attempts):
    backend.start(SPEC_PROMPT, f"Board description:\n\n{description}\n\n" + note, [])
    last_error = None
    for _ in range(attempts):
        turn = backend.step()
        with open(workdir / "spec_transcript.jsonl", "a") as f:
            f.write(json.dumps({"stop": turn.stop, "revision": filename, **turn.record},
                               default=str) + "\n")
        if turn.stop in ("refusal", "max_tokens"):
            last_error = SpecError(f"spec revision ended with {turn.stop}")
            continue
        try:
            reqs = parse_requirements(turn.text)
            check_nets(reqs, description)
        except SpecError as e:
            last_error = e
            backend.add_user_message(f"{e}. Reply with only require(...) lines.")
            continue
        code = to_code(reqs)
        (workdir / filename).write_text(code)
        return reqs, code
    raise last_error


# ----------------------------------------------------------------------------
# Deterministic coverage: numbers the description states that no requirement checks
# ----------------------------------------------------------------------------

_PREFIX = {"": 1, "k": 1e3, "M": 1e6, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "n": 1e-9}
_QTY = re.compile(r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(k|M|m|u|µ)?(Hz|V|A)\b")
_GAIN = re.compile(r"gain (?:of )?(?:about |around |roughly |approximately )?(-?\d+(?:\.\d+)?)")


def stated_quantities(description):
    """(kind, value, text) for each frequency, voltage, current and gain the description states.

    Tolerances ("within 0.1 V", "+/-5 V") are skipped: they bound a property, they
    are not one.
    """
    out = []
    for m in _QTY.finditer(description):
        if re.search(r"(within|±)\s*$", description[max(0, m.start() - 8):m.start()]):
            continue
        value = float(m.group(1)) * _PREFIX[m.group(2) or ""]
        out.append((m.group(3), value, m.group(0)))
    for m in _GAIN.finditer(description):
        out.append(("gain", abs(float(m.group(1))), m.group(0)))
    if re.search(r"\bunity[- ]gain\b", description) and not any(
            k == "gain" and v == 1 for k, v, _ in out):
        out.append(("gain", 1.0, "unity gain"))
    return out


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _implied_gains(req):
    """Gains a DC v() test implies: its expected output over each input level or difference."""
    if not str(req["measure"]).replace(" ", "").startswith("v("):
        return []
    mid = abs(float(req["low"]) + float(req["high"])) / 2
    ins = [v for v in (req.get("sources") or {}).values() if _num(v) and 0 < abs(v) < 5]
    diffs = {abs(v) for v in ins} | {abs(a - b) for a in ins for b in ins if a != b}
    return [mid / d for d in diffs if d > 0]


def _covered(kind, value, reqs, volts=()):
    """Whether some requirement would reject a circuit that gets this quantity wrong."""
    value = abs(value)
    srcs = [abs(v) for r in reqs for v in (r.get("sources") or {}).values() if _num(v)]
    for r in reqs:
        m = str(r["measure"]).replace(" ", "")
        lo, hi = float(r["low"]), float(r["high"])
        inside = lo <= value * 1.02 and value * 0.98 <= hi
        if kind == "Hz":
            # An oscillation frequency in a range no wider than 3:1, or a response
            # measured near that frequency.
            if m.startswith("freq(") and inside and hi / max(lo, 1e-12) <= 3:
                return True
            if r.get("analysis") == "ac" and value / 1.6 <= float(r.get("freq", 1000)) <= value * 1.6:
                return True
        elif kind == "V":
            if m.startswith(("v(", "avg(")) and (inside or lo <= -value * 0.98 and -value * 1.02 <= hi):
                return True
            if m.startswith("ripple(") and value * 0.5 <= hi <= value * 1.5:
                return True
        elif kind == "A":
            if m.startswith(("i(", "iavg(")) and inside:
                return True
            for load in (r.get("loads") or {}).values():
                ohms = load.get("r") if isinstance(load, dict) else load
                if _num(ohms) and any(abs(v / ohms - value) <= 0.2 * value
                                      for v in srcs + list(volts)):
                    return True
        elif kind == "gain":
            if m.startswith("gain(") and inside and hi / max(lo, 1e-12) <= 2.5:
                return True
            if any(0.8 * value <= g <= 1.25 * value for g in _implied_gains(r)):
                return True
    if kind == "V" and any(abs(s - value) <= 0.02 * value for s in srcs):
        return True                        # a supply or input level the tests apply
    if kind == "V" and any(0.7 * value <= s < value for s in srcs) and any(
            value < s <= 1.3 * value for s in srcs):
        return True                        # a threshold, tested on both sides
    return False


def uncovered_quantities(description, reqs):
    """Numbers the description states that no requirement checks tightly enough.

    Deterministic, so it costs nothing: a frequency, voltage, current or gain with
    no requirement measuring it, or only one whose range is too wide to reject a
    circuit that gets it wrong (e.g. 1.7-2.35 Hz for "about 1 Hz").
    """
    qs = stated_quantities(description)
    volts = [abs(v) for k, v, _ in qs if k == "V"]
    seen, out = set(), []
    for kind, value, text in qs:
        if (kind, value) not in seen and not _covered(kind, value, reqs, volts):
            out.append(text)
        seen.add((kind, value))
    return out


COVERAGE_NOTE = """These values stated in the description have no requirement that \
would reject a circuit that gets them wrong: {missing}.

Current requirements:
{spec}

For each, add a requirement that measures it, or tighten the one that should: \
a frequency needs freq() in a range containing it (no wider than about 2:1) or \
an AC test near it; a voltage, current or gain needs a measurement whose range \
contains the stated value. If a value is only an operating condition (a supply \
or input level), apply it as a source in a test. Reply with the complete list \
of require(...) lines."""


def cover_quantities(description, backend, workdir, spec_code, missing, attempts=3):
    """Ask the spec writer to add requirements for stated values none checks."""
    note = COVERAGE_NOTE.format(missing=", ".join(missing), spec=spec_code.strip())
    return _respec(description, backend, workdir, note, "spec_covered.py", attempts)
