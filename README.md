# Projeto N1 — Medidor de Nível de Água do Reservatório

## 1. Integrantes

* Anttonio Osorio Molinaro Maccagnini - @anttonio06
* Gabriel Lengert Guedes - @GabrielLengertGuedes
* João Pedro Alves de Lima - @CapJao
* João Vitor Paranhos - @joaoparanhoss
* Rafael Alexandre Alves Bandoch - @RafaelBandoch
* Heitor Lopes Reis - @dev-heitorreis

---

## 2. Família temática

**Família 4 — Nível de reservatório**

---

## 3. Projeto

**Sistema IoT de medição de nível de um reservatório de água, com ESP32, sensor ultrassônico, Wi-Fi e MQTT.** O nível é lido, publicado na rede e só então aciona a bomba (LED) automaticamente — a decisão nunca é tomada localmente no mesmo ciclo da leitura.

---

## 4. Problema

É importante acompanhar a quantidade de água disponível em um reservatório para evitar que ele fique vazio ou transborde. A verificação manual do nível é imprecisa e exige alguém conferindo o reservatório constantemente, sem histórico e sem aviso a distância.

O projeto mede o nível de água do reservatório, publica essa leitura pela rede, aciona automaticamente uma bomba quando o nível está baixo, permite ligar/desligar remotamente, e alerta (display + buzzer) em situações de nível baixo ou crítico.

---

## 5. Usuário ou contexto de uso

O sistema pode ser usado em residências, pequenos estabelecimentos, laboratórios ou qualquer ambiente com um reservatório de água. O usuário acompanha o nível pelo display físico (LCD 16x2) ou por um painel web que recebe os dados em tempo real pela internet, e pode ligar/desligar a bomba manualmente pelo mesmo painel.

---

## 6. Objetivo da N1

Um protótipo com ESP32 que:

1. mede a distância até a água com um sensor ultrassônico HC-SR04;
2. converte essa distância em percentual de 0% a 100%;
3. publica essa leitura via MQTT **antes** de qualquer decisão de acionamento;
4. decide ligar/desligar a bomba (simulada por um LED) a partir do próprio tópico MQTT que ele assina de volta — não a partir do valor calculado localmente;
5. aceita comando remoto (ligar/desligar manual) por outro tópico MQTT;
6. confirma o estado da bomba e a execução dos comandos;
7. sinaliza alertas de nível baixo/crítico no display, no buzzer e via MQTT;
8. reconecta sozinho ao Wi-Fi e ao broker depois de uma queda.

---

## 7. Componentes usados

* ESP32 DevKit (ESP32-D0WD-V3);
* Sensor ultrassônico HC-SR04 — TRIG no D33, ECHO no D34 através de um divisor resistivo 1kΩ + 1kΩ (o ECHO do sensor sai em 5V, e o ESP32 não é 5V-tolerante);
* Display LCD 16x2 com módulo I2C (PCF8574) — SDA no D21, SCL no D22;
* LED — representa a bomba/válvula, no D26 (com resistor em série);
* Buzzer ativo 5V — alerta sonoro, no D27;
* Sensor de temperatura DS18B20 — DQ no D4, com resistor de pull-up de 4k7 para o 3V3. A temperatura lida a cada 3s corrige a velocidade do som no cálculo da distância; sem o sensor, o firmware assume 25°C;
* Protoboard, jumpers, cabo USB-C;
* Computador com Arduino CLI para compilar e gravar o firmware.

---

## 8. Arquitetura

```text
HC-SR04 (distância) ──▶ ESP32 (firmware)
                            │
                            │ mede, filtra (mediana + média móvel)
                            ▼
                     publica "nivel" no MQTT
                            │
                            ▼
                 broker.hivemq.com (broker público, internet)
                    │                           │
                    ▼                           ▼
     ESP32 assina o próprio "nivel"      painel-reservatorio.local.html
     de volta e SÓ AÍ decide ligar/           (navegador, MQTT sobre
     desligar a bomba (LED) — nunca           WebSocket)
     direto do valor calculado
                    │
                    ▼
              LED (bomba) + buzzer (alerta)
```

A leitura sempre atravessa a rede (publica → broker → assina de volta) antes de qualquer atuação. O sensor nunca aciona o atuador diretamente dentro do mesmo `loop()`.

### Tópicos MQTT (prefixo `catolicasc-g4/reservatorio`)

| Tópico | Sentido | Conteúdo |
| --- | --- | --- |
| `.../nivel` | ESP32 → broker → ESP32 (e painel) | percentual 0-100, publicado a cada 2s; é a mensagem que dispara a decisão automática |
| `.../distancia` | ESP32 → broker | distância filtrada em cm |
| `.../temperatura` | ESP32 → broker | temperatura do DS18B20 em °C, usada no cálculo da distância |
| `.../bomba` | ESP32 → broker | `DESLIGADA`, `LIGADA:AUTO` ou `LIGADA:MANUAL` (retido) |
| `.../alerta/nivel` | ESP32 → broker | `NORMAL`, `NIVEL_BAIXO`, `CRITICO_ALTO`, `ACIONAMENTO_AUTOMATICO`, `ENCHIMENTO_CONCLUIDO` |
| `.../status` | ESP32 → broker | `online` (retido) / `offline` (Last Will) |
| `.../comando/bomba` | painel → broker → ESP32 | `LIGAR` ou `PARAR` |
| `.../comando/bomba/confirmacao` | ESP32 → broker | `LIGAR:OK`, `PARAR:OK` ou `ERRO:comando_invalido` |

---

## 9. Como rodar

1. Copie o modelo de credenciais e preencha com a rede Wi-Fi real:
   ```bash
   cp credenciais.exemplo.h credenciais.h
   ```
   Edite `WIFI_SSID`/`WIFI_SENHA` e, se necessário, `MQTT_BROKER` (hoje configurado para o broker público `broker.hivemq.com`, sem necessidade de infraestrutura própria).
2. Compile o firmware:
   ```bash
   ./compilar.sh
   ```
3. Grave na placa (com o ESP32 conectado por USB):
   ```bash
   arduino-cli upload -p COM3 --fqbn esp32:esp32:esp32doit-devkit-v1 --input-dir build build
   ```
   (troque `COM3` pela porta serial correta)
4. Acompanhe o boot pelo monitor serial (115200 baud) — comandos disponíveis: `QUEDA`, `STATUS`, `LIGAR`, `PARAR`, `I2C`.
5. Abra `painel-reservatorio.local.html` no navegador para ver os dados em tempo real e ligar/desligar a bomba manualmente.

O repositório também tem um `docker-compose.yml` com Mosquitto + Postgres + coletor, para quem quiser rodar um broker próprio em vez do público — não é necessário para o funcionamento atual do projeto.

---

## 10. Backlog

| Tarefa | Status |
| --- | --- |
| Criar repositório | Feito |
| Migrar de Arduino Uno para ESP32 | Feito |
| Ligar sensor ultrassônico (com divisor resistivo) | Feito |
| Ligar display LCD 16x2 via I2C | Feito |
| Calcular nível em porcentagem, com filtro de mediana + média móvel | Feito |
| Conectar ao Wi-Fi com reconexão automática | Feito |
| Publicar telemetria via MQTT | Feito |
| Reestruturar decisão automática para depender do tópico, não do cálculo local | Feito |
| Implementar comando remoto (ligar/desligar) | Feito |
| Confirmar estado/ação via MQTT | Feito |
| Adicionar alerta sonoro (buzzer) e visual (LED) | Feito |
| Montar painel web (MQTT sobre WebSocket) | Feito |
| Integrar sensor de temperatura (DS18B20) | Feito |
| Calibrar `D_VAZIO`/`D_CHEIO` para o recipiente real (23,5cm vazio / 4cm cheio) | Feito |
| Testar reconexão após queda de Wi-Fi/broker (comando `QUEDA`, ver EXECUCAO.md) | Feito |

---

## 11. Primeiro risco técnico

### Risco

O sensor ultrassônico pode apresentar leituras imprecisas, instáveis ou inválidas, dependendo da fiação (o ECHO sai em 5V e passa por um divisor resistivo até o ESP32), do posicionamento e de ruído elétrico no barramento.

### Impacto

Leituras inválidas poderiam travar o cálculo do nível ou, pior, ser interpretadas como "reservatório cheio" por engano.

### Mitigação já implementada

* Leituras fora da faixa físicamente possível (`d < 2cm` ou `d > 450cm`) são descartadas, não usadas no cálculo.
* Um contador de falhas seguidas (`falhasSensor`) loga no serial, a cada 50 falhas, um diagnóstico de fiação (nível do ECHO em repouso, pinos usados).
* As leituras válidas passam por filtro de mediana (últimas 5) + média móvel exponencial, reduzindo o efeito de picos isolados.

---

## 12. Dúvidas para o professor

* O broker público (`broker.hivemq.com`) é aceitável para a demonstração da N1, ou é esperado um broker próprio (Mosquitto local)?
