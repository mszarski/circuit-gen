"""ATtiny85 sensor node: AMS1117-3.3 from VIN (5-9 V) with 10u in/out, 100n at the MCU,
10k reset pull-up, 4.7k I2C pull-ups on SDA (PB0) and SCL (PB2) to +3V3, a 4-pin I2C header,
a 6-pin ISP header and a green power LED (1k)."""
vin, v3, gnd = Net("VIN"), Net("+3V3"), Net("GND")
sda, scl, rst, pb1, la = Net("SDA"), Net("SCL"), Net("RESET"), Net("PB1"), Net("LED_A")
vin.drive = gnd.drive = POWER
header(vin, gnd)
reg = Part("Regulator_Linear", "AMS1117-3.3", footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
reg["VI"] += vin; reg["GND"] += gnd; reg["VO"] += v3
vin & C("10u") & gnd
v3 & C("10u") & gnd
u = Part("MCU_Microchip_ATtiny", "ATtiny85-20S", footprint="Package_SO:SOIC-8W_5.3x5.3mm_P1.27mm")
u["VCC"] += v3; u["GND"] += gnd
u[1] += rst; u[5] += sda; u[6] += pb1; u[7] += scl
v3 & C("100n") & gnd
v3 & R("10k") & rst
v3 & R("4.7k") & sda
v3 & R("4.7k") & scl
header(v3, gnd, sda, scl)
isp = Part("Connector", "AVR-ISP-6", footprint="Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical")
isp["MISO"] += pb1; isp["VCC"] += v3; isp["SCK"] += scl
isp["MOSI"] += sda; isp[5] += rst; isp["GND"] += gnd
led = Part("Device", "LED", value="green", footprint="LED_SMD:LED_0805_2012Metric")
v3 & R("1k") & la; la += led["A"]; led["K"] += gnd
