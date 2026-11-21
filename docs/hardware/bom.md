# BOM (lista de materiais)

Planejada no CP12 e conferida com a montagem da N3. Detalhes das ligações e das
diferenças em relação ao CP12 em [mapa_de_pinos.md](mapa_de_pinos.md).

## Usados na montagem

| Item | Qtd | Especificação | Uso |
|---|---|---|---|
| ESP32 DevKit V1 | 2 | ESP32-WROOM-32 | Medidor e controlador |
| Sensor ultrassônico | 1 | HC-SR04 | Medidor: distância até a água |
| Sensor de temperatura | 1 | DS18B20 em sonda à prova d'água | Medidor: corrige a velocidade do som |
| Display | 1 | LCD 16x2 com módulo I2C PCF8574 | Medidor: visor local |
| Buzzer | 1 | Buzzer 5 V | Medidor: alerta local |
| Resistores | 3 | 1 kΩ (2) no divisor do ECHO; 4,7 kΩ (1) de pull-up do DS18B20 | Medidor |
| Módulo relé | 1 | 1 canal, relé JQC3F-03VDC (bobina de 3 V), DC+ no 3V3, DC- no GND, IN no GPIO26, jumper em High | Controlador |
| Mini bomba | 1 | Submersível 5 V DC + mangueira | Controlador: atuador |
| Carregadores USB 5 V | 3 | Um para cada ESP32 e um para a bomba | Alimentação |
| Protoboard | 1 | Protoboard comum | Montagem dos dois ESP32 |
| Caixa | 1 | Caixa com abertura para o LCD | Acabamento |
| Maquete | 1 | Duas garrafas: caixa d'água (com torneira e os sensores na tampa) e cisterna (com a bomba) | Demonstração |

## Previstos no CP12 e não usados

| Item | Por quê |
|---|---|
| Conversor de nível BSS138 | O LCD funciona ligado direto (I2C a 50 kHz no firmware) |
| Transistor BC547 e diodo 1N4148 do buzzer | Buzzer ligado direto no GPIO27 |
| Resistor de 2 kΩ do divisor do ECHO | Usado o divisor da N1, com dois resistores de 1 kΩ |
| Resistor de 10 kΩ de pull-down no relé | O firmware põe o GPIO26 em LOW no boot |
| LEDs verde e vermelho, resistores de 330 Ω | Ficariam escondidos na caixa; o estado aparece no painel e no Telegram |
| Diodo 1N4007, capacitores de 100 nF e 470 µF, fusível de 1 A | Não comprados; continuam recomendados nos terminais da bomba |
| Fonte de 5 V / 2 A para a bomba | Substituída por um carregador USB próprio |
| Placas perfuradas, bornes e segunda caixa | Montagem em protoboard numa caixa só |
