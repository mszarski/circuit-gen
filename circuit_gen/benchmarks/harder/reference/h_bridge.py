"""Discrete H-bridge on 12 V: each side an AO3401A high-side and AO3400A low-side MOSFET with
tied gates, pulled up to V12 by 10k and pulled down by a BC847 driven from the logic input
through 10k (100k base pull-down). IN high -> gate low -> high side on: that output at V12.
IN low or floating -> gate high -> low side on: that output at GND (brake)."""
v12, gnd = Net("V12"), Net("GND")
ina, inb, ma, mb = Net("IN_A"), Net("IN_B"), Net("MOTOR_A"), Net("MOTOR_B")
v12.drive = gnd.drive = POWER
header(v12, gnd); header(ina, inb, gnd); header(ma, mb)
v12 & C("100u") & gnd
v12 & C("100n") & gnd
for inp, outp, k in ((ina, ma, "A"), (inb, mb, "B")):
    g, b = Net("G" + k), Net("B" + k)
    p = Part("Transistor_FET", "AO3401A", footprint="Package_TO_SOT_SMD:SOT-23")
    n = Part("Transistor_FET", "AO3400A", footprint="Package_TO_SOT_SMD:SOT-23")
    p["S"] += v12; p["D"] += outp; p["G"] += g
    n["S"] += gnd; n["D"] += outp; n["G"] += g
    v12 & R("10k") & g
    q = Part("Transistor_BJT", "BC847", footprint="Package_TO_SOT_SMD:SOT-23")
    q["C"] += g; q["E"] += gnd; q["B"] += b
    inp & R("10k") & b & R("100k") & gnd
