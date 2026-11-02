"""12 V in through an SS34 reverse-polarity diode, LM7805 to +5V, AMS1117-3.3 from +5V to
+3V3, bulk and output capacitors, a green LED on +5V (1.5k) and a red LED on +3V3 (680 R)."""
vin, vp, v5, v3, gnd = Net("VIN"), Net("VPROT"), Net("+5V"), Net("+3V3"), Net("GND")
a5, a3 = Net("LED5_A"), Net("LED3_A")
vin.drive = gnd.drive = POWER
header(vin, gnd); header(v5, v3, gnd)
d = Part("Diode", "SS34", footprint="Diode_SMD:D_SMA")
d["A"] += vin; d["K"] += vp
cin = Part("Device", "C_Polarized", value="100u", footprint="Capacitor_SMD:CP_Elec_6.3x7.7")
cin[1] += vp; cin[2] += gnd
r5 = Part("Regulator_Linear", "LM7805_TO220", footprint="Package_TO_SOT_THT:TO-220-3_Vertical")
r5["VI"] += vp; r5["GND"] += gnd; r5["VO"] += v5
vp & C("330n") & gnd
v5 & C("10u") & gnd
r3 = Part("Regulator_Linear", "AMS1117-3.3", footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
r3["VI"] += v5; r3["GND"] += gnd; r3["VO"] += v3
v3 & C("22u") & gnd
l5 = Part("Device", "LED", value="green", footprint="LED_SMD:LED_0805_2012Metric")
l3 = Part("Device", "LED", value="red", footprint="LED_SMD:LED_0805_2012Metric")
v5 & R("1.5k") & a5; a5 += l5["A"]; l5["K"] += gnd
v3 & R("680") & a3; a3 += l3["A"]; l3["K"] += gnd
