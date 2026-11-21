import schemdraw
import schemdraw.elements as elm

schemdraw.config(fontsize=11, font='DejaVu Sans', lw=1.3)
NET = '#1f4e79'
FS = 10


def ic(pins, size, lbl, spacing=1.0):
    """pins: list of (nome, lado, anchor). Em cada lado, a ordem e de baixo para cima."""
    e = elm.Ic(pins=[elm.IcPin(name=n, side=s, anchorname=a) for n, s, a in pins],
               size=size, pinspacing=spacing, plblsize=10)
    return e.theta(0).anchor('center').label(lbl, loc='top', fontsize=11, ofst=0.25)


def netlabel(d, p, text, side):
    d += elm.Dot(open=True).at(p).label(text, loc=side, fontsize=FS, color=NET)


def stub_net(d, p, dx, text):
    ln = elm.Line().at(p).to((p[0] + dx, p[1]))
    d += ln
    netlabel(d, ln.end, text, 'right' if dx > 0 else 'left')


def pwr(d, p, dx, text):
    d += elm.Line().at(p).to((p[0] + dx, p[1]))
    d += elm.Vdd().at((p[0] + dx, p[1])).label(text, fontsize=FS)


def gnd(d, p, dx, text=None):
    d += elm.Line().at(p).to((p[0] + dx, p[1]))
    g = elm.Ground().at((p[0] + dx, p[1]))
    if text:
        g = g.label(text, loc='right', fontsize=FS)
    d += g


def note(d, p, text):
    d += elm.Label().at(p).label(text, fontsize=FS, color='#555555')


# ======================================================== MEDIDOR
# Montagem da N3 (ver mapa_de_pinos.md): LCD e buzzer ligados direto, divisor
# do ECHO com dois resistores de 1 kOhm.
with schemdraw.Drawing(file='esquema_medidor.png', dpi=170, show=False) as d:
    esp = ic([('GND', 'left', 'gnd'), ('3V3', 'left', 'v33'), ('VIN (5 V)', 'left', 'vin'),
              ('GPIO27', 'right', 'g27'), ('GPIO22 (SCL)', 'right', 'g22'), ('GPIO21 (SDA)', 'right', 'g21'),
              ('GPIO4', 'right', 'g4'), ('GPIO34', 'right', 'g34'), ('GPIO33', 'right', 'g33')],
             (5.2, 7.0), 'ESP32 DevKit V1 (rsv-medidor-01)', spacing=1.0)
    d += esp.at((0, 0))
    stub_net(d, esp.vin, -1.2, '+5V (linha + da protoboard)')
    stub_net(d, esp.v33, -1.2, '+3V3')
    gnd(d, esp.gnd, -1.0, 'linha -')
    stub_net(d, esp.g33, 1.4, 'TRIG')
    stub_net(d, esp.g34, 1.4, 'ECHO_DIV')
    stub_net(d, esp.g4, 1.4, 'DQ')
    stub_net(d, esp.g21, 1.4, 'SDA')
    stub_net(d, esp.g22, 1.4, 'SCL')
    stub_net(d, esp.g27, 1.4, 'BUZ')
    note(d, (0, -4.6), 'Alimentação: carregador USB 5 V; o VIN alimenta a linha + da protoboard')

    # ---- HC-SR04 com divisor no ECHO
    hc = ic([('GND', 'left', 'gnd'), ('ECHO', 'left', 'echo'), ('TRIG', 'left', 'trig'), ('VCC', 'left', 'vcc')],
            (2.6, 5.0), 'HC-SR04', spacing=1.2)
    d += hc.at((19, 5.5))
    pwr(d, hc.vcc, -0.9, '+5V')
    stub_net(d, hc.trig, -5.2, 'TRIG')
    r1 = elm.Resistor().at(hc.echo).to((hc.echo[0] - 3.0, hc.echo[1])).label('R1  1 kΩ', fontsize=FS)
    d += r1
    d += elm.Dot().at(r1.end)
    stub_net(d, r1.end, -2.2, 'ECHO_DIV')
    d += elm.Resistor().at(r1.end).down(2.4).label('R2\n1 kΩ', loc='bottom', fontsize=FS)
    d += elm.Ground()
    gnd(d, hc.gnd, -0.9)
    note(d, (14.2, 9.2), 'Divisor do ECHO: 5 V x 1k/(1k+1k) = 2,5 V')

    # ---- DS18B20 com pull-up
    ds = ic([('GND', 'left', 'gnd'), ('DQ', 'left', 'dq'), ('VDD', 'left', 'vdd')],
            (2.6, 3.8), "DS18B20 (sonda)", spacing=1.2)
    d += ds.at((19, -1.5))
    pwr(d, ds.vdd, -0.9, '+3V3')
    ldq = elm.Line().at(ds.dq).to((ds.dq[0] - 2.4, ds.dq[1]))
    d += ldq
    d += elm.Dot().at(ldq.end)
    d += elm.Resistor().at(ldq.end).up(2.2).label('R3\n4,7 kΩ', loc='bottom', fontsize=FS)
    d += elm.Vdd().label('+3V3', fontsize=FS)
    stub_net(d, ldq.end, -2.8, 'DQ')
    gnd(d, ds.gnd, -0.9)

    # ---- LCD ligado direto no I2C
    lcd = ic([('GND', 'left', 'gnd'), ('SCL', 'left', 'scl'), ('SDA', 'left', 'sda'), ('VCC', 'left', 'vcc')],
             (3.0, 4.8), 'LCD 16x2 I2C (PCF8574)', spacing=1.1)
    d += lcd.at((19, -8.5))
    pwr(d, lcd.vcc, -0.9, '+5V')
    stub_net(d, lcd.sda, -2.2, 'SDA')
    stub_net(d, lcd.scl, -2.2, 'SCL')
    gnd(d, lcd.gnd, -0.9)
    note(d, (19, -12.0), 'Fios curtos e longe da bomba; I2C a 50 kHz no firmware')

    # ---- Buzzer ligado direto
    bx, by = 2.0, -7.5
    netlabel(d, (bx, by), 'BUZ', 'top')
    d += elm.ResistorIEC().at((bx, by)).down(2.6).label('BZ1 buzzer\n(+ no GPIO27)', loc='bottom', fontsize=FS)
    d += elm.Ground()
    note(d, (2.0, -12.0), 'Buzzer direto no pino, tocado por PWM de 2,7 kHz')

    note(d, (8, -14.0), 'Ligações por rótulo de rede: pontos com o mesmo nome (TRIG, ECHO_DIV, DQ, SDA, SCL, BUZ, +5V, +3V3) estão conectados. GND comum.')

# ======================================================== CONTROLADOR
# Montagem da N3: relé JQC3F-03VDC alimentado pelo 3V3, sem LEDs de status, e a
# bomba com carregador próprio chaveado pelo contato COM/NO.
with schemdraw.Drawing(file='esquema_controlador.png', dpi=170, show=False) as d:
    esp = ic([('GND', 'left', 'gnd'), ('3V3', 'left', 'v33'), ('GPIO26', 'right', 'g26')],
             (4.6, 3.4), 'ESP32 DevKit V1 (rsv-controlador-01)', spacing=1.0)
    d += esp.at((0, 0))
    stub_net(d, esp.v33, -1.2, '+3V3')
    gnd(d, esp.gnd, -1.0)
    stub_net(d, esp.g26, 1.4, 'RELE_IN')
    note(d, (0, -2.8), 'Alimentação: carregador USB 5 V próprio (não usa a protoboard)')

    rl = ic([('DC-', 'left', 'gnd'), ('IN', 'left', 'inp'), ('DC+', 'left', 'vcc'),
             ('NC', 'right', 'nc'), ('NO', 'right', 'no'), ('COM', 'right', 'com')],
            (3.6, 4.0), 'Módulo relé JQC3F-03VDC', spacing=1.2)
    d += rl.at((11.0, 0))
    note(d, (11.0, -3.0), 'bobina de 3 V; jumper em High: IN em ALTO aciona')
    pwr(d, rl.vcc, -0.9, '+3V3')
    stub_net(d, rl.inp, -1.6, 'RELE_IN')
    gnd(d, rl.gnd, -0.9)
    d += elm.Line().at(rl.nc).right(0.8)
    d += elm.Label().label('(livre)', loc='right', fontsize=FS)

    # circuito da bomba, isolado do ESP32
    ytop, ybot = 4.0, -4.5
    xm, xs = rl.no[0] + 4.0, rl.no[0] + 8.5
    d += elm.Line().at(rl.com).right(0.8)
    d += elm.Line().up(ytop - rl.com[1])
    d += elm.Line().to((xs, ytop))
    d += elm.SourceV().at((xs, ybot)).to((xs, ytop)).label('Carregador\nda bomba\n5 V', loc='bottom', fontsize=FS)
    d += elm.Line().at(rl.no).to((xm, rl.no[1]))
    d += elm.Motor().at((xm, rl.no[1])).to((xm, ybot)).label('M1 bomba\n5 V', loc='bottom', fontsize=FS)
    d += elm.Line().at((xm, ybot)).to((xs, ybot))
    note(d, ((rl.no[0] + xs) / 2 + 1.0, ybot - 1.4), 'A bomba tem alimentação própria: o relé só fecha o contato COM/NO (isolado do ESP32).')
    note(d, ((rl.no[0] + xs) / 2 + 1.0, ybot - 2.2), 'Recomendado, não montado: diodo 1N4007 e capacitor de 100 nF nos terminais da bomba.')
