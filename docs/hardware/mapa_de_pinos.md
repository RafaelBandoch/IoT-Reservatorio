# Mapa de pinos (CP12, v1.0)

Valores calculados a partir dos datasheets, não medidos. Esquemas: `esquema_medidor.png` e `esquema_controlador.png` nesta pasta.

## ESP32 medidor (rsv-medidor-01)

| Pino ESP32 | Ligado a | Função | Observação |
|---|---|---|---|
| VIN (5 V) | Carregador USB 5 V / 1 A; HC-SR04, LCD, buzzer | Alimentação | Entrada do regulador de 3,3 V |
| 3V3 | DS18B20, R3, lado LV do conversor | Alimentação 3,3 V | Só cargas leves (cerca de 2 mA) |
| GND | Todos os módulos | Terra comum | |
| GPIO33 | HC-SR04 TRIG | Saída, pulso de 10 µs | Mesmo pino da N1 |
| GPIO34 | HC-SR04 ECHO via divisor R1 = 1 kΩ / R2 = 2 kΩ | Entrada digital | Só entrada; máximo 3,33 V |
| GPIO4 | DS18B20 DQ | 1-Wire | Pull-up R3 4,7 kΩ para 3,3 V |
| GPIO21 | LV1 do conversor BSS138 → SDA do LCD | I2C SDA | Conversor 3,3 V ↔ 5 V |
| GPIO22 | LV2 do conversor BSS138 → SCL do LCD | I2C SCL | Conversor 3,3 V ↔ 5 V |
| GPIO27 | Base de Q1 (BC547) via R4 = 1 kΩ | Saída do buzzer | Buzzer acionado por transistor |

## ESP32 controlador (rsv-controlador-01)

| Pino ESP32 | Ligado a | Função | Observação |
|---|---|---|---|
| VIN (5 V) | Fonte A: carregador USB 5 V / 1 A | Alimentação | Separada da Fonte B (bomba) |
| 3V3 | DC+ do módulo relé JQC3F-03VDC | Bobina do relé (3 V) | Cerca de 120 mA com o relé acionado, tirados do regulador de 3,3 V da placa |
| GND | DC- do módulo relé, LEDs, R5 | Terra do circuito lógico | Não vai ao GND da Fonte B |
| GPIO26 | In1 do módulo relé (jumper em High) + R5 = 10 kΩ ao GND | ALTO = bomba ligada | Em reset fica BAIXO: bomba desligada |
| GPIO25 | R6 = 330 Ω + LD1 verde | Operando (AUTOMÁTICO ou MANUAL) | |
| GPIO27 | R7 = 330 Ω + LD2 vermelho | Estado SEGURO | |

## Regras seguidas

- Nenhum GPIO de 6 a 11 e nenhum pino de strapping (0, 2, 5, 12, 15).
- Nenhum sinal de 5 V direto em GPIO: ECHO pelo divisor, I2C pelo conversor de nível.
- Nenhuma carga alimentada por GPIO: bomba pela Fonte B via relé, bobina pelo transistor do módulo, buzzer por Q1.
- Sem sensores analógicos (HC-SR04 e DS18B20 são digitais).
