"""
Tests for single-shot RL: prompts, code extraction and the checker-based reward.
"""

import json
import re

from circuit_gen import procgen, rl

from conftest import needs_libs, needs_skidl


def test_extract_code_takes_the_last_python_block():
    text = "Plan.\n```python\nx = 1\n```\nFixed:\n```python\nvcc = Net('VCC')\n```\n"
    assert rl.extract_code(text).strip() == "vcc = Net('VCC')"
    assert rl.extract_code("no code here") is None
    assert "Part(" in rl.extract_code('r = Part("Device", "R")')


def test_board_tests_from_require_lines_and_benchmark_tests():
    board = procgen.generate(1, seed=3, families=["led_resistor"])[0]
    tests = rl.board_tests(board)
    assert tests and tests[0]["measure"] == "i(VCC)" and len(tests[0]["expect"]) == 2
    bench = {"tests": [{"name": "t", "measure": "v(OUT)", "expect": [1, 2]}]}
    assert rl.board_tests(bench)[0]["expect"] == [1, 2]


@needs_libs
def test_parts_sheet_lists_pins_by_unit():
    sheet = rl.parts_sheet()
    tl072 = next(l for l in sheet.splitlines() if '"TL072"' in l)
    assert "unit 1:" in tl072 and "4:V-" in tl072 and "8:V+" in tl072
    assert "LM2596S-5" in sheet and "Parts:" in rl.system_prompt()


@needs_skidl
def test_reward_levels(tmp_path):
    board = procgen.generate(1, seed=3, families=["led_resistor"])[0]
    tests = rl.board_tests(board)
    code = "\n".join(l for l in board["code"].splitlines() if not l.startswith("require("))
    good = f"```python\n{code}\n```"
    assert rl.score(good, tests)[0] == 1.0
    wrong = re.sub(r'R\("[^"]+"\)', 'R("1M")', good, count=1)   # the LED resistor
    assert rl.score(wrong, tests)[0] == 0.4      # runs and passes checks, fails the test
    stray = good.replace("```python\n", '```python\nPart("Device", "R", value="1k")\n', 1)
    reward, details = rl.score(stray, tests)
    assert details["check_errors"] >= 1 and 0.8 < reward < 1.0   # partial structural credit
    assert rl.score("```python\nthis is not python\n```", tests)[0] == 0.0
    assert rl.score("I can't", tests)[0] == 0.0


@needs_libs
def test_build_dataset_splits_and_holds_out_the_benchmark(tmp_path):
    boards = procgen.generate(40, seed=1)
    path = tmp_path / "boards.jsonl"
    path.write_text("".join(json.dumps(b) + "\n" for b in boards))
    bench = tmp_path / "bench.json"
    bench.write_text(json.dumps([{"id": "b1", "description": "d", "tests": [
        {"name": "t", "measure": "v(OUT)", "expect": [1, 2]}]}]))
    counts = rl.build_dataset(path, tmp_path / "rl", eval_fraction=0.25, benchmark=bench)
    assert counts["train"] + counts["eval"] == 40 and counts["eval"] > 0 and counts["bench"] == 1
    row = json.loads(open(tmp_path / "rl" / "train.jsonl").readline())
    assert row["prompt"][0]["role"] == "system" and json.loads(row["tests"])
    train_ids = {json.loads(l)["id"] for l in open(tmp_path / "rl" / "train.jsonl")}
    eval_ids = {json.loads(l)["id"] for l in open(tmp_path / "rl" / "eval.jsonl")}
    assert not train_ids & eval_ids
    sft = [json.loads(l) for l in open(tmp_path / "rl" / "sft.jsonl")]
    assert {r["id"] for r in sft} == train_ids
    assert sft[0]["completion"][0]["content"].startswith("```python")
    assert "require(" not in sft[0]["completion"][0]["content"]


@needs_libs
def test_build_dataset_holds_out_whole_families(tmp_path):
    boards = procgen.generate(30, seed=2, families=["led_resistor", "voltage_divider", "rc_filter"])
    path = tmp_path / "boards.jsonl"
    path.write_text("".join(json.dumps(b) + "\n" for b in boards))
    counts = rl.build_dataset(path, tmp_path / "rl", holdout_families=["rc_filter"])
    held = [json.loads(l) for l in open(tmp_path / "rl" / "heldout.jsonl")]
    assert counts["heldout"] == 10 and {r["family"] for r in held} == {"rc_filter"}
    for name in ("train", "eval", "sft"):
        rows = [json.loads(l) for l in open(tmp_path / "rl" / f"{name}.jsonl")]
        assert not any(r["id"].startswith("rc_filter") for r in rows)
