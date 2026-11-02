"""Single 9 V band-limited preamp on an LM324: mid-supply REF (10k/10k, 10u, buffered),
a unity-gain Sallen-Key high-pass at ~100 Hz (C = 100n, R1 = 11k to the output side,
R2 = 22k to REF: Q 0.71), a non-inverting gain of 1 + 9.1k/1k = 10.1 (AC-grounded through
10u), a unity-gain Sallen-Key low-pass at ~8 kHz (R = 2.8k, C1 = 10n feedback, C2 = 4.7n),
and a 10u output capacitor."""
vcc, gnd, inp, out = Net("VCC"), Net("GND"), Net("IN"), Net("OUT")
div, ref, h1, h2, o1, i2, f2, o2, m3, x3, o3 = (Net(n) for n in (
    "DIV", "REF", "H1", "H2", "O1", "I2", "F2", "O2", "M3", "X3", "O3"))
vcc.drive = gnd.drive = POWER
header(vcc, gnd); header(inp, gnd); header(out, gnd)
u = Part("Amplifier_Operational", "LM324", footprint="Package_SO:SOIC-14_3.9x8.7mm_P1.27mm")
u["V+"] += vcc; u["V-"] += gnd
vcc & C("100n") & gnd
vcc & R("10k") & div & R("10k") & gnd
div & C("10u") & gnd
u[12] += div; u[13] += ref; u[14] += ref
inp & C("100n") & h1 & C("100n") & h2
h1 & R("11k", tolerance=0.01) & o1
h2 & R("22k", tolerance=0.01) & ref
u[3] += h2; u[2] += o1; u[1] += o1
u[5] += o1; u[6] += i2; u[7] += o2
o2 & R("9.1k", tolerance=0.01) & i2 & R("1k", tolerance=0.01) & f2
f2 & C("10u") & ref
o2 & R("2.8k", tolerance=0.01) & m3 & R("2.8k", tolerance=0.01) & x3
m3 & C("10n") & o3
x3 & C("4.7n") & ref
u[10] += x3; u[9] += o3; u[8] += o3
o3 & C("10u") & out
