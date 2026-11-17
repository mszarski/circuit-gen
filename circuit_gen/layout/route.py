"""
Route a board with FreeRouting and check it with KiCad's DRC.

The board is exported to Specctra DSN, routed headless by FreeRouting, and
the resulting session (SES) file is read back and applied as tracks and vias.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pcbnew

from .. import config, sexp

_RESOLUTION_TO_NM = {"um": 1000, "mm": 1_000_000, "mil": 25_400, "inch": 25_400_000}


@dataclass
class RouteResult:
    ok: bool
    unrouted: int | None = None
    tracks: int = 0
    vias: int = 0
    error: str | None = None


def run_freerouting(dsn_path, ses_path, max_passes=30, timeout=600):
    """Route `dsn_path` and write `ses_path`. Returns (ok, unrouted count, log tail)."""
    cmd = ["java", "-jar", str(config.FREEROUTING_JAR), "--gui.enabled=false",
           "-de", str(dsn_path), "-do", str(ses_path), "-mp", str(max_passes)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, None, f"FreeRouting timed out after {timeout}s"
    log = proc.stdout + proc.stderr
    unrouted = None
    m = re.search(r'"incomplete_count":\s*(\d+)', log)
    if m:
        unrouted = int(m.group(1))
    ok = proc.returncode == 0 and Path(ses_path).exists()
    return ok, unrouted, "\n".join(log.strip().splitlines()[-10:])


def _scale(routes):
    res = sexp.child(routes, "resolution")
    unit, per_unit = res[1], float(res[2])
    return _RESOLUTION_TO_NM[unit] / per_unit


def _via_drill_nm(padstack_name):
    # KiCad names via padstacks "Via[0-1]_<diameter>:<drill>_um".
    m = re.search(r"_(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)_(um|mm|mil)", padstack_name)
    if not m:
        return None
    return int(float(m.group(2)) * _RESOLUTION_TO_NM[m.group(3)])


def apply_session(board, ses_path):
    """Add the tracks and vias from a FreeRouting SES file to `board`."""
    session = sexp.parse(Path(ses_path).read_text())[0]
    routes = sexp.child(session, "routes")
    if routes is None:
        return 0, 0
    scale = _scale(routes)

    def pt(x, y):
        return pcbnew.VECTOR2I(int(round(float(x) * scale)), int(round(-float(y) * scale)))

    via_sizes = {}
    library_out = sexp.child(routes, "library_out") or []
    for ps in sexp.children(library_out, "padstack"):
        shape = sexp.child(sexp.child(ps, "shape"), "circle")
        via_sizes[ps[1]] = (int(float(shape[2]) * scale), _via_drill_nm(ps[1]))

    layer_ids = {board.GetLayerName(i): i for i in range(pcbnew.PCB_LAYER_ID_COUNT)}
    n_tracks = n_vias = 0
    for net_expr in sexp.children(sexp.child(routes, "network_out") or [], "net"):
        net = board.FindNet(net_expr[1])
        for wire in sexp.children(net_expr, "wire"):
            path = sexp.child(wire, "path")
            layer, width, coords = path[1], float(path[2]) * scale, path[3:]
            points = [pt(coords[i], coords[i + 1]) for i in range(0, len(coords) - 1, 2)]
            for a, b in zip(points, points[1:]):
                track = pcbnew.PCB_TRACK(board)
                track.SetStart(a)
                track.SetEnd(b)
                track.SetWidth(int(width))
                track.SetLayer(layer_ids[layer])
                if net:
                    track.SetNet(net)
                board.Add(track)
                n_tracks += 1
        for via_expr in sexp.children(net_expr, "via"):
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pt(via_expr[2], via_expr[3]))
            diameter, drill = via_sizes.get(via_expr[1], (None, None))
            ds = board.GetDesignSettings()
            via.SetWidth(diameter or ds.GetCurrentViaSize())
            via.SetDrill(drill or ds.GetCurrentViaDrill())
            via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            if net:
                via.SetNet(net)
            board.Add(via)
            n_vias += 1
    return n_tracks, n_vias


# Net names may be quoted and contain brackets, e.g. "Net-(R1-Pad1)".
_DSN_CLASS = re.compile(r'\(class kicad_default ""(?P<nets>(?:"[^"]*"|[^()"])*)\(circuit')


def widen_power_nets(dsn_path, nets, width_mm, via_diameter_mm, via_drill_mm, clearance_mm=0.2):
    """Move `nets` into their own class in a Specctra DSN file, with wider tracks and vias.

    KiCad 7's exporter puts every net in one class whatever the board's net
    classes say, so the class is written here. Returns the nets moved.
    """
    text = Path(dsn_path).read_text()
    m = _DSN_CLASS.search(text)
    if not m or not nets:
        return []
    tokens = re.findall(r'"[^"]*"|\S+', m.group("nets"))
    power = [t for t in tokens if t.strip('"') in nets]
    if not power:
        return []
    rest = [t for t in tokens if t not in power]
    via = f"Via[0-1]_{round(via_diameter_mm * 1000)}:{round(via_drill_mm * 1000)}_um"
    if via not in text:
        # Declare the larger via: a copy of the default padstack with the new diameter.
        default = re.search(r'\(padstack "?(Via\[0-1\]_(\d+):\d+_um)"?(?P<body>.*?)\n    \)\n', text, re.S)
        if default:
            body = default.group("body").replace(f"0 0 {default.group(2)}", "0 0 %d" % round(via_diameter_mm * 1000))
            body = re.sub(r"\(circle (\S+) [\d.]+", lambda c: f"(circle {c.group(1)} {round(via_diameter_mm * 1000)}", body)
            text = text.replace(default.group(0), default.group(0) + f'    (padstack "{via}"{body}\n    )\n', 1)
        else:
            via = None
    block = (f'(class power {" ".join(power)}\n      (circuit\n        (use_via {via})\n      )\n'
             if via else f'(class power {" ".join(power)}\n')
    block += (f"      (rule\n        (width {round(width_mm * 1000)})\n"
              f"        (clearance {clearance_mm * 1000 + 0.1:.1f})\n      )\n    )\n    ")
    start, end = _DSN_CLASS.search(text).span("nets")     # the padstack insert moved it
    text = text[:start] + (" " + " ".join(rest) + "\n      " if rest else "\n      ") + text[end:]
    m = _DSN_CLASS.search(text)
    text = text[:m.start()] + block + text[m.start():]
    Path(dsn_path).write_text(text)
    return [t.strip('"') for t in power]


def route_board(board, workdir, max_passes=30, attempts=3, power_nets=(), rules=None):
    """Export, route and re-import. Modifies `board` in place.

    `power_nets` are routed with the rules' power track width and via size.

    FreeRouting is multi-threaded and not deterministic: it occasionally leaves
    a connection unrouted that a second run completes. It is rerun up to
    `attempts` times while anything is unrouted, and the best session is kept.
    """
    workdir = Path(workdir)
    dsn = workdir / "board.dsn"
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)):
        return RouteResult(ok=False, error="DSN export failed")
    if power_nets and rules is not None:
        widen_power_nets(dsn, set(power_nets), rules.power_track_width,
                         rules.power_via_diameter, rules.power_via_drill, rules.clearance)
    best = None              # (unrouted, ses path)
    last_error = None
    for attempt in range(1, attempts + 1):
        ses = workdir / ("board.ses" if attempt == 1 else f"board.{attempt}.ses")
        ok, unrouted, log = run_freerouting(dsn, ses, max_passes=max_passes)
        if not ok:
            last_error = log
            continue
        score = unrouted if unrouted is not None else float("inf")
        if best is None or score < best[0]:
            best = (score, ses, unrouted)
        if unrouted == 0:
            break
    if best is None:
        return RouteResult(ok=False, error=last_error)
    tracks, vias = apply_session(board, best[1])
    return RouteResult(ok=True, unrouted=best[2], tracks=tracks, vias=vias)


@dataclass
class DrcResult:
    errors: int          # DRC violations with severity "error"
    warnings: int        # e.g. silkscreen overlaps; do not stop fabrication
    unconnected: int
    report_path: Path

    @property
    def ok(self):
        return self.errors == 0 and self.unconnected == 0


def run_drc(board_path, report_path):
    """Run KiCad DRC on a saved board file and count problems."""
    board = pcbnew.LoadBoard(str(board_path))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.WriteDRCReport(board, str(report_path), pcbnew.EDA_UNITS_MILLIMETRES, True)
    text = Path(report_path).read_text()
    violations = text.split("** Found")[1] if "** Found" in text else ""
    severities = re.findall(r"Severity: (\w+)", violations.split("unconnected pads")[0])
    unconnected = re.search(r"\*\* Found (\d+) unconnected pads", text)
    return DrcResult(
        errors=severities.count("error"),
        warnings=severities.count("warning"),
        unconnected=int(unconnected.group(1)) if unconnected else -1,
        report_path=Path(report_path),
    )


def export_outputs(board_path, out_dir):
    """Gerbers, drill files and an SVG preview via kicad-cli. Returns paths written."""
    out_dir = Path(out_dir)
    gerber_dir = out_dir / "gerbers"
    gerber_dir.mkdir(parents=True, exist_ok=True)
    cmds = [
        [config.KICAD_CLI, "pcb", "export", "gerbers", "--output", str(gerber_dir) + "/",
         "--layers", "F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts",
         str(board_path)],
        [config.KICAD_CLI, "pcb", "export", "drill", "--output", str(gerber_dir) + "/", str(board_path)],
        [config.KICAD_CLI, "pcb", "export", "svg", "--output", str(out_dir / "board.svg"),
         "--layers", "F.Cu,B.Cu,F.Silkscreen,Edge.Cuts", "--exclude-drawing-sheet",
         "--page-size-mode", "2", str(board_path)],
    ]
    errors = []
    for cmd in cmds:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            errors.append(f"{' '.join(cmd[:4])}: {proc.stderr.strip()[-300:]}")
    return {"gerber_dir": str(gerber_dir), "svg": str(out_dir / "board.svg"), "errors": errors}


def summarize_drc(report_path, limit=10):
    """First few violation lines of a DRC report, for logs."""
    lines = [l for l in Path(report_path).read_text().splitlines() if l.startswith("[")]
    return json.dumps(lines[:limit], indent=1)
