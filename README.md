# AquaNível: monitoramento e controle de caixa d'água

> Projeto da Equipe 4 (catolicasc-g4), Internet das Coisas 2026/2, Prof. Edson Vaz Lopes.
> Evolução do Medidor de Nível de Reservatório da N1 para o produto da N3.

Quem tem uma caixa d'água abastecida por bomba não sabe o nível de verdade e só
percebe que a água acabou quando abre a torneira. O AquaNível mede o nível da
caixa, mostra tudo no celular ou no computador, liga a bomba sozinho quando o
nível baixa e desliga quando enche, avisa pelo Telegram quando algo sai do
normal e, se a comunicação cair, desliga a bomba por segurança em vez de
arriscar transbordar. O usuário é quem cuida da água da casa, do prédio ou do
estabelecimento (morador, síndico ou zelador).

## Integrantes

| Integrante | GitHub |
|---|---|
| Anttonio Osorio Molinaro Maccagnini | @anttonio06 |
| Gabriel Lengert Guedes | @GabrielLengertGuedes |
| Heitor Lopes Reis | @dev-heitorreis |
| João Pedro Alves de Lima | @CapJao |
| João Vitor Paranhos | @joaoparanhoss |
| Rafael Alexandre Alves Bandoch | @RafaelBandoch |

## O que o sistema faz

- **Dois ESP32 com funções separadas, sem fio entre eles.** O **medidor**
  (`rsv-medidor-01`) fica na caixa: mede o nível com o HC-SR04 corrigido pela
  temperatura do DS18B20, mostra no LCD e toca o buzzer em alerta, mesmo sem
  rede. O **controlador** (`rsv-controlador-01`) fica na bomba: aciona o relé e
  aplica as proteções locais.
- **O backend decide.** Ele recebe a telemetria, aplica a regra (liga em 10 %,
  desliga em 90 %) e é o único que manda comandos ao controlador. A cada 10 s
  manda um heartbeat dizendo se a bomba pode ligar.
- **Segurança da bomba.** O controlador liga sempre com a bomba desligada,
  desliga sozinho se ficar 30 s sem heartbeat ou se a bomba passar de 10 minutos
  ligada, e recusa LIGAR em modo seguro, com nível alto ou sem leitura recente.
- **Comando com confirmação.** Cada comando tem um `commandId` e passa por
  ENVIADO, RECEBIDO, EXECUTADO (ou REJEITADO com motivo) e CONFIRMADO.
- **Painel web com login**, histórico, gráficos, alertas, comandos e parâmetros.
  O perfil administrador comanda; o visitante só vê.
- **Alertas no Telegram**: nível crítico, risco de transbordo, falha no sensor,
  bomba ligada e desligada pela regra, modo seguro e dispositivo offline.
- **Broker próprio com TLS**, usuário e senha por dispositivo e ACL por tópico.
- **Atualização de firmware pelo Wi-Fi (OTA)** nos dois ESP32.
- **140 testes automáticos** do backend rodando no GitHub Actions a cada push.

## Arquitetura

```text
 ESP32 medidor                                                ESP32 controlador
 HC-SR04, DS18B20,         MQTT com TLS (porta 8883)          relé + bomba
 LCD, buzzer        ───telemetria, alerta───▶   ┌─────────┐   ◀── cmd, heartbeat, config
                    ◀──────config─────────────  │Mosquitto│   ──▶ ack, estado
                                                └────┬────┘
                                                     │
                                       ┌─────────────▼─────────────┐
                                       │ Backend FastAPI           │──▶ Telegram
                                       │ valida, grava, decide,    │
                                       │ API REST com login (JWT)  │
                                       └──────┬─────────────┬──────┘
                                              │             │ HTTP
                                         PostgreSQL     Painel web
                                       (histórico)    (navegador)
```

Broker, backend e banco sobem juntos pelo Docker Compose num computador da
mesma rede dos ESP32. Os ESP32 acham esse computador pelo nome `aquanivel.local`
(mDNS). Diagramas detalhados em [docs/arquitetura](docs/arquitetura) e o
contrato MQTT em [CONTRATOS.md](CONTRATOS.md) (v1) e
[CONTRATOS-v1.1.md](CONTRATOS-v1.1.md) (o que mudou na implementação).

## Hardware

Pinos, materiais e o que mudou em relação ao projeto do CP12 em
[docs/hardware/mapa_de_pinos.md](docs/hardware/mapa_de_pinos.md); lista de
materiais em [docs/hardware/bom.md](docs/hardware/bom.md).

| Medidor | Pino | Controlador | Pino |
|---|---|---|---|
| HC-SR04 TRIG | GPIO33 | Relé JQC3F-03VDC (In1), jumper em High | GPIO26 |
| HC-SR04 ECHO | GPIO34 | Alimentação da bobina do relé | 3V3 |
| DS18B20 | GPIO4 | Bomba 5 V | contato do relé, carregador próprio |
| LCD I2C (SDA, SCL) | GPIO21, GPIO22 | | |
| Buzzer | GPIO27 | | |

## Estrutura do repositório

| Caminho | O que é |
| --- | --- |
| `medidor/`, `controlador/` | firmwares dos dois ESP32 |
| `broker_tls.h` | conexão TLS com o broker, comum aos dois firmwares |
| `credenciais.exemplo.h` | modelo de Wi-Fi, senhas do broker e do OTA (copiar para `credenciais.h`, que não é versionado) |
| `compilar.sh`, `ota.sh` | compila com o Arduino CLI e grava pelo Wi-Fi |
| `libraries.txt` | bibliotecas Arduino usadas |
| `docker-compose.yml`, `.env.example` | broker, banco, backend e pgAdmin com um comando |
| `mosquitto/` | configuração do broker, ACL e script dos certificados |
| `anunciar_broker.py` | anuncia o computador como `aquanivel.local` para os ESP32 |
| `liberar-firewall.ps1` | libera no Windows as portas do broker, do OTA e do mDNS |
| `backend/` | FastAPI: contrato, regras, API, Telegram; testes em `backend/tests/` |
| `painel/` | painel web, servido pelo backend |
| `pgadmin/` | servidor do pgAdmin pré-cadastrado |
| `backend-teste/` | backend mínimo usado nos testes de bancada |
| `CONTRATOS.md`, `CONTRATOS-v1.1.md` | contrato MQTT v1 (congelado no CP12) e a revisão v1.1 com o que mudou na implementação |
| `TESTE-DOIS-ESP32.md` | ligação, gravação, OTA e roteiro de testes |
| `docs/` | arquitetura, hardware, firmware, testes de falha e telas |
| `n1/` | versão da N1, mantida como histórico (ver [n1/LEIAME.md](n1/LEIAME.md)) |

## Como executar

### Pré-requisitos

- **Docker Desktop** (com Docker Compose).
- **Python 3.12** com `pip install zeroconf` (para o `anunciar_broker.py`).
- **Arduino CLI** com o core do ESP32 3.3.x e as bibliotecas do `libraries.txt`:
  ```bash
  arduino-cli core install esp32:esp32
  arduino-cli lib install PubSubClient ArduinoJson "LiquidCrystal I2C" DallasTemperature OneWire
  ```
- No Windows, os scripts `.sh` rodam no **Git Bash** (que já traz o `openssl`).
- Um roteador ou hotspot **2,4 GHz** em que os ESP32 e o computador fiquem na
  mesma rede.

### 1. Configuração

```bash
git clone https://github.com/RafaelBandoch/IoT-Reservatorio.git
cd IoT-Reservatorio
cp .env.example .env
cp pgadmin/pgpass.example pgadmin/pgpass
```

No `.env`, troque todos os `troque-aqui`. As senhas e o segredo dos tokens podem
ser gerados com:

```bash
python -c "import secrets; print(secrets.token_urlsafe(18))"
```

No `pgadmin/pgpass`, ponha a mesma senha de `POSTGRES_PASSWORD`.

### 2. Certificados do broker (uma vez só)

```bash
./mosquitto/gerar-certificados.sh
```

Gera a CA própria e o certificado do broker em `mosquitto/certs/` e o
`certificado_ca.h` para os firmwares. Gere uma vez só: os ESP32 levam a CA
embutida, e uma CA nova exige gravar os dois de novo.

### 3. Subir broker, backend e banco

```bash
docker compose up -d --build
```

- Painel: http://localhost:8000 (usuário e senha do `ADMIN_*` do `.env`; o
  `VISUALIZADOR_*` só vê, sem comandar).
- Documentação da API: http://localhost:8000/docs
- pgAdmin: http://localhost:5050

### 4. Deixar o computador acessível aos ESP32

Numa janela de terminal que fica aberta enquanto o sistema roda:

```bash
python anunciar_broker.py
```

No Windows, rode uma vez o `liberar-firewall.ps1` num PowerShell aberto como
administrador (libera as portas 8883, 3233 e 5353). Se houver antivírus com
firewall próprio, marque a rede do hotspot como confiável nele também.

### 5. Gravar os ESP32

```bash
cp credenciais.exemplo.h credenciais.h
```

No `credenciais.h`, preencha o Wi-Fi (2,4 GHz), as senhas
`MQTT_SENHA_MEDIDOR` e `MQTT_SENHA_CONTROLADOR` (as mesmas do `.env`) e a
`OTA_SENHA`. Depois:

```bash
./compilar.sh medidor
./compilar.sh controlador
arduino-cli board list          # descobre as portas COM
arduino-cli upload -p COM3 --fqbn esp32:esp32:esp32doit-devkit-v1 --input-dir build/medidor
arduino-cli upload -p COM4 --fqbn esp32:esp32:esp32doit-devkit-v1 --input-dir build/controlador
```

As próximas gravações podem ser pelo Wi-Fi, sem cabo:

```bash
./ota.sh medidor
./ota.sh controlador
```

Detalhes da ligação, da gravação e do OTA em
[TESTE-DOIS-ESP32.md](TESTE-DOIS-ESP32.md). Monitor serial a 115200 baud;
comandos: `STATUS`, `QUEDA` (derruba o Wi-Fi para testar a reconexão), `I2C`
(medidor) e `BROKER <ip>` (IP do computador, se o mDNS não funcionar na rede).

### 6. Alertas no Telegram (opcional)

1. No Telegram, fale com o **@BotFather**, mande `/newbot` e guarde o token.
2. Mande qualquer mensagem para o bot criado e abra
   `https://api.telegram.org/bot<TOKEN>/getUpdates` para ver o `chat.id`.
3. Ponha `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` no `.env` e rode
   `docker compose up -d backend`.

### 7. Testes automáticos

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

Os mesmos testes rodam no GitHub Actions a cada push e pull request
([.github/workflows/testes.yml](.github/workflows/testes.yml)).

## Requisitos da N3

| Req. | Como foi atendido | Onde |
|---|---|---|
| R1 Hardware | HC-SR04 e DS18B20 (duas entradas) e relé com bomba real | `docs/hardware` |
| R2 Firmware | leitura não bloqueante, alerta local sem rede (LCD e buzzer), estado seguro, reconexão, configuração na NVS alterável pelo painel, NTP ou `NAO_SINCRONIZADO`, logs | `medidor/`, `controlador/` |
| R3 Contrato | contrato v1 com versão, unidades, `commandId`, QoS e Last Will; mudanças registradas na v1.1 | `CONTRATOS.md`, `CONTRATOS-v1.1.md` |
| R4 Backend | valida, descarta e registra inválidos, grava e expõe API | `backend/app` |
| R5 Banco | medições, comandos e confirmações, alertas, estado, usuários; migrações Alembic | `backend/alembic` |
| R6 Interface | painel com valor atual, histórico, online e offline, alertas e comandos | `painel/` |
| R7 Ciclo de comando | ENVIADO, RECEBIDO, EXECUTADO ou REJEITADO com motivo, CONFIRMADO | painel, `comandos` |
| R8 Decisão remota | regra 10/90 no backend com margem 90/87 | `backend/app/logica.py` |
| R9 Segurança básica | login JWT, só admin comanda, segredos fora do git | `.env`, `credenciais.h` |
| R10 Falhas | 4 cenários testados com evidência | [docs/testes-de-falha.md](docs/testes-de-falha.md) |
| R11 Entrega | este README, release da versão defendida | este arquivo |
| R12 Orquestração | `docker compose up -d` sobe broker, backend e banco | `docker-compose.yml` |
| R13 Dispositivos | dois ESP32 físicos com `deviceId` próprio | painel |
| R14 Painel analítico | gráfico de histórico, média, mínimo e máximo, tempo de bomba ligada, acionamentos | painel |
| R15 Notificação | alertas no Telegram, com reenvio se a internet cair | `backend/app/telegram.py` |
| R16 Segurança avançada | TLS no MQTT com certificado validado pelos ESP32 e perfis admin e visitante | `mosquitto/`, `broker_tls.h` |
| R17 OTA | ArduinoOTA com senha nos dois ESP32, com a bomba desligada durante a gravação | `ota.sh` |
| R18 Testes e CI | 140 testes pytest no GitHub Actions | `backend/tests` |

## Limitações conhecidas

- O servidor (broker, backend e banco) roda num computador da mesma rede; num
  produto vendido ele iria para a nuvem com o mesmo Docker Compose.
- O hotspot do celular com bateria baixa deixou o Wi-Fi do medidor instável em
  alguns testes (quedas por tempo esgotado no broker, com reconexão automática).
  Na demonstração, o celular fica carregando e perto da caixa.
- Diodo, capacitor e fusível previstos para a bomba no CP12 não foram montados;
  o ruído do motor no LCD foi resolvido encurtando e afastando os fios.
- A rede Wi-Fi e as senhas ficam gravadas no firmware (`credenciais.h`); trocar
  de rede exige gravar de novo.

## Reaproveitamento

Bibliotecas usadas como dependências, sem alteração: PubSubClient, ArduinoJson,
LiquidCrystal I2C, DallasTemperature, OneWire e ArduinoOTA (firmware); FastAPI,
aiomqtt, Pydantic, SQLAlchemy, Alembic, PyJWT, bcrypt e httpx (backend);
Chart.js (painel); Eclipse Mosquitto e PostgreSQL (infraestrutura).
