"""Low-side current monitor: 50 mR shunt from LOAD_NEG to GND, 1k/150n filter (~1 kHz)
into an LM358 non-inverting stage of gain 1 + 19.1k/1k = 20.1 on 5 V: OUT ~ 1 V/A."""
v12, vcc, gnd, load, out = Net("V12"), Net("VCC"), Net("GND"), Net("LOAD_NEG"), Net("OUT")
f, inv = Net("F"), Net("INV")
v12.drive = vcc.drive = gnd.drive = POWER
header(v12, gnd); header(v12, load); header(vcc, out, gnd)
shunt = Part("Device", "R", value="0.05", footprint="Resistor_SMD:R_2512_6332Metric", tolerance=0.01)
shunt[1] += load; shunt[2] += gnd
load & R("1k", tolerance=0.01) & f
f & C("150n") & gnd
u = Part("Amplifier_Operational", "LM358", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
u["V+"] += vcc; u["V-"] += gnd
vcc & C("100n") & gnd
v12 & C("10u") & gnd
u[3] += f; u[2] += inv; u[1] += out
out & R("19.1k", tolerance=0.01) & inv & R("1k", tolerance=0.01) & gnd
u[5] += gnd; u[6] += u[7]
