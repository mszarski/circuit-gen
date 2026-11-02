"""350 mA constant-current driver for three white LEDs in series on 12-15 V: a 5.1 V zener
(2.2k from VIN) and a 13.3k/1k divider set 0.355 V, an LM358 drives an IRLZ44N so the 1 R
sense resistor carries Vref / 1 R ~ 355 mA."""
vin, gnd, a1, k1, k2, k3, src, gate, vz, ref, fb = (Net(n) for n in (
    "VIN", "GND", "LED_A", "K1", "K2", "K3", "SRC", "GATE", "VZ", "REF", "FB"))
vin.drive = gnd.drive = POWER
header(vin, gnd)
vin & C("10u") & gnd
leds = [Part("Device", "LED", value="white", footprint="LED_SMD:LED_1206_3216Metric") for _ in range(3)]
vin += leds[0]["A"]; leds[0]["K"] += k1
leds[1]["A"] += k1; leds[1]["K"] += k2
leds[2]["A"] += k2; leds[2]["K"] += k3
m = Part("Transistor_FET", "IRLZ44N", footprint="Package_TO_SOT_THT:TO-220-3_Vertical")
m["D"] += k3; m["S"] += src; m["G"] += gate
sense = Part("Device", "R", value="1", footprint="Resistor_SMD:R_2512_6332Metric", tolerance=0.01)
sense[1] += src; sense[2] += gnd
z = Part("Device", "D_Zener", value="5.1V", footprint="Diode_SMD:D_SOD-123")
vin & R("2.2k") & vz
z["K"] += vz; z["A"] += gnd
vz & R("13.3k", tolerance=0.01) & ref & R("1k", tolerance=0.01) & gnd
u = Part("Amplifier_Operational", "LM358", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
u["V+"] += vin; u["V-"] += gnd
vin & C("100n") & gnd
u[3] += ref; u[2] += fb; u[1] += gate
src & R("1k") & fb
u[5] += gnd; u[6] += u[7]
