"""LM2596-5.0 buck from 12-24 V to +5V (33uH, SS34, 680u in, 220u out), then an AMS1117-3.3
post-regulator for a quiet +3V3 rail."""
vin, v5, v3, gnd, sw = Net("VIN"), Net("+5V"), Net("+3V3"), Net("GND"), Net("SW")
vin.drive = gnd.drive = POWER
header(vin, gnd); header(v5, v3, gnd)
u = Part("Regulator_Switching", "LM2596S-5", footprint="Package_TO_SOT_SMD:TO-263-5_TabPin3")
u["VIN"] += vin; u["GND"] += gnd; u["OUT"] += sw; u["FB"] += v5; u[5] += gnd
cin = Part("Device", "C_Polarized", value="680u", footprint="Capacitor_SMD:CP_Elec_10x10.5")
cin[1] += vin; cin[2] += gnd
vin & C("100n") & gnd
d = Part("Diode", "SS34", footprint="Diode_SMD:D_SMA")
d["K"] += sw; d["A"] += gnd
l = Part("Device", "L", value="33u", footprint="Inductor_SMD:L_12x12mm_H8mm")
l[1] += sw; l[2] += v5
cout = Part("Device", "C_Polarized", value="220u", footprint="Capacitor_SMD:CP_Elec_10x10.5")
cout[1] += v5; cout[2] += gnd
r3 = Part("Regulator_Linear", "AMS1117-3.3", footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
r3["VI"] += v5; r3["GND"] += gnd; r3["VO"] += v3
v3 & C("22u") & gnd
