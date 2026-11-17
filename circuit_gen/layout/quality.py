"""
Layout rules that make a routed board electrically sound, not just connected:
which capacitor decouples which IC power pin (so it can be placed there), and
which nets carry supply or load current (so they get wider tracks).

Works on the circuit dict from the runner; no KiCad needed.
"""

import re

from ..checks import POWER_IN_TYPES, is_ground
from ..spice import parse_value

POWER_OUT_TYPES = {"PWROUT", "power_out"}
_IC_REF = re.compile(r"^(U|IC)\d")


def _two_pin(part, prefix):
    return part["symbol"].startswith(prefix) and len(part["pins"]) == 2


def _value(part):
    try:
        return parse_value(part.get("value") or "")
    except (ValueError, TypeError):
        return None


def decoupling_pairs(circuit):
    """[(capacitor ref, IC ref, IC pin number)]: one capacitor for each IC supply pin.

    For every IC power-input pin on a non-ground net, the smallest capacitor
    from that net to ground that is not already serving another pin (small
    ceramics belong at the pin; bulk capacitors can sit further away).
    """
    caps = {}                                    # net -> [(value, ref)]
    for part in circuit["parts"]:
        if not _two_pin(part, "Device:C"):
            continue
        nets = [p["net"] for p in part["pins"]]
        if sum(is_ground(n) for n in nets) == 1:
            net = next(n for n in nets if not is_ground(n))
            if net:
                caps.setdefault(net, []).append((_value(part) or 1.0, part["ref"]))
    for options in caps.values():
        options.sort()
    pairs, used = [], set()
    for part in circuit["parts"]:
        if not _IC_REF.match(part["ref"]):
            continue
        seen = set()
        for pin in part["pins"]:
            net = pin["net"]
            if pin["type"] not in POWER_IN_TYPES or not net or is_ground(net) or net in seen:
                continue
            seen.add(net)
            free = [ref for _, ref in caps.get(net, []) if ref not in used]
            if free:
                used.add(free[0])
                pairs.append((free[0], part["ref"], pin["num"]))
    return pairs


def power_nets(circuit):
    """Nets that carry supply or load current and should be routed with wide tracks.

    Ground; any net on an IC or regulator power pin; and the nets of parts that
    only appear in current paths: inductors, power MOSFET drains and sources,
    resistors under 1 ohm (shunts) and Schottky or rectifier diodes.
    """
    nets = set()
    for part in circuit["parts"]:
        sym, pins = part["symbol"], part["pins"]
        for pin in pins:
            if pin["net"] and (is_ground(pin["net"]) or pin["type"] in POWER_IN_TYPES | POWER_OUT_TYPES):
                nets.add(pin["net"])
        heavy = (_two_pin(part, "Device:L")
                 or (_two_pin(part, "Device:R") and (_value(part) or 1e9) < 1)
                 or (sym.startswith("Diode:") and re.search(r":(SS|1N58|1N400|1N540|MBR|B[0-9])", sym)))
        if heavy:
            nets.update(p["net"] for p in pins if p["net"])
        if sym.startswith("Transistor_FET"):
            nets.update(p["net"] for p in pins if p["net"] and p["name"] in ("D", "S"))
    return nets
