"""
The benchmark's own consistency: every reference design passes its board's
checks and hidden tests, so a model failure means the model, not the spec.
"""

import json
from pathlib import Path

import pytest

from circuit_gen.checks import check_circuit
from circuit_gen.runner import run_circuit_code
from circuit_gen.spice import simulate

from conftest import needs_skidl

BENCH = Path(__file__).parent.parent / "benchmarks"
HARDER = BENCH / "harder"
CASES = {c["id"]: c for c in json.loads((BENCH / "boards.json").read_text())}
HARDER_CASES = {c["id"]: c for c in json.loads((HARDER / "boards.json").read_text())}
REFERENCES = sorted(p for p in (BENCH / "reference").glob("*.py") if not p.name.startswith("_"))
HARDER_REFERENCES = sorted((HARDER / "reference").glob("*.py"))


def test_boards_are_well_formed():
    assert not set(CASES) & set(HARDER_CASES)
    for case in [*CASES.values(), *HARDER_CASES.values()]:
        assert case["description"] and isinstance(case.get("tests", []), list)
        for t in case.get("tests", []):
            lo, hi = t["expect"]
            assert lo <= hi, case["id"]


def test_every_board_has_a_reference_and_two_tests():
    assert {p.stem for p in REFERENCES} == set(CASES)
    assert {p.stem for p in HARDER_REFERENCES} == set(HARDER_CASES)
    for case in [*CASES.values(), *HARDER_CASES.values()]:
        assert len(case.get("tests", [])) >= 2, case["id"]


@needs_skidl
@pytest.mark.parametrize("ref", REFERENCES + HARDER_REFERENCES,
                         ids=lambda p: p.stem)
def test_reference_passes(ref, tmp_path):
    helpers = (BENCH / "reference" / "_parts.py").read_text()
    run = run_circuit_code(helpers + "\n" + ref.read_text(), tmp_path)
    assert run.ok, run.error
    assert not run.erc_errors
    report = check_circuit(run.circuit)
    assert report.ok, report.summary()
    sim = simulate(run.circuit, {**CASES, **HARDER_CASES}[ref.stem]["tests"])
    assert sim.ok, sim.summary()


# Plausible wrong designs: each reference with one mistake. The hidden tests must
# reject every one, or they are too weak to tell a working board from a broken one.
MUTANTS = [
    ("reg_12v_5v", 'v5 & R("1k") & la', "v5 += la"),                     # LED without resistor
    ("usb_c_power", 'vbus & R("1k") & la', "vbus += la"),                 # LED without resistor
    ("mic_preamp", 'vcc & R("2.2k") & mic\n', ""),                        # no mic bias
    ("mic_preamp", 'mic & C("1u") & ninp', 'mic & C("1n") & ninp'),       # input corner too high
    ("rc_lowpass", 'R("1.6k", tolerance=0.01)', 'R("16k", tolerance=0.01)'),  # cutoff 10x low
    ("buffered_reference", "u[3] += div; u[2] += out; u[1] += out", "div += out"),  # unbuffered
    ("inverting_amp", 'inp & C("10u") & cin', 'inp & C("100n") & cin'),   # input corner 160 Hz
    ("i2c_sensor_breakout", 'vcc & R("4.7k") & sda\n', ""),               # missing pull-up
    ("led_resistor", 'R("330")', 'R("100")'),
    ("button_debounce", 'vcc & R("10k") & btn\n', ""),                    # no pull-up: OUT high released
    ("button_debounce", "u[1] += din; u[2] += out", "u[1] += din; u[2] += out; din += out"),  # output shorted to its input                             # LED overdriven
    ("instrumentation_amp", 'R("2.2k", tolerance=0.01)', 'R("22k", tolerance=0.01)'),  # gain 19
    ("instrumentation_amp", 'ni & R("100k", tolerance=0.001)', 'ni & R("110k", tolerance=0.001)'),  # CMRR
    ("mfb_bandpass", 'R("4.53k", tolerance=0.01)', 'R("45.3k", tolerance=0.01)'),  # centre moved
    ("window_comparator", "u[5] += inp; u[6] += vl", "u[5] += vl; u[6] += inp"),  # inputs swapped
    ("two_stage_audio_amp", "i1 & R(\"30k\") & o1", "i1 & R(\"3k\") & o1"),  # first stage gain 4
    ("nand_xor", "u[13] += n3", "u[13] += n2"),                             # miswired last gate
    ("bjt_current_mirror", 'R("11k", tolerance=0.01)', 'R("1.1k", tolerance=0.01)'),  # 10 mA
    ("high_side_switch", 'q["S"] += v12; q["D"] += load', 'q["D"] += v12; q["S"] += load'),  # body diode
    ("led_bar_3level", "(t1, t2, t3)", "(t3, t2, t1)"),                   # thresholds reversed
    ("buck_5v", 'value="220u"', 'value="4.7u"'),                         # output cap too small
    ("buck_5v", 'value="33u"', 'value="1u"'),                            # inductor far too small
    ("schmitt_rc_oscillator", 'R("11k", tolerance=0.01)', 'R("110k", tolerance=0.01)'),  # 100 Hz
    ("pwm_555", 'd = Part("Diode", "1N4148W", footprint="Diode_SMD:D_SOD-123")\nd["A"] += dis; d["K"] += thr\n', ""),  # no diode: duty > 50%
    ("rail_splitter", "u[3] += div; u[2] += vmid; u[1] += vmid", "div += vmid"),  # unbuffered
    ("audio_mixer_3ch", "    n & C(\"10u\") & c", "    n & C(\"10n\") & c"),  # inputs roll off below 1.6 kHz
    ("balanced_line_receiver", 'ni & R("10k", tolerance=0.001) & gnd', 'ni & R("11k", tolerance=0.001) & gnd'),  # CMRR
    ("balanced_line_receiver", 'm & C("2.2n") & out', 'm & C("22n") & out'),  # filter corner far too low
]


HARDER_MUTANTS = [
    ("butterworth_lp4", 'm2 & C("22n") & out', 'm2 & C("6.8n") & out'),   # stage 2 Q 0.72: not Butterworth
    ("butterworth_lp4", 'x1 & C("10n") & gnd', 'x1 & C("100n") & gnd'),   # stage 1 cutoff far too low
    ("current_sense", 'R("19.1k", tolerance=0.01)', 'R("9.1k", tolerance=0.01)'),  # 0.5 V/A
    ("dual_ldo_rails", 'd["A"] += vin; d["K"] += vp', 'vin += vp'),         # no reverse protection
    ("dual_ldo_rails", 'v5 & R("1.5k") & a5', 'v5 += a5'),                 # LED without resistor
    ("buck_ldo", 'r3["VI"] += v5', 'r3["VI"] += vin'),                     # 3.3 V linear from 24 V
    ("pwm_led_dimmer", 'dd["A"] += dis; dd["K"] += thr', 'dd["A"] += thr; dd["K"] += dis'),  # diode reversed: duty > 50%
    ("h_bridge", '"AO3401A"', '"AO3400A"'),                                # N-channel high side
    ("bridge_amp_5v", 'ni & R("100k", tolerance=0.001) & ref', 'ni & R("100k", tolerance=0.001) & gnd'),  # output not offset
    ("bridge_amp_5v", 'R("2.2k", tolerance=0.01)', 'R("22k", tolerance=0.01)'),  # gain ~19
    ("attiny_sensor_node", 'v3 & R("4.7k") & scl\n', ""),                 # missing SCL pull-up
    ("led_constant_current", 'vz & R("13.3k"', 'vin & R("13.3k"'),         # reference from the raw supply
    ("bandlimited_preamp", 'inp & C("100n") & h1', 'inp & C("10u") & h1'),  # high-pass corner too low
    ("bandlimited_preamp", 'x3 & C("4.7n") & ref', 'x3 & C("47n") & ref'),  # low-pass corner too low
]


def _mutant_rejected(cases, ref_dir, board, old, new, tmp_path):
    helpers = (BENCH / "reference" / "_parts.py").read_text()
    code = (ref_dir / f"{board}.py").read_text()
    assert old in code
    run = run_circuit_code(helpers + "\n" + code.replace(old, new), tmp_path)
    assert run.ok, run.error
    assert not simulate(run.circuit, cases[board]["tests"]).ok


@needs_skidl
@pytest.mark.parametrize("board,old,new", MUTANTS, ids=lambda x: x if "(" not in x else "")
def test_hidden_tests_reject_mutant(board, old, new, tmp_path):
    _mutant_rejected(CASES, BENCH / "reference", board, old, new, tmp_path)


@needs_skidl
@pytest.mark.parametrize("board,old,new", HARDER_MUTANTS, ids=lambda x: x if "(" not in x else "")
def test_harder_hidden_tests_reject_mutant(board, old, new, tmp_path):
    _mutant_rejected(HARDER_CASES, HARDER / "reference", board, old, new, tmp_path)
