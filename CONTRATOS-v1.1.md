# Contrato MQTT do AquaNível: revisão v1.1

**Versão v1.1, de 19/11/2026.** Registra o que mudou em relação ao contrato
v1 congelado no CP12 ([CONTRATOS.md](CONTRATOS.md)), como ele pede: "qualquer
mudança gera uma nova versão".

**Por que v1.1 e não v2.** Nenhum payload mudou de formato: os campos, os tipos
e os nomes são os do v1, e o campo `v` continua `1`. As mudanças são valores
novos em campos que já existiam, tempos, QoS efetivo e a configuração do
broker. Um componente que fala o v1 continua entendendo todas as mensagens. Uma
mudança de formato (campo novo obrigatório, campo removido ou renomeado) é que
levaria a `v: 2`.

Tudo o que não está listado aqui continua como no v1.

## 1. Broker e segurança

| v1 | v1.1 |
|---|---|
| Broker não especificado; nos testes, broker público sem senha | Mosquitto do Docker Compose, **só TLS na porta 8883** (TLS 1.2 ou mais), sem acesso anônimo |
| ACL citada como garantia de que só o backend publica `cmd` | **Usuário e senha por cliente** (`backend`, `rsv-medidor-01`, `rsv-controlador-01`) e **ACL por tópico** (`mosquitto/acl.conf`) |

ACL efetiva:

| Usuário | Publica | Assina |
|---|---|---|
| `backend` | tudo do prefixo (único em `cmd`, `heartbeat` e `config`) | tudo do prefixo |
| `rsv-medidor-01` | `telemetria`, `alerta`, `status` do medidor | `config` do medidor |
| `rsv-controlador-01` | `ack`, `estado`, `status` do controlador | `cmd` e `config` do controlador, `backend/heartbeat` |

O medidor não lê nada do controlador, e o controlador não lê a telemetria do
medidor. Os ESP32 validam o certificado do broker contra uma CA própria
embutida no firmware e pelo nome `aquanivel.local`, achado por mDNS. Os tópicos
de texto da N1 (`.../nivel`, `.../bomba`, `.../comando/bomba`...) foram
removidos; a ACL os recusa.

## 2. QoS efetivo

A biblioteca MQTT dos ESP32 (PubSubClient) **só publica em QoS 0**. Por isso as
mensagens que saem dos dispositivos não têm a garantia de entrega do QoS 1
previsto no v1. O que vale de fato:

| Tópico | v1 | v1.1 (efetivo) | Como a perda é coberta |
|---|---|---|---|
| `telemetria` | QoS 0 | QoS 0 | a próxima chega em até 3 s |
| `alerta` | QoS 1 | **QoS 0** | o nível e o `sensor` também vão na telemetria seguinte |
| `ack` | QoS 1 | **QoS 0** | o `estado` com `ultimoCommandId` confirma o comando; sem resposta em 15 s o comando vira EXPIRADO |
| `estado` | QoS 1, retain | **QoS 0**, retain | publicado de novo a cada mudança e a cada reconexão |
| `status` online | QoS 1, retain | **QoS 0**, retain | publicado de novo a cada conexão |
| `status` Last Will | QoS 1, retain | QoS 1, retain | publicado pelo broker |
| `cmd`, `config` | QoS 1 | QoS 1 | publicados pelo backend, assinados em QoS 1 |
| `backend/heartbeat` | QoS 0 | QoS 0 | o seguinte chega em até 10 s |

## 3. Telemetria por evento e período

| v1 | v1.1 |
|---|---|
| Telemetria a cada 10 s | Periódica a cada `periodo_s` (padrão **5 s**; na demonstração, **3 s**, ajustado pelo painel) **e por evento**: na hora em que o nível muda 5 pontos ou mais desde a última enviada, cruza 10 % ou 90 % (limiares da regra) ou o sensor muda entre `OK` e `FALHA`, com no mínimo 1 s entre envios |

Com `sensor: FALHA`, a telemetria continua saindo com o último valor válido em
`nivel` e `distancia` (ou `null` se ainda não houve leitura válida). O backend
trata `FALHA` como falta de leitura, qualquer que seja o valor.

Leitura fora da faixa plausível (distância 2 a 400 cm, temperatura -10 a 60 °C,
nível 0 a 100 %) não é payload inválido: o backend grava com `sensor: FALHA`,
registra o alerta `SENSOR_FALHA` e bloqueia a bomba.

## 4. Heartbeat e `liberaBomba`

| v1 | v1.1 |
|---|---|
| Heartbeat a cada 10 s | A cada 10 s **e na hora em que `liberaBomba` muda**, sempre antes de qualquer comando que dependa disso |
| `liberaBomba=false` com nível em 90 % ou mais | `false` (`NIVEL_ALTO`) a partir de 90 %, e só volta a `true` quando o nível **cai até 87 %**. Sem a margem, um nível oscilando em torno de 90 % alternava a liberação a cada telemetria |

`SEM_LEITURA` continua valendo para leitura com mais de 30 s, sensor em `FALHA`
ou medidor offline (Last Will), e tem prioridade sobre `NIVEL_ALTO`.

## 5. Estado do controlador: valores de `motivoSeguro`

O v1 só dizia "`SEGURO` (com `motivoSeguro`)". Valores usados:

| `motivoSeguro` | Quando | Como sai do SEGURO |
|---|---|---|
| `BOOT` | ao ligar o controlador | sozinho, depois de 3 heartbeats válidos seguidos |
| `SEM_HEARTBEAT` | mais de `hb_timeout_s` (30 s) sem heartbeat | sozinho, depois de 3 heartbeats |
| `TEMPO_MAXIMO` | bomba ligada há `t_max_ligada_s` (600 s) | só com o comando `LIBERAR` de um admin |
| `OTA` | gravação de firmware pelo Wi-Fi em andamento (bomba desligada antes) | reinicia em `BOOT` ao terminar |
| `OTA_FALHOU` | a gravação falhou; o firmware anterior continua | sozinho, depois de 3 heartbeats |

## 6. Ciclo de comando: regras e códigos novos

| Situação | Resposta |
|---|---|
| `LIGAR` com `origem: regra` enquanto o controlador está em `MANUAL` | `REJEITADO`, motivo **`MODO_MANUAL`** (código novo): a regra não passa por cima do usuário |
| `MODO_AUTO` em `SEGURO` | `REJEITADO`, `MODO_SEGURO` |
| `LIBERAR` em `SEGURO` por motivo diferente de `TEMPO_MAXIMO` | `REJEITADO`, `MODO_SEGURO` (esses motivos saem sozinhos e não podem ser pulados) |
| `LIBERAR` fora do `SEGURO` | aceito, sem efeito (`EXECUTADO`) |
| `DESLIGAR` | sempre aceito, em qualquer modo |
| `origem` fora de `regra` e `manual` | `REJEITADO`, `PAYLOAD_INVALIDO` |

O controlador guarda os últimos 8 `commandId`, inclusive os rejeitados: uma
reentrega nunca executa o que antes foi recusado.

## 7. Status e OTA

- `status` com `online: true` traz `fw` e `ip` (já previsto) e é republicado a
  cada conexão.
- Antes de uma gravação OTA, o **medidor publica `online: false`** (retain).
  Assim o backend tira a liberação da bomba na hora, sem esperar a leitura
  envelhecer 30 s.

## 8. Configuração

Faixas aceitas (o v1 não listava). O backend valida as mesmas faixas antes de
publicar, e o firmware valida de novo ao receber; fora da faixa, nada é aplicado
e vale a configuração anterior.

| Dispositivo | Campo | Faixa |
|---|---|---|
| medidor | `d_vazio_cm` | 5 a 400 |
| medidor | `d_cheio_cm` | 2 até `d_vazio_cm` - 2 |
| medidor | `alerta_baixo_pct` | 0 a 50, menor que `alerta_alto_pct` |
| medidor | `alerta_alto_pct` | 50 a 100 |
| medidor | `periodo_s` | 2 a 300 |
| controlador | `t_manual_s` | 30 a 3600 |
| controlador | `t_max_ligada_s` | 30 a 3600 |
| controlador | `hb_timeout_s` | 15 a 300 |

Campos ausentes mantêm o valor atual. Limitação conhecida: não há tópico de
resposta para a configuração; `VALOR_FORA_FAIXA` no dispositivo aparece só no
monitor serial (como o backend valida antes, isso só acontece com mensagem
publicada por fora do backend).

## 9. Validação no backend

Além do previsto no v1, o backend descarta e registra como `PAYLOAD_INVALIDO`
uma mensagem cujo `deviceId` não bate com o tópico em que chegou, e como
`VERSAO_INCOMPATIVEL` qualquer `v` diferente de 1. Campos a mais são ignorados,
para uma versão futura poder acrescentar campos sem quebrar quem fala o v1.

## Códigos de erro (acumulado)

Os do v1, mais:

| Código | Quando |
|---|---|
| `MODO_MANUAL` | `LIGAR` da regra com o controlador em `MANUAL` |

## Histórico de versões

| Versão | Data | Mudança |
|---|---|---|
| v1 | 27/10/2026 | Contrato congelado no CP12 ([CONTRATOS.md](CONTRATOS.md)) |
| v1.1 | 19/11/2026 | Este documento: broker com TLS, usuário por dispositivo e ACL; QoS efetivo; telemetria e heartbeat por evento; margem 90/87; valores de `motivoSeguro`; código `MODO_MANUAL`; faixas de configuração |
