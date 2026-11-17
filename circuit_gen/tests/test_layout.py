"""
Tests for placement, session import and the full build pipeline.
"""

from pathlib import Path

import pytest

from circuit_gen.layout.place import Item, place, bounding_box, wirelength, net_weights

from conftest import needs_pcbnew, needs_toolchain

EXAMPLES = Path(__file__).parent.parent / "examples"


def _overlap(a, b):
    a, b = a.box(), b.box()
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class TestPlacement:
    def _items(self):
        return [
            Item("U1", 6, 5, {"VCC", "GND", "A", "B", "C"}),
            Item("R1", 3, 1.5, {"VCC", "A"}),
            Item("R2", 3, 1.5, {"A", "B"}),
            Item("C1", 3, 1.5, {"B", "GND"}),
            Item("C2", 3, 1.5, {"VCC", "GND"}),
            Item("D1", 3, 1.5, {"C", "GND"}),
            Item("J1", 3, 6, {"VCC", "GND"}, is_connector=True),
        ]

    def test_no_overlaps(self):
        items = place(self._items(), clearance=1.0, ignore_nets={"GND"})
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                assert not _overlap(a, b), f"{a.ref} overlaps {b.ref}"

    def test_connector_outside_core(self):
        items = place(self._items(), clearance=1.0, ignore_nets={"GND"})
        core = [it for it in items if not it.is_connector]
        x0, y0, x1, y1 = bounding_box(core)
        j = next(it for it in items if it.ref == "J1")
        jx0, jy0, jx1, jy1 = j.box()
        assert jx1 <= x0 or jx0 >= x1 or jy1 <= y0 or jy0 >= y1

    def test_beats_naive_row(self):
        items = place(self._items(), clearance=1.0)
        weights = net_weights(items)
        placed = wirelength(items, weights)

        row = self._items()
        x = 0.0
        for it in row:
            it.x, it.y, it.placed = x + it.width / 2, 0.0, True
            x += it.width + 1.0
        assert placed < wirelength(row, weights)

    def test_single_part(self):
        items = place([Item("J1", 3, 6, {"A"}, is_connector=True)])
        assert items[0].placed


SES_TEMPLATE = """(session board.ses
  (routes
    (resolution um 10)
    (library_out
      (padstack "Via[0-1]_600:300_um"
        (shape (circle F.Cu 6000 0 0))
        (shape (circle B.Cu 6000 0 0))
      )
    )
    (network_out
      (net SIG
        (wire (path F.Cu 2500 1000000 -1000000 1010000 -1000000 1010000 -1005000))
        (via "Via[0-1]_600:300_um" 1010000 -1005000)
        (wire (path B.Cu 2500 1010000 -1005000 1020000 -1005000))
      )
    )
  )
)
"""


@needs_pcbnew
class TestSessionImport:
    def test_tracks_and_vias(self, tmp_path):
        import pcbnew
        from circuit_gen.layout.route import apply_session

        board = pcbnew.BOARD()
        net = pcbnew.NETINFO_ITEM(board, "SIG")
        board.Add(net)
        ses = tmp_path / "board.ses"
        ses.write_text(SES_TEMPLATE)

        tracks, vias = apply_session(board, ses)
        assert (tracks, vias) == (3, 1)

        items = list(board.GetTracks())
        via = next(t for t in items if isinstance(t, pcbnew.PCB_VIA))
        assert pcbnew.ToMM(via.GetPosition().x) == pytest.approx(101.0)
        assert pcbnew.ToMM(via.GetPosition().y) == pytest.approx(100.5)   # Specctra y is flipped
        assert pcbnew.ToMM(via.GetWidth()) == pytest.approx(0.6)
        assert pcbnew.ToMM(via.GetDrillValue()) == pytest.approx(0.3)
        first = next(t for t in items if not isinstance(t, pcbnew.PCB_VIA))
        assert pcbnew.ToMM(first.GetWidth()) == pytest.approx(0.25)
        assert first.GetNetname() == "SIG"
        assert {board.GetLayerName(t.GetLayer()) for t in items if not isinstance(t, pcbnew.PCB_VIA)} == {"F.Cu", "B.Cu"}


@needs_toolchain
@pytest.mark.parametrize("example", ["ldo_3v3.py", "blinker_555.py"])
def test_examples_build_end_to_end(example, tmp_path):
    from circuit_gen.pipeline import build

    report = build((EXAMPLES / example).read_text(), tmp_path)
    failed = {k: v for k, v in report["stages"].items() if not v.get("ok")}
    assert report["success"], failed
    assert report["stages"]["routing"]["unrouted"] == 0
    assert report["stages"]["drc"]["errors"] == 0
    assert (tmp_path / "gerbers" / "board-F_Cu.gtl").exists()


@needs_pcbnew
def test_routing_retries_until_complete(tmp_path, monkeypatch):
    import pcbnew
    from circuit_gen.layout import route

    outcomes = [(True, 1, ""), (True, 0, ""), (True, 0, "")]
    calls, applied = [], []

    def fake_freerouting(dsn, ses, max_passes):
        calls.append(ses)
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(route, "run_freerouting", fake_freerouting)
    monkeypatch.setattr(route, "apply_session", lambda board, ses: applied.append(ses) or (5, 0))
    result = route.route_board(pcbnew.BOARD(), tmp_path)
    assert len(calls) == 2                      # stopped once fully routed
    assert result.unrouted == 0 and applied == [calls[1]]


class TestLayoutQuality:
    """Decoupling capacitors at their IC pin, wide power tracks, ground pour."""

    def test_anchored_part_goes_next_to_its_anchor_point(self):
        ic = Item("U1", 6, 5, nets={"VCC", "GND", "A", "B"})
        others = [Item(f"R{i}", 3, 2, nets={"A", "B"}) for i in range(6)]
        cap = Item("C1", 3, 2, nets={"VCC", "GND"}, anchor=("U1", 3.0, -2.0))
        place([ic, *others, cap], ignore_nets={"GND"})
        # As near the IC corner as the part boxes allow, and nothing overlaps.
        assert abs(cap.x - (ic.x + 3.0)) <= 3.5 and abs(cap.y - (ic.y - 2.0)) <= 3.5
        boxes = [it.box() for it in [ic, *others, cap]]
        assert not any(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                       for i, a in enumerate(boxes) for b in boxes[i + 1:])

    def test_decoupling_pairs_and_power_nets(self):
        from circuit_gen.layout.quality import decoupling_pairs, power_nets

        def part(ref, symbol, value, pins):
            return {"ref": ref, "symbol": symbol, "value": value,
                    "pins": [{"num": n, "name": name, "type": t, "net": net} for n, name, t, net in pins]}
        cap = lambda ref, value, net: part(ref, "Device:C", value, [("1", "~", "PASSIVE", net),
                                                                    ("2", "~", "PASSIVE", "GND")])
        circuit = {"parts": [
            part("U1", "Timer:NE555D", "NE555D", [("8", "VCC", "PWRIN", "VCC"), ("1", "GND", "PWRIN", "GND"),
                                                  ("3", "Q", "OUTPUT", "OUT")]),
            cap("C1", "10u", "VCC"), cap("C2", "100n", "VCC"), cap("C3", "10n", "CV"),
            part("L1", "Device:L", "33u", [("1", "1", "PASSIVE", "SW"), ("2", "2", "PASSIVE", "VOUT")]),
            part("R1", "Device:R", "10k", [("1", "~", "PASSIVE", "OUT"), ("2", "~", "PASSIVE", "SIG")]),
            part("R2", "Device:R", "0.05", [("1", "~", "PASSIVE", "SENSE"), ("2", "~", "PASSIVE", "GND")]),
        ]}
        assert decoupling_pairs(circuit) == [("C2", "U1", "8")]       # the small ceramic, not the bulk
        assert power_nets(circuit) == {"VCC", "GND", "SW", "VOUT", "SENSE"}

    def test_widen_power_nets_moves_them_to_their_own_class(self, tmp_path):
        from circuit_gen.layout.route import widen_power_nets
        dsn = tmp_path / "b.dsn"
        dsn.write_text('''(pcb x
  (library
    (padstack "Via[0-1]_600:300_um"
      (shape (circle F.Cu 600))
      (shape (circle B.Cu 600))
      (attach off)
    )
  )
  (network
    (class kicad_default "" +5V GND SIG "Net-(R1-Pad1)"
      (circuit
        (use_via Via[0-1]_600:300_um)
      )
      (rule
        (width 250)
        (clearance 200.1)
      )
    )
  )
)''')
        assert widen_power_nets(dsn, {"+5V", "GND"}, 0.5, 0.8, 0.4) == ["+5V", "GND"]
        text = dsn.read_text()
        default = text[text.index("(class kicad_default"):]
        power = text[text.index("(class power"):text.index("(class kicad_default")]
        assert "+5V" not in default and "SIG" in default and '"Net-(R1-Pad1)"' in default
        assert "(width 500)" in power and "Via[0-1]_800:400_um" in power
        assert '(padstack "Via[0-1]_800:400_um"' in text and "(circle F.Cu 800)" in text


@needs_toolchain
def test_built_board_has_wide_power_tracks_a_ground_pour_and_close_decoupling(tmp_path):
    import pcbnew
    from circuit_gen.pipeline import build

    report = build((EXAMPLES / "blinker_555.py").read_text(), tmp_path)
    assert report["success"], report["stages"]
    routing, placement = report["stages"]["routing"], report["stages"]["placement"]
    assert routing["ground_pour"] == "GND" and "GND" in routing["power_nets"]
    assert placement["decoupling_mm"] and max(placement["decoupling_mm"].values()) < 6
    board = pcbnew.LoadBoard(str(tmp_path / "board.kicad_pcb"))
    widths = {}
    for t in board.GetTracks():
        if t.GetClass() == "PCB_TRACK":
            widths.setdefault(t.GetNetname(), set()).add(pcbnew.ToMM(t.GetWidth()))
    assert all(w == {0.5} for net, w in widths.items() if net in routing["power_nets"])
    assert any(w == {0.25} for net, w in widths.items() if net not in routing["power_nets"])
    assert len(board.Zones()) == 2 and all(z.IsFilled() for z in board.Zones())
