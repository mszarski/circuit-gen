"""555 PWM on 12 V at ~1 kHz, ~30% on: diode from DIS to THR, RA = 3.9k charges and
RB = 10k discharges 100n. OUT drives an AO3400A low-side switch through 100 R with a
100k gate pull-down; flyback diode across the LED-strip load."""
v12, gnd, load, q, gate = Net("V12"), Net("GND"), Net("LOAD_NEG"), Net("PWM"), Net("GATE")
dis, thr, cv = Net("DIS"), Net("THR"), Net("CV")
v12.drive = gnd.drive = POWER
header(v12, gnd); header(v12, load)
t = Part("Timer", "NE555D", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
t["VCC"] += v12; t["R"] += v12; t["GND"] += gnd
t["DIS"] += dis; t["THR"] += thr; t["TR"] += thr; t["CV"] += cv; t["Q"] += q
v12 & R("3.9k", tolerance=0.01) & dis & R("10k", tolerance=0.01) & thr
dd = Part("Diode", "1N4148W", footprint="Diode_SMD:D_SOD-123")
dd["A"] += dis; dd["K"] += thr
thr & C("100n") & gnd
cv & C("10n") & gnd
v12 & C("100n") & gnd
v12 & C("100u") & gnd
m = Part("Transistor_FET", "AO3400A", footprint="Package_TO_SOT_SMD:SOT-23")
m["D"] += load; m["S"] += gnd; m["G"] += gate
q & R("100") & gate
gate & R("100k") & gnd
fd = Part("Diode", "SS34", footprint="Diode_SMD:D_SMA")
fd["A"] += load; fd["K"] += v12
