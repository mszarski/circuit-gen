"""4th-order Butterworth low-pass at 1 kHz on +/-12 V: two unity-gain Sallen-Key stages
with Q = 0.54 (R = 14.7k, C1 = 12n feedback, C2 = 10n) and Q = 1.31 (R = 18.7k, C1 = 22n,
C2 = 3.3n), each at fc = 1/(2 pi R sqrt(C1 C2)) ~ 1 kHz."""
vpos, vneg, gnd, inp, out = Net("VPOS"), Net("VNEG"), Net("GND"), Net("IN"), Net("OUT")
m1, x1, o1, m2, x2 = Net("M1"), Net("X1"), Net("O1"), Net("M2"), Net("X2")
vpos.drive = vneg.drive = gnd.drive = POWER
header(vpos, gnd, vneg); header(inp, gnd); header(out, gnd)
u = tl072()
u["V+"] += vpos; u["V-"] += vneg
vpos & C("100n") & gnd; vneg & C("100n") & gnd
inp & R("14.7k", tolerance=0.01) & m1 & R("14.7k", tolerance=0.01) & x1
m1 & C("12n") & o1
x1 & C("10n") & gnd
u[3] += x1; u[2] += o1; u[1] += o1
o1 & R("18.7k", tolerance=0.01) & m2 & R("18.7k", tolerance=0.01) & x2
m2 & C("22n") & out
x2 & C("3.3n") & gnd
u[5] += x2; u[6] += out; u[7] += out
