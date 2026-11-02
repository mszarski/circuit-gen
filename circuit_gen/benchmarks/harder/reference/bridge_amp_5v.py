"""Single-supply 5 V bridge amplifier: three-op-amp instrumentation amplifier from two
MCP6002s, gain (1 + 2 * 10k / 2.2k) * 100k / 10k = 100.9, with the difference stage
referred to a buffered 2.5 V mid-supply (10k/10k divider, 1u), so OUT = 2.5 V + 100 x (IN_P - IN_N)."""
vcc, gnd, inp, inn, out = Net("VCC"), Net("GND"), Net("IN_P"), Net("IN_N"), Net("OUT")
a1, a2, g1, g2, ni, inv, div, ref = (Net(n) for n in ("A1", "A2", "G1", "G2", "NI", "INV", "DIV", "REF"))
vcc.drive = gnd.drive = POWER
header(vcc, gnd); header(inp, inn, gnd); header(out, gnd)
u1 = Part("Amplifier_Operational", "MCP6002-xSN", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
u2 = Part("Amplifier_Operational", "MCP6002-xSN", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
for u in (u1, u2):
    u["V+"] += vcc; u["V-"] += gnd
    vcc & C("100n") & gnd
u1[3] += inp; u1[2] += g1; u1[1] += a1
u1[5] += inn; u1[6] += g2; u1[7] += a2
a1 & R("10k", tolerance=0.01) & g1 & R("2.2k", tolerance=0.01) & g2 & R("10k", tolerance=0.01) & a2
a2 & R("10k", tolerance=0.001) & inv & R("100k", tolerance=0.001) & out
a1 & R("10k", tolerance=0.001) & ni & R("100k", tolerance=0.001) & ref
u2[2] += inv; u2[3] += ni; u2[1] += out
vcc & R("10k", tolerance=0.01) & div & R("10k", tolerance=0.01) & gnd
div & C("1u") & gnd
u2[5] += div; u2[6] += ref; u2[7] += ref
