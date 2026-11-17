"""
Build a KiCad board from a circuit: load footprints, assign nets, place parts
and draw the board outline. Uses KiCad's `pcbnew` Python module.
"""

from dataclasses import dataclass
from pathlib import Path

import pcbnew

from .. import config
from ..checks import is_ground
from . import place as placer
from .quality import decoupling_pairs


@dataclass
class BoardRules:
    track_width: float = 0.25    # mm
    power_track_width: float = 0.5   # mm, for supply, ground and load-current nets
    power_via_diameter: float = 0.8  # mm
    power_via_drill: float = 0.4     # mm
    ground_pour: bool = True     # fill both copper layers with ground after routing
    pour_clearance: float = 0.3  # mm between the pour and other copper
    clearance: float = 0.2       # mm
    via_diameter: float = 0.6    # mm
    via_drill: float = 0.3       # mm
    hole_clearance: float = 0.15 # mm; some KiCad library footprints (USB-C) need < 0.25
    part_spacing: float = 1.0    # mm between part bounding boxes
    edge_margin: float = 2.0     # mm from parts to board edge


def _mm(v):
    return pcbnew.FromMM(v)


def load_footprint(fp_id):
    lib, name = fp_id.split(":", 1)
    path = Path(config.KICAD_FOOTPRINT_DIR) / f"{lib}.pretty"
    fp = pcbnew.FootprintLoad(str(path), name)
    if fp is None:
        raise KeyError(f"footprint {fp_id} not found")
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def _bbox(fp):
    """Footprint body plus its reference label.

    The value text is left out: it sits on the fabrication layer, is not
    printed, and would push small parts millimetres away from their neighbours.
    """
    box = fp.GetBoundingBox(False, False)
    box.Merge(fp.Reference().GetBoundingBox())
    return box


def _apply_rules(board, rules):
    ds = board.GetDesignSettings()
    nc = ds.m_NetSettings.m_DefaultNetClass
    nc.SetTrackWidth(_mm(rules.track_width))
    nc.SetClearance(_mm(rules.clearance))
    nc.SetViaDiameter(_mm(rules.via_diameter))
    nc.SetViaDrill(_mm(rules.via_drill))
    ds.m_TrackMinWidth = _mm(min(rules.track_width, 0.2))
    ds.m_MinClearance = _mm(min(rules.clearance, 0.2))
    ds.m_HoleClearance = _mm(rules.hole_clearance)


def _draw_outline(board, x0, y0, x1, y1):
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetStart(pcbnew.VECTOR2I(_mm(ax), _mm(ay)))
        seg.SetEnd(pcbnew.VECTOR2I(_mm(bx), _mm(by)))
        seg.SetWidth(_mm(0.1))
        board.Add(seg)


def build_board(circuit, rules=None, origin=(100.0, 100.0)):
    """Return (board, placement items) for a circuit dict from the runner."""
    rules = rules or BoardRules()
    board = pcbnew.BOARD()
    _apply_rules(board, rules)

    nets = {}
    for part in circuit["parts"]:
        for pin in part["pins"]:
            if pin["net"] and pin["net"] not in nets:
                net = pcbnew.NETINFO_ITEM(board, pin["net"])
                board.Add(net)
                nets[pin["net"]] = net

    footprints, items = {}, []
    for part in circuit["parts"]:
        fp = load_footprint(part["footprint"])
        fp.SetReference(part["ref"])
        fp.SetValue(part["value"])
        pin_nets = {p["num"]: p["net"] for p in part["pins"]}
        for pad in fp.Pads():
            net = pin_nets.get(pad.GetNumber())
            if net:
                pad.SetNet(nets[net])
        board.Add(fp)
        footprints[part["ref"]] = fp

        # The reference label counts, so silkscreen labels don't overlap.
        bb = _bbox(fp)
        items.append(placer.Item(
            ref=part["ref"],
            width=pcbnew.ToMM(bb.GetWidth()),
            height=pcbnew.ToMM(bb.GetHeight()),
            nets={n for n in pin_nets.values() if n},
            is_connector=part["ref"].startswith("J"),
        ))

    # Decoupling capacitors are anchored to the IC supply pin they serve.
    by_ref = {it.ref: it for it in items}
    for cap, ic, pin in decoupling_pairs(circuit):
        fp = footprints[ic]
        pad = next((p for p in fp.Pads() if p.GetNumber() == pin), None)
        if pad is None or cap not in by_ref:
            continue
        off = pad.GetPosition() - _bbox(fp).GetCenter()
        # Aim the capacitor's own pad on that net at the pin, not its centre.
        cfp = footprints[cap]
        cpad = next((p for p in cfp.Pads() if p.GetNetname() == pad.GetNetname()), None)
        if cpad is not None:
            off = off - (cpad.GetPosition() - _bbox(cfp).GetCenter())
        by_ref[cap].anchor = (ic, pcbnew.ToMM(off.x), pcbnew.ToMM(off.y))

    ground = {n for n in nets if is_ground(n)}
    placer.place(items, clearance=rules.part_spacing, ignore_nets=ground)

    ox, oy = origin
    for it in items:
        fp = footprints[it.ref]
        # Footprint origins are often not their centre (e.g. pin 1 of a header).
        bb = _bbox(fp)
        off = bb.GetCenter() - fp.GetPosition()
        fp.SetPosition(pcbnew.VECTOR2I(_mm(ox + it.x) - off.x, _mm(oy + it.y) - off.y))

    x0, y0, x1, y1 = placer.bounding_box(items, margin=rules.edge_margin)
    _draw_outline(board, ox + x0, oy + y0, ox + x1, oy + y1)
    return board, items


def board_size_mm(items, rules=None):
    rules = rules or BoardRules()
    x0, y0, x1, y1 = placer.bounding_box(items, margin=rules.edge_margin)
    return x1 - x0, y1 - y0


def decoupling_distances(board, circuit):
    """[{cap, ic, pin, mm}]: pad-to-pad distance from each decoupling capacitor to its IC pin."""
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    out = []
    for cap, ic, pin in decoupling_pairs(circuit):
        if cap not in fps or ic not in fps:
            continue
        ic_pad = next((p for p in fps[ic].Pads() if p.GetNumber() == pin), None)
        if ic_pad is None:
            continue
        net = ic_pad.GetNetname()
        cap_pad = next((p for p in fps[cap].Pads() if p.GetNetname() == net), None)
        if cap_pad is None:
            continue
        d = ic_pad.GetPosition() - cap_pad.GetPosition()
        out.append({"cap": cap, "ic": ic, "pin": pin,
                    "mm": round(pcbnew.ToMM((d.x ** 2 + d.y ** 2) ** 0.5), 2)})
    return out


def add_ground_pour(board, rules=None):
    """Ground zones over the whole board on both copper layers (unfilled). Returns the net name.

    Added after routing: every ground pad is already connected by tracks, so
    the pour only adds copper (a low-impedance return path and shielding) and
    cannot leave anything unconnected if tracks cut it into islands.
    """
    rules = rules or BoardRules()
    counts = {}
    for pad in board.GetPads():
        if is_ground(pad.GetNetname()):
            counts[pad.GetNetname()] = counts.get(pad.GetNetname(), 0) + 1
    if not counts:
        return None
    name = max(counts, key=counts.get)
    box = board.GetBoardEdgesBoundingBox()
    corners = [(box.GetLeft(), box.GetTop()), (box.GetRight(), box.GetTop()),
               (box.GetRight(), box.GetBottom()), (box.GetLeft(), box.GetBottom())]
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(layer)
        zone.SetNet(board.FindNet(name))
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in corners:
            outline.Append(x, y)
        zone.SetLocalClearance(_mm(rules.pour_clearance))
        zone.SetMinThickness(_mm(0.25))
        zone.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        zone.SetThermalReliefGap(_mm(0.3))
        zone.SetThermalReliefSpokeWidth(_mm(0.4))
        zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        board.Add(zone)
    return name


def fill_zones(board_path):
    """Fill the zones of a saved board and save it again.

    Filling a board built in memory crashes pcbnew 7; a board loaded from a
    file fills correctly, so the pour is filled in a second step.
    """
    board = pcbnew.LoadBoard(str(board_path))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(board_path), board)

