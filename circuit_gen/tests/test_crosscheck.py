"""
Tests for flagging accepted designs that fail requirements the other samples agree on.
"""

from circuit_gen import crosscheck


def _sample(name, cls, reqs):
    return {"dir": f"runs/x/b/{name}", "board": "b", "cls": cls,
            "circuit": {"requirements": [{"measure": "v(OUT)", "min": 0, "max": 1, "name": n}
                                         for n in reqs]}}


def test_flags_a_design_failing_a_requirement_the_others_pass():
    samples = [_sample("s1", "pass", ["freq ~1 Hz"]), _sample("s2", "pass", ["freq ~1 Hz"]),
               _sample("s3", "false_accept", ["freq ~2 Hz"])]
    d = {s["dir"][-2:]: s["dir"] for s in samples}
    fails = {(a, b): [] for a in d.values() for b in d.values()}
    fails[(d["s3"], d["s1"])] = ["freq ~1 Hz"]          # s3's 2 Hz design fails s1's spec
    fails[(d["s1"], d["s3"])] = ["freq ~2 Hz"]          # the good designs fail s3's spec
    fails[(d["s2"], d["s3"])] = ["freq ~2 Hz"]
    flagged = crosscheck.flag(samples, fails, support=0.6)
    assert list(flagged) == [d["s3"]] and "freq ~1 Hz" in flagged[d["s3"]][0]


def test_requirement_without_consensus_flags_nothing():
    samples = [_sample(f"s{i}", "pass", ["dc level"]) for i in (1, 2, 3)]
    d = [s["dir"] for s in samples]
    fails = {(a, b): [] for a in d for b in d}
    fails[(d[0], d[2])] = ["dc level"]                  # only one other design passes it
    fails[(d[1], d[2])] = ["dc level"]
    assert crosscheck.flag(samples, fails, support=0.6) == {}
