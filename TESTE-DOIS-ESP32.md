# Teste com dois ESP32 (AquaNível, N3)

O medidor só mede e alerta; o controlador só executa. Os dois **não têm fio entre si**: tudo passa pelo broker MQTT, seguindo o [CONTRATOS.md](CONTRATOS.md) v1. Enquanto o FastAPI não existe, quem decide é o [backend-teste/backend_teste.py](backend-teste/backend_teste.py).

## Ligação

**ESP32 medidor (rsv-medidor-01)**, a placa da N1:

| Pino | Ligado a |
|---|---|
| GPIO33 | HC-SR04 TRIG |
| GPIO34 | HC-SR04 ECHO pelo divisor resistivo |
| VIN (5 V) | HC-SR04 VCC |
| GPIO4 | DS18B20 dados, pull-up de 4,7 kΩ para 3,3 V (sensor em 3,3 V) |
| GPIO21 / GPIO22 | LCD 16x2 I2C (PCF8574) SDA / SCL |
| GPIO27 | Buzzer |
| GPIO26 | livre (o LED da bomba saiu desta placa) |

**ESP32 controlador (rsv-controlador-01)**, a placa nova:

| Pino | Ligado a |
|---|---|
| GPIO26 | Bomba: hoje LED de teste com resistor; depois In1 do relé JQC3F-03VDC (DC+ no 3V3, DC- no GND, jumper em High). ALTO = bomba ligada |
| GPIO25 | LED verde (opcional): aceso em AUTOMATICO, piscando em MANUAL |
| GPIO27 | LED vermelho (opcional): aceso em SEGURO |

## Como gravar

1. `cp credenciais.exemplo.h credenciais.h` e preencha Wi-Fi e broker (os dois ESP32 usam o mesmo arquivo). Para abrir as pastas `medidor/` ou `controlador/` na Arduino IDE, copie o `credenciais.h` para dentro delas também.
2. Compile: `./compilar.sh medidor` e `./compilar.sh controlador` (saída em `build/medidor/` e `build/controlador/`).
3. Descubra as portas com `arduino-cli board list` e grave cada uma na placa certa:
   ```
   arduino-cli upload -p COMx --fqbn esp32:esp32:esp32doit-devkit-v1 --input-dir build/medidor
   arduino-cli upload -p COMy --fqbn esp32:esp32:esp32doit-devkit-v1 --input-dir build/controlador
   ```
4. Monitor serial a 115200: `arduino-cli monitor -p COMy -c baudrate=115200`. Comandos no monitor: medidor `QUEDA | STATUS | I2C`; controlador `STATUS | QUEDA`.
5. Backend: `pip install paho-mqtt` e `python backend-teste/backend_teste.py` (padrão `broker.hivemq.com:1883`; opções `--broker`, `--porta`, `--sem-medidor`). No terminal: `ligar`, `desligar`, `auto`, `liberar`, `repetir`, `pausa`, `volta`, `status`, `sair`.

## Roteiro de testes

Anote, para cada teste, o esperado, o observado e a evidência (print do serial e do terminal do backend).

| # | Teste | Como fazer | Esperado |
|---|---|---|---|
| 1 | Boot seguro | Ligue o controlador com o backend parado | GPIO26 em LOW desde o início, LED vermelho aceso, `estado` com `modo=SEGURO`, `motivoSeguro=BOOT`. Nada liga sozinho |
| 2 | Saída do SEGURO | Rode o backend | Após 3 heartbeats válidos seguidos (~20 a 30 s) o serial mostra `SAINDO do SEGURO` e o estado vira `AUTOMATICO` |
| 3 | Ligar e desligar manual | `ligar` e depois `desligar` | Acks `RECEBIDO` e `EXECUTADO`, LED do GPIO26 acende e apaga, `modo=MANUAL`. Após 5 min sem comando (ou com `auto`) volta a `AUTOMATICO` |
| 4 | Heartbeat perdido | Com a bomba ligada, digite `pausa` | Em até 30 s a bomba desliga e o estado vira `SEGURO` com `motivoSeguro=SEM_HEARTBEAT`. `volta` tira do SEGURO depois de 3 heartbeats |
| 5 | LIGAR em SEGURO | Com o controlador em SEGURO, `ligar` | `ack` `REJEITADO` com `motivo=MODO_SEGURO`, bomba continua desligada |
| 6 | Comando duplicado | `ligar` e logo depois `repetir` | O segundo recebe `REJEITADO` com `motivo=COMANDO_DUPLICADO` e não executa de novo |
| 7 | Nível alto | Com o medidor rodando, leve a água (ou um anteparo) a 90 % ou mais com a bomba ligada | Heartbeat com `liberaBomba=false` e `motivo=NIVEL_ALTO`; o controlador desliga a bomba. `ligar` recebe `REJEITADO NIVEL_ALTO`. Em 95 % o medidor publica `NIVEL_TRANSBORDO` e toca o buzzer |
| 8 | Medidor desligado | Tire o medidor da tomada com a bomba ligada | O broker publica o Last Will (`online:false`); em até 30 s o heartbeat passa a `liberaBomba=false` com `SEM_LEITURA` e o controlador desliga a bomba. `ligar` recebe `REJEITADO SEM_LEITURA` |
| 9 | Alerta local sem rede | No medidor, digite `QUEDA` (ou desligue o hotspot) e leve o nível a 10 % ou menos | LCD mostra `CRITICO` e o buzzer toca mesmo sem Wi-Fi; o alerta sai no MQTT quando a rede volta |
| 10 | Tempo máximo | Publique em `.../rsv-controlador-01/config` (retain) `{"v":1,"t_max_ligada_s":60}` e ligue a bomba | Após 60 s a bomba desliga, `SEGURO` com `TEMPO_MAXIMO`; heartbeats não tiram daí, só `liberar` |

Para o teste 10 e para voltar ao padrão (600 s), use por exemplo o `mosquitto_pub` ou o MQTT Explorer no mesmo broker.

### Tempo de reação

Com o controlador em AUTOMATICO e a bomba desligada, leve o nível de acima de 10 % para 10 % ou menos (por exemplo, tire a mão de perto do sensor). O backend imprime `LATÊNCIA ...: telemetria -> EXECUTADO = N ms`. Meta: 1 a 3 s entre o nível mudar e a bomba agir. Os tempos de segurança do controlador (30 s sem heartbeat, 10 min de T_MAX) não mudam.

## Pontos para a v2 do contrato

O firmware e o backend de teste já fazem isto, mas o CONTRATOS.md v1 ainda não prevê:

- **Telemetria por evento.** Além da periódica, o medidor publica na hora quando o nível muda 5 pontos percentuais ou mais desde a última publicação, cruza 10 % ou 90 % (limiares da regra do backend) ou o sensor muda entre `OK` e `FALHA`, com no mínimo 1 s entre publicações. O período padrão caiu de 10 s para 5 s (`periodo_s` no config do medidor). O v1 diz "a cada 10 s".
- **Heartbeat por evento.** O backend publica um heartbeat extra na hora em que `liberaBomba` muda, antes de qualquer comando, além do de 10 s. O controlador já aceita heartbeats fora do ritmo.
- **Margem no `liberaBomba`.** O backend bloqueia com `NIVEL_ALTO` em 90 % ou mais e só volta a liberar quando o nível cai para 87 % ou menos (constantes `NIVEL_ALTO` e `NIVEL_LIBERA` no topo do `backend_teste.py`). O v1 diz só "false quando o nível está em 90 % ou mais"; sem a margem, um nível oscilando em torno de 90 % alternava a liberação a cada telemetria.
