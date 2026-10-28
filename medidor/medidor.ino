// ESP32 MEDIDOR (rsv-medidor-01) do AquaNivel.
//
// Mede o nivel do reservatorio (HC-SR04 corrigido pela temperatura do
// DS18B20), mostra no LCD, toca o buzzer no alerta local e publica a
// telemetria v1 do CONTRATOS.md. Nao aciona bomba nenhuma: quem decide e o
// backend, e quem executa e o ESP32 controlador. Os dois nao tem fio entre
// si, tudo passa pelo broker.
//
// O alerta local (LCD e buzzer) e calculado aqui mesmo, a partir da leitura,
// entao continua funcionando sem Wi-Fi e sem broker.
#include <WiFi.h>
#include "credenciais.h"   // copie de credenciais.exemplo.h; nao e versionado
#if MQTT_TLS
  #include <WiFiClientSecure.h>
#endif
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <time.h>
#include <stdarg.h>

#define DEVICE_ID "rsv-medidor-01"
#define FW_VERSAO "2.0.0"

// ---------- pinos ----------
// O HC-SR04 e alimentado com 5V, entao o ECHO responde em 5V. Nenhum pino do
// ESP32 e 5V-tolerante, por isso o ECHO chega no GPIO34 por um divisor
// resistivo. O GPIO34 e entrada pura, sem pull-up nem pull-down interno,
// entao nada disputa com o divisor. O GPIO26 ficou livre: o LED da bomba
// saiu deste ESP32 e foi para o controlador.
#define PIN_TRIG    33
#define PIN_ECHO    34
#define PIN_TEMP    4
#define PIN_BUZZER  27

// ---------- display no barramento I2C ----------
#define I2C_SDA     21
#define I2C_SCL     22
#define LCD_COLUNAS 16
#define LCD_LINHAS  2

// ---------- calibracao e limites (padrao; o valor vigente vem da NVS) ----------
// Calibrado para o recipiente real usado nos testes: 23,5cm de altura.
// D_CHEIO fica em 4cm em vez de 0 de proposito: o HC-SR04 tem uma zona
// morta de ~2cm, e 4cm da margem mesmo com a agua balancando.
const float PADRAO_D_VAZIO   = 23.5;   // cm do sensor ate o fundo
const float PADRAO_D_CHEIO   = 4.0;    // cm do sensor ate o nivel maximo
const int   PADRAO_ALERTA_BAIXO = 10;  // NIVEL_CRITICO quando nivel <= isto
const int   PADRAO_ALERTA_ALTO  = 95;  // NIVEL_TRANSBORDO quando nivel >= isto
const int   PADRAO_PERIODO_S    = 5;   // telemetria periodica
const float ALFA_EMA = 0.30;

// Telemetria por evento: alem da periodica, sai na hora quando o nivel anda
// 5 pontos desde a ultima publicada, cruza um limiar da regra do backend
// (liga em <= 10%, desliga e bloqueia em >= 90%) ou o sensor muda entre OK
// e FALHA. Nunca mais de uma por segundo, para nao inundar o broker.
const int NIVEL_DELTA_EVENTO = 5;
const int LIMIAR_LIGA  = 10;
const int LIMIAR_ALTO  = 90;
const unsigned long PUB_INTERVALO_MIN = 1000;

// Faixas plausiveis do contrato. Fora delas a leitura e descartada.
const float DIST_MIN = 2.0, DIST_MAX = 400.0;
const float TEMP_MIN = -10.0, TEMP_MAX = 60.0;

// Quantas leituras invalidas seguidas viram SENSOR_FALHA. O HC-SR04 e lido a
// cada 100 ms (30 = 3 s); o DS18B20 a cada 3 s (3 = 9 s).
const unsigned long FALHAS_DIST_LIMITE = 30;
const unsigned long FALHAS_TEMP_LIMITE = 3;

// Margem para sair do alerta. Sem ela, um nivel oscilando em 10% faria o
// alerta abrir e fechar a cada leitura.
const int MARGEM_NORMALIZA = 2;

// ---------- rede ----------
const char* SSID    = WIFI_SSID;
const char* SENHA   = WIFI_SENHA;
const char* BROKER  = MQTT_BROKER;
const int   PORTA   = MQTT_PORTA;
const char* PREFIXO = MQTT_PREFIXO;

const unsigned long WIFI_ESPERA_INICIAL = 5000;
const unsigned long WIFI_ESPERA_MAXIMA  = 30000;
const long NTP_FUSO = -3 * 3600;   // horario de Brasilia (UTC-3), so para o log

#if MQTT_TLS
WiFiClientSecure net;
const char* MQTT_USER = MQTT_USUARIO;
const char* MQTT_PASS = MQTT_SENHA;
#else
WiFiClient net;
const char* MQTT_USER = nullptr;
const char* MQTT_PASS = nullptr;
#endif
PubSubClient mqtt(net);
Preferences nvs;
LiquidCrystal_I2C* lcd = nullptr;   // so nasce se um LCD responder
enum TipoTela { SEM_TELA, TELA_LCD };
TipoTela tipoTela = SEM_TELA;
OneWire fio(PIN_TEMP);
DallasTemperature sensorTemp(&fio);

// ---------- parametros vigentes ----------
float dVazio = PADRAO_D_VAZIO, dCheio = PADRAO_D_CHEIO;
int alertaBaixo = PADRAO_ALERTA_BAIXO, alertaAlto = PADRAO_ALERTA_ALTO;
int periodoS = PADRAO_PERIODO_S;

// ---------- estado da medicao ----------
float buf[5]; uint8_t bufIdx = 0; bool bufCheio = false;
float distFiltrada = -1, temperatura = 25.0;
unsigned long falhasSensor = 0;   // leituras invalidas seguidas do HC-SR04
unsigned long falhasTemp = 0;     // leituras invalidas seguidas do DS18B20
int nivel = 0;
bool sensorFalha = false;
unsigned long seqTelemetria = 0;
int nivelPublicado = -1;            // nivel da ultima telemetria (-1 = nenhuma)
bool sensorFalhaPublicado = false;
unsigned long tLeitura = 0, tTela = 0, tPub = 0, tPubAntigo = 0, tMqtt = 0;
unsigned long tTempPedido = 0;
bool tempConvertendo = false;
unsigned long backoff = 1000;

// ---------- alerta local ----------
enum Alerta { AL_NORMAL, AL_CRITICO, AL_TRANSBORDO, AL_FALHA };
Alerta alerta = AL_NORMAL;

// Alertas que aconteceram sem broker ficam guardados (com a hora do evento)
// e saem assim que o MQTT volta. Cabem os 4 ultimos.
struct AlertaPendente { char codigo[20]; char ts[25]; int nivel; };
AlertaPendente pendentes[4];
uint8_t nPendentes = 0;

// ---------- estado da rede ----------
bool wifiConectado = false, horaSincronizada = false;
unsigned long tWifi = 0, tTentativa = 0, tQueda = 0, tSemWifi = 0, tStatus = 0;
unsigned long esperaWifi = WIFI_ESPERA_INICIAL;
unsigned long tentativasWifi = 0, quedasWifi = 0;

// ---------- log carimbado ----------
// Toda linha sai com [data hora | uptime] na frente, para conferir o log com
// o relogio do computador na mesma tela.
void carimbo(char* saida, size_t n) {
  unsigned long s = millis() / 1000;
  char hora[20] = "--/-- --:--:--";
  if (horaSincronizada) {
    time_t agora = time(nullptr);
    struct tm t;
    localtime_r(&agora, &t);
    strftime(hora, sizeof(hora), "%d/%m %H:%M:%S", &t);
  }
  snprintf(saida, n, "[%s | up %02lu:%02lu:%02lu]", hora, s / 3600, (s / 60) % 60, s % 60);
}

void logSerial(const char* fmt, ...) {
  char ts[40];
  carimbo(ts, sizeof(ts));
  char msg[240];
  va_list args;
  va_start(args, fmt);
  vsnprintf(msg, sizeof(msg), fmt, args);
  va_end(args);
  Serial.print(ts); Serial.print(' '); Serial.println(msg);
}

// Hora em ISO 8601 UTC para o campo ts do contrato. Devolve false sem NTP:
// quem chama manda "relogio":"NAO_SINCRONIZADO" no lugar.
bool horaIso(char* saida, size_t n) {
  if (!horaSincronizada) return false;
  time_t agora = time(nullptr);
  struct tm t;
  gmtime_r(&agora, &t);
  strftime(saida, n, "%Y-%m-%dT%H:%M:%SZ", &t);
  return true;
}

void carimbarJson(JsonDocument& doc) {
  char ts[25];
  if (horaIso(ts, sizeof(ts))) doc["ts"] = String(ts);
  else                         doc["relogio"] = "NAO_SINCRONIZADO";
}

const char* nomeStatusWifi(wl_status_t s) {
  switch (s) {
    case WL_CONNECTED:       return "CONECTADO";
    case WL_NO_SSID_AVAIL:   return "SSID_NAO_ENCONTRADO";
    case WL_CONNECT_FAILED:  return "FALHA_NA_AUTENTICACAO";
    case WL_CONNECTION_LOST: return "CONEXAO_PERDIDA";
    case WL_DISCONNECTED:    return "DESCONECTADO";
    case WL_IDLE_STATUS:     return "OCIOSO";
    default:                 return "DESCONHECIDO";
  }
}

// RSSI e potencia recebida em dBm, sempre negativa: quanto mais perto de zero,
// mais forte o sinal.
const char* qualidadeRssi(int rssi) {
  if (rssi >= -60) return "boa";
  if (rssi >= -70) return "aceitavel";
  if (rssi >= -80) return "fraca";
  return "critica";
}

const char* codigoAlerta(Alerta a) {
  switch (a) {
    case AL_CRITICO:    return "NIVEL_CRITICO";
    case AL_TRANSBORDO: return "NIVEL_TRANSBORDO";
    case AL_FALHA:      return "SENSOR_FALHA";
    default:            return "NORMALIZADO";
  }
}

void imprimirStatus() {
  wl_status_t s = WiFi.status();
  if (s == WL_CONNECTED) {
    int rssi = WiFi.RSSI();
    logSerial("ATIVO: IP=%s RSSI=%d dBm (%s) | MQTT=%s | nivel=%d%% dist=%.1fcm temp=%.1fC sensor=%s alerta=%s | quedas=%lu",
              WiFi.localIP().toString().c_str(), rssi, qualidadeRssi(rssi),
              mqtt.connected() ? "conectado" : "desconectado",
              nivel, distFiltrada, temperatura, sensorFalha ? "FALHA" : "OK",
              alerta == AL_NORMAL ? "NORMAL" : codigoAlerta(alerta), quedasWifi);
  } else {
    logSerial("OFFLINE: status=%s | tentativas=%lu | quedas=%lu | nivel=%d%% alerta=%s",
              nomeStatusWifi(s), tentativasWifi, quedasWifi, nivel,
              alerta == AL_NORMAL ? "NORMAL" : codigoAlerta(alerta));
  }
}

// ---------- wi-fi ----------
void iniciarWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);        // nao regrava as credenciais na flash a cada boot
  WiFi.setAutoReconnect(false);  // a reconexao e feita por supervisionarWifi()
  logSerial("Wi-Fi: associando ao SSID \"%s\" e pedindo endereco por DHCP...", SSID);
  WiFi.begin(SSID, SENHA);
  tTentativa = millis();
  tentativasWifi = 1;
}

void aoConectarWifi() {
  wifiConectado = true;
  esperaWifi = WIFI_ESPERA_INICIAL;
  backoff = 1000;   // com rede de volta o MQTT tenta na hora
  tMqtt = millis() - backoff;
  int rssi = WiFi.RSSI();
  logSerial("Wi-Fi CONECTADO: SSID=%s canal=%d RSSI=%d dBm (%s)",
            WiFi.SSID().c_str(), WiFi.channel(), rssi, qualidadeRssi(rssi));
  logSerial("DHCP entregou: IP=%s mascara=%s gateway=%s DNS=%s",
            WiFi.localIP().toString().c_str(), WiFi.subnetMask().toString().c_str(),
            WiFi.gatewayIP().toString().c_str(), WiFi.dnsIP().toString().c_str());
  if (tQueda) {
    logSerial("RECONEXAO AUTOMATICA concluida em %.1f s apos a queda #%lu",
              (millis() - tQueda) / 1000.0, quedasWifi);
    tQueda = 0;
  }
  if (!horaSincronizada) {
    configTime(NTP_FUSO, 0, "pool.ntp.org", "a.st1.ntp.br");
    logSerial("NTP: pedindo a hora certa para carimbar as proximas linhas");
  }
}

// Caminho unico para registrar a perda, venha de uma queda real ou do
// comando QUEDA digitado no monitor serial.
void registrarQueda(const char* motivo) {
  if (!wifiConectado) return;
  wifiConectado = false;
  quedasWifi++;
  tQueda = millis();
  tSemWifi = 0;
  logSerial("Wi-Fi PERDIDO (queda #%lu): %s", quedasWifi, motivo);
  mqtt.disconnect();
  esperaWifi = WIFI_ESPERA_INICIAL;
  tTentativa = millis() - esperaWifi;   // primeira tentativa sai imediatamente
}

// Detecta e reage a uma queda de Wi-Fi. Roda a cada 250 ms dentro do loop(),
// sem bloquear: medicao, tela e alerta local seguem enquanto a rede nao volta.
void supervisionarWifi() {
  unsigned long agora = millis();
  if (agora - tWifi < 250) return;
  tWifi = agora;

  wl_status_t s = WiFi.status();

  if (s == WL_CONNECTED) {
    if (!wifiConectado) aoConectarWifi();
    if (!horaSincronizada && time(nullptr) > 1700000000) {
      horaSincronizada = true;
      logSerial("NTP: relogio sincronizado, as mensagens passam a levar ts");
    }
    return;
  }

  if (wifiConectado) {
    char motivo[80];
    snprintf(motivo, sizeof(motivo), "queda detectada pelo firmware (status=%s)", nomeStatusWifi(s));
    registrarQueda(motivo);
  }

  if (agora - tSemWifi >= (tQueda ? 1000UL : 2000UL)) {
    tSemWifi = agora;
    if (tQueda) logSerial("sem Wi-Fi ha %.1f s (status=%s), tentando voltar sozinho",
                          (agora - tQueda) / 1000.0, nomeStatusWifi(s));
    else        logSerial("ainda sem IP (status=%s), aguardando o Wi-Fi subir", nomeStatusWifi(s));
  }

  if (agora - tTentativa < esperaWifi) return;
  tTentativa = agora;
  tentativasWifi++;
  logSerial("Wi-Fi: tentativa #%lu de %s (proxima em %lu s se esta falhar)",
            tentativasWifi, tQueda ? "reconexao" : "conexao", esperaWifi / 1000);
  WiFi.disconnect(false, false);
  WiFi.begin(SSID, SENHA);
  esperaWifi = min(esperaWifi * 2, WIFI_ESPERA_MAXIMA);
}

// ---------- buzzer nao bloqueante ----------
// Cada padrao e uma lista de duracoes em ms (liga, desliga, liga...) que
// termina em 0. Os repetitivos tocam enquanto o alerta durar.
const uint16_t PAD_CRITICO[]   = {200, 200, 200, 1400, 0};
const uint16_t PAD_TRANSBORDO[] = {800, 400, 0};
const uint16_t PAD_FALHA[]     = {100, 2900, 0};
const uint16_t* padrao = nullptr;
uint8_t padIdx = 0; bool padRepete = false; unsigned long padT = 0;

void tocar(const uint16_t* p, bool rep) {
  if (padrao == p) return;
  padrao = p; padIdx = 0; padRepete = rep; padT = millis();
  digitalWrite(PIN_BUZZER, HIGH);
}
void pararBuzzer() { padrao = nullptr; digitalWrite(PIN_BUZZER, LOW); }
void atualizarBuzzer() {
  if (!padrao) return;
  if (millis() - padT < padrao[padIdx]) return;
  padT = millis(); padIdx++;
  if (padrao[padIdx] == 0) {
    if (padRepete) { padIdx = 0; digitalWrite(PIN_BUZZER, HIGH); }
    else pararBuzzer();
    return;
  }
  digitalWrite(PIN_BUZZER, (padIdx % 2 == 0) ? HIGH : LOW);
}

// ---------- NVS ----------
void carregarConfig() {
  nvs.begin("medidor", true);
  dVazio      = nvs.getFloat("d_vazio", PADRAO_D_VAZIO);
  dCheio      = nvs.getFloat("d_cheio", PADRAO_D_CHEIO);
  alertaBaixo = nvs.getInt("al_baixo", PADRAO_ALERTA_BAIXO);
  alertaAlto  = nvs.getInt("al_alto", PADRAO_ALERTA_ALTO);
  periodoS    = nvs.getInt("periodo", PADRAO_PERIODO_S);
  nvs.end();
  logSerial("NVS: d_vazio=%.1fcm d_cheio=%.1fcm alerta_baixo=%d%% alerta_alto=%d%% periodo=%ds",
            dVazio, dCheio, alertaBaixo, alertaAlto, periodoS);
}

void gravarConfig() {
  nvs.begin("medidor", false);
  nvs.putFloat("d_vazio", dVazio);
  nvs.putFloat("d_cheio", dCheio);
  nvs.putInt("al_baixo", alertaBaixo);
  nvs.putInt("al_alto", alertaAlto);
  nvs.putInt("periodo", periodoS);
  nvs.end();
}

// Mensagem em .../rsv-medidor-01/config. Campos ausentes mantem o valor
// atual; se qualquer campo sair da faixa, nada e aplicado (VALOR_FORA_FAIXA).
void aplicarConfig(const byte* payload, unsigned int len) {
  if (len == 0) return;   // retain apagado no broker
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) {
    logSerial("config: PAYLOAD_INVALIDO (JSON malformado), mantendo a configuracao atual");
    return;
  }
  if (doc["v"] != 1) {
    logSerial("config: VERSAO_INCOMPATIVEL, mantendo a configuracao atual");
    return;
  }
  float nVazio = doc["d_vazio_cm"] | dVazio;
  float nCheio = doc["d_cheio_cm"] | dCheio;
  int nBaixo   = doc["alerta_baixo_pct"] | alertaBaixo;
  int nAlto    = doc["alerta_alto_pct"] | alertaAlto;
  int nPeriodo = doc["periodo_s"] | periodoS;

  bool ok = nVazio >= 5 && nVazio <= DIST_MAX &&
            nCheio >= DIST_MIN && nCheio <= nVazio - 2 &&
            nBaixo >= 0 && nBaixo <= 50 &&
            nAlto >= 50 && nAlto <= 100 && nBaixo < nAlto &&
            nPeriodo >= 2 && nPeriodo <= 300;
  if (!ok) {
    logSerial("config: VALOR_FORA_FAIXA (d_vazio=%.1f d_cheio=%.1f baixo=%d alto=%d periodo=%d), mantendo a atual",
              nVazio, nCheio, nBaixo, nAlto, nPeriodo);
    return;
  }
  if (nVazio == dVazio && nCheio == dCheio && nBaixo == alertaBaixo &&
      nAlto == alertaAlto && nPeriodo == periodoS) {
    logSerial("config: recebida, igual a vigente");
    return;
  }
  dVazio = nVazio; dCheio = nCheio; alertaBaixo = nBaixo; alertaAlto = nAlto; periodoS = nPeriodo;
  gravarConfig();
  logSerial("config: aplicada e gravada na NVS (d_vazio=%.1f d_cheio=%.1f baixo=%d alto=%d periodo=%d)",
            dVazio, dCheio, alertaBaixo, alertaAlto, periodoS);
}

// ---------- medicao ----------
float lerDistancia() {
  float v = 331.4 + 0.606 * temperatura;      // m/s
  float cmPorUs = v / 10000.0;
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(4);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long dur = pulseIn(PIN_ECHO, HIGH, 25000UL);
  if (dur == 0) return -1.0;
  float d = (dur * cmPorUs) / 2.0;
  // Abaixo de ~2 cm o HC-SR04 nao enxerga nada: valor menor e pulso espurio
  // de pino flutuante (sensor sem alimentacao ou fio do ECHO solto).
  if (d < DIST_MIN) return -1.0;
  return d;
}

float mediana() {
  float t[5]; uint8_t n = bufCheio ? 5 : bufIdx;
  if (n == 0) return -1.0;
  for (uint8_t i = 0; i < n; i++) t[i] = buf[i];
  for (uint8_t i = 1; i < n; i++) {
    float k = t[i]; int j = i - 1;
    while (j >= 0 && t[j] > k) { t[j + 1] = t[j]; j--; }
    t[j + 1] = k;
  }
  return t[n / 2];
}

void medir() {
  float d = lerDistancia();
  if (d < 0 || d > DIST_MAX) {
    // Uma falha isolada e normal (eco perdido). Muitas seguidas sao fiacao.
    falhasSensor++;
    if (falhasSensor % 50 == 0)
      logSerial("HC-SR04: %lu leituras invalidas seguidas (ECHO em repouso=%d), confira "
                "VCC no 5V, GND, TRIG no D%d e ECHO pelo divisor ate o D%d",
                falhasSensor, digitalRead(PIN_ECHO), PIN_TRIG, PIN_ECHO);
    return;
  }
  falhasSensor = 0;
  buf[bufIdx] = d;
  bufIdx = (bufIdx + 1) % 5;
  if (bufIdx == 0) bufCheio = true;
  float m = mediana();
  if (m < 0) return;
  distFiltrada = (distFiltrada < 0) ? m : (ALFA_EMA * m + (1 - ALFA_EMA) * distFiltrada);
  float pct = (dVazio - distFiltrada) / (dVazio - dCheio) * 100.0;
  nivel = constrain((int)round(pct), 0, 100);
}

// O DS18B20 leva ~750 ms para converter. Em vez de esperar parado, o pedido
// sai a cada 3 s e a leitura e feita 800 ms depois, sem travar o loop.
void medirTemperatura(unsigned long agora) {
  if (!tempConvertendo && agora - tTempPedido >= 3000) {
    sensorTemp.requestTemperatures();
    tTempPedido = agora;
    tempConvertendo = true;
    return;
  }
  if (!tempConvertendo || agora - tTempPedido < 800) return;
  tempConvertendo = false;
  float t = sensorTemp.getTempCByIndex(0);
  if (t >= TEMP_MIN && t <= TEMP_MAX) {
    temperatura = t;
    falhasTemp = 0;
  } else {
    falhasTemp++;
    if (falhasTemp % 5 == 0)
      logSerial("DS18B20: %lu leituras invalidas seguidas (t=%.1f, %d sensor(es) no barramento), "
                "confira VCC, GND, DATA no D%d e o pull-up de 4.7k",
                falhasTemp, t, sensorTemp.getDeviceCount(), PIN_TEMP);
  }
}

// ---------- publicacao ----------
void topicoMedidor(char* saida, size_t n, const char* sufixo) {
  snprintf(saida, n, "%s/%s/%s", PREFIXO, DEVICE_ID, sufixo);
}

bool publicarJson(const char* sufixo, JsonDocument& doc, bool retained = false) {
  if (!mqtt.connected()) return false;
  char topico[110], payload[400];
  topicoMedidor(topico, sizeof(topico), sufixo);
  serializeJson(doc, payload, sizeof(payload));
  return mqtt.publish(topico, payload, retained);
}

// Topicos de texto da N1, mantidos durante a transicao para o painel e o
// coletor antigos continuarem funcionando.
void pubAntigo(const char* sufixo, const char* payload, bool retained = false) {
  if (!mqtt.connected()) return;
  char topico[100];
  snprintf(topico, sizeof(topico), "%s/%s", PREFIXO, sufixo);
  mqtt.publish(topico, payload, retained);
}

void publicarTelemetria() {
  JsonDocument doc;
  doc["v"] = 1;
  doc["deviceId"] = DEVICE_ID;
  carimbarJson(doc);
  doc["seq"] = ++seqTelemetria;
  bool temLeitura = distFiltrada >= 0;
  JsonObject n = doc["nivel"].to<JsonObject>();
  if (temLeitura) n["valor"] = nivel; else n["valor"] = nullptr;
  n["unidade"] = "%";
  JsonObject d = doc["distancia"].to<JsonObject>();
  if (temLeitura) d["valor"] = round(distFiltrada * 10) / 10.0; else d["valor"] = nullptr;
  d["unidade"] = "cm";
  JsonObject t = doc["temperatura"].to<JsonObject>();
  t["valor"] = round(temperatura * 10) / 10.0;
  t["unidade"] = "°C";
  doc["sensor"] = sensorFalha ? "FALHA" : "OK";
  if (publicarJson("telemetria", doc) && temLeitura) nivelPublicado = nivel;
  sensorFalhaPublicado = sensorFalha;
}

// Motivo para publicar fora do periodo, ou nullptr se nao ha.
const char* motivoEvento() {
  if (sensorFalha != sensorFalhaPublicado) return "sensor mudou";
  if (distFiltrada < 0 || nivelPublicado < 0) return nullptr;
  if ((nivel <= LIMIAR_LIGA) != (nivelPublicado <= LIMIAR_LIGA)) return "cruzou 10%";
  if ((nivel >= LIMIAR_ALTO) != (nivelPublicado >= LIMIAR_ALTO)) return "cruzou 90%";
  if (abs(nivel - nivelPublicado) >= NIVEL_DELTA_EVENTO) return "variou 5 pontos";
  return nullptr;
}

bool enviarAlerta(const char* codigo, const char* ts, int nivelEvento) {
  JsonDocument doc;
  doc["v"] = 1;
  doc["deviceId"] = DEVICE_ID;
  if (ts[0]) doc["ts"] = String(ts);
  else       doc["relogio"] = "NAO_SINCRONIZADO";
  doc["codigo"] = codigo;
  JsonObject n = doc["nivel"].to<JsonObject>();
  n["valor"] = nivelEvento;
  n["unidade"] = "%";
  if (!publicarJson("alerta", doc)) return false;
  pubAntigo("alerta/nivel", codigo);
  logSerial("MQTT: alerta %s publicado (nivel %d%%)", codigo, nivelEvento);
  return true;
}

// O alerta leva a hora do evento, nao a hora do envio. Sem broker, entra na
// fila e sai na reconexao.
void registrarAlerta(Alerta a) {
  AlertaPendente p;
  strncpy(p.codigo, codigoAlerta(a), sizeof(p.codigo));
  p.codigo[sizeof(p.codigo) - 1] = 0;
  if (!horaIso(p.ts, sizeof(p.ts))) p.ts[0] = 0;
  p.nivel = nivel;
  if (nPendentes == 0 && enviarAlerta(p.codigo, p.ts, p.nivel)) return;
  if (nPendentes == 4) {   // fila cheia: descarta o mais antigo
    for (uint8_t i = 1; i < 4; i++) pendentes[i - 1] = pendentes[i];
    nPendentes = 3;
  }
  pendentes[nPendentes++] = p;
  logSerial("alerta %s guardado para envio quando o broker voltar (%u na fila)", p.codigo, nPendentes);
}

void descarregarPendentes() {
  while (nPendentes && mqtt.connected()) {
    if (!enviarAlerta(pendentes[0].codigo, pendentes[0].ts, pendentes[0].nivel)) return;
    for (uint8_t i = 1; i < nPendentes; i++) pendentes[i - 1] = pendentes[i];
    nPendentes--;
  }
}

// ---------- alerta local ----------
// Decide o alerta a partir da leitura local, sem depender da rede.
void avaliarAlerta() {
  bool falhaDist = falhasSensor >= FALHAS_DIST_LIMITE ||
                   (distFiltrada < 0 && millis() > 5000);
  bool falhaTemp = falhasTemp >= FALHAS_TEMP_LIMITE;
  bool falha = falhaDist || falhaTemp;
  if (falha != sensorFalha) {
    sensorFalha = falha;
    if (falha) logSerial("SENSOR_FALHA: %s", falhaDist ? "HC-SR04 sem leitura valida" : "DS18B20 fora da faixa");
    else       logSerial("sensores de volta ao normal");
  }
  if (distFiltrada < 0 && !sensorFalha) return;   // ainda no boot, sem leitura

  Alerta novo;
  if (sensorFalha)               novo = AL_FALHA;
  else if (nivel <= alertaBaixo) novo = AL_CRITICO;
  else if (nivel >= alertaAlto)  novo = AL_TRANSBORDO;
  else if (alerta == AL_CRITICO && nivel <= alertaBaixo + MARGEM_NORMALIZA)   novo = AL_CRITICO;
  else if (alerta == AL_TRANSBORDO && nivel >= alertaAlto - MARGEM_NORMALIZA) novo = AL_TRANSBORDO;
  else                           novo = AL_NORMAL;

  if (novo == alerta) return;
  alerta = novo;
  logSerial("ALERTA LOCAL: %s (nivel %d%%)", codigoAlerta(alerta), nivel);
  switch (alerta) {
    case AL_CRITICO:    tocar(PAD_CRITICO, true); break;
    case AL_TRANSBORDO: tocar(PAD_TRANSBORDO, true); break;
    case AL_FALHA:      tocar(PAD_FALHA, true); break;
    default:            pararBuzzer(); break;
  }
  registrarAlerta(alerta);
}

// ---------- barramento I2C ----------
// Le o nivel eletrico das duas linhas com e sem os pull-ups internos. Separa
// "modulo mudo" de "modulo sem alimentacao" sem precisar de multimetro.
void diagnosticarLinhasI2C() {
  pinMode(I2C_SDA, INPUT);
  pinMode(I2C_SCL, INPUT);
  delayMicroseconds(200);
  int sdaSolto = digitalRead(I2C_SDA), sclSolto = digitalRead(I2C_SCL);
  pinMode(I2C_SDA, INPUT_PULLUP);
  pinMode(I2C_SCL, INPUT_PULLUP);
  delayMicroseconds(400);
  int sdaPuxado = digitalRead(I2C_SDA), sclPuxado = digitalRead(I2C_SCL);
  logSerial("I2C: sem pull-up SDA(D%d)=%d SCL(D%d)=%d | com pull-up interno SDA=%d SCL=%d",
            I2C_SDA, sdaSolto, I2C_SCL, sclSolto, sdaPuxado, sclPuxado);
  logSerial("I2C: 1/1 sem pull-up = modulo alimentado | 0 que vira 1 = linha solta | "
            "0 que continua 0 = presa no GND");
  Wire.begin(I2C_SDA, I2C_SCL);
}

uint8_t varrerBarramento(const char* quando, uint8_t pinoSda, uint8_t pinoScl) {
  Wire.begin(pinoSda, pinoScl);
  uint8_t preferido = 0, primeiro = 0;
  for (uint8_t ender = 0x08; ender < 0x78; ender++) {
    Wire.beginTransmission(ender);
    if (Wire.endTransmission() != 0) continue;
    if (!primeiro) primeiro = ender;
    if (ender == 0x27 || ender == 0x3F) preferido = ender;
    logSerial("I2C (%s): dispositivo respondeu em 0x%02X (SDA=D%d SCL=D%d)",
              quando, ender, pinoSda, pinoScl);
  }
  return preferido ? preferido : primeiro;
}

uint8_t varrerI2C(const char* quando) {
  diagnosticarLinhasI2C();
  uint8_t ender = varrerBarramento(quando, I2C_SDA, I2C_SCL);
  if (ender) return ender;
  // Antes de desistir, tenta com os dois fios trocados: e o erro de
  // montagem mais comum.
  ender = varrerBarramento(quando, I2C_SCL, I2C_SDA);
  if (ender) {
    logSerial("I2C: respondeu com os fios TROCADOS (SDA no D%d e SCL no D%d). "
              "Funciona assim; o certo seria SDA no D%d e SCL no D%d",
              I2C_SCL, I2C_SDA, I2C_SDA, I2C_SCL);
    return ender;
  }
  logSerial("I2C (%s): ninguem respondeu nas duas ordens de fio, confira VCC, GND, "
            "SDA no D%d e SCL no D%d", quando, I2C_SDA, I2C_SCL);
  Wire.begin(I2C_SDA, I2C_SCL);
  return 0;
}

void iniciarTela(uint8_t ender) {
  lcd = new LiquidCrystal_I2C(ender, LCD_COLUNAS, LCD_LINHAS);
  lcd->init();
  lcd->backlight();
  lcd->setCursor(0, 0); lcd->print("AquaNivel");
  lcd->setCursor(0, 1); lcd->print("Medidor...");
  tipoTela = TELA_LCD;
  logSerial("LCD 16x2 em uso no endereco 0x%02X", ender);
}

// ---------- tela ----------
// Cada linha e montada inteira com snprintf e escrita por cima da anterior:
// o preenchimento ate 16 colunas apaga o resto sem clear(), que faria piscar.
void desenharTela() {
  if (tipoTela != TELA_LCD) return;
  char l1[17], l2[17];
  snprintf(l1, sizeof(l1), "Nivel %3d%% %4.1fC", nivel, temperatura);
  const char* rede = mqtt.connected() ? "MQTT" : (wifiConectado ? "WIFI" : "----");
  switch (alerta) {
    case AL_CRITICO:    snprintf(l2, sizeof(l2), "CRITICO     %-4s", rede); break;
    case AL_TRANSBORDO: snprintf(l2, sizeof(l2), "TRANSBORDO  %-4s", rede); break;
    case AL_FALHA:      snprintf(l2, sizeof(l2), "FALHA SENSOR%-4s", rede); break;
    default:
      if (distFiltrada < 0) snprintf(l2, sizeof(l2), "Medindo...  %-4s", rede);
      else                  snprintf(l2, sizeof(l2), "D %5.1fcm   %-4s", distFiltrada, rede);
  }
  lcd->setCursor(0, 0); lcd->print(l1);
  lcd->setCursor(0, 1); lcd->print(l2);
}

// ---------- comandos digitados no monitor serial ----------
void processarComandoSerial(String c) {
  c.trim(); c.toUpperCase();
  if (c == "QUEDA") {
    logSerial("COMANDO \"QUEDA\": derrubando o Wi-Fi de proposito para testar a reconexao");
    registrarQueda("desconexao provocada pelo comando QUEDA no monitor serial");
    WiFi.disconnect(false, false);   // so desassocia; nao apaga as credenciais
  } else if (c == "STATUS") {
    imprimirStatus();
  } else if (c == "I2C") {
    uint8_t ender = varrerI2C("comando");
    if (ender && tipoTela == SEM_TELA) iniciarTela(ender);
  } else {
    logSerial("comando \"%s\" desconhecido: use QUEDA, STATUS ou I2C", c.c_str());
  }
}

void lerSerial() {
  static String linha;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (linha.length()) {
        Serial.println();
        processarComandoSerial(linha);
        linha = "";
      }
    } else if (c == 8 || c == 127) {          // backspace
      if (linha.length()) {
        linha.remove(linha.length() - 1);
        Serial.print("\b \b");
      }
    } else if (linha.length() < 32) {
      linha += c;
      Serial.write(c);   // eco: alguns terminais nao ecoam sozinhos
    }
  }
}

// ---------- MQTT ----------
void aoReceber(char* topico, byte* payload, unsigned int len) {
  char esperado[110];
  topicoMedidor(esperado, sizeof(esperado), "config");
  if (strcmp(topico, esperado) == 0) aplicarConfig(payload, len);
}

void conectarMqtt() {
  if (mqtt.connected() || !wifiConectado) return;
  if (millis() - tMqtt < backoff) return;
  tMqtt = millis();
  char id[48], lwt[110], payloadLwt[80];
  snprintf(id, sizeof(id), "catolicasc-g4-%s-%06X", DEVICE_ID, (uint32_t)ESP.getEfuseMac());
  topicoMedidor(lwt, sizeof(lwt), "status");
  snprintf(payloadLwt, sizeof(payloadLwt), "{\"v\":1,\"deviceId\":\"%s\",\"online\":false}", DEVICE_ID);
  if (mqtt.connect(id, MQTT_USER, MQTT_PASS, lwt, 1, true, payloadLwt)) {
    backoff = 1000;
    JsonDocument st;
    st["v"] = 1;
    st["deviceId"] = DEVICE_ID;
    st["online"] = true;
    st["fw"] = FW_VERSAO;
    st["ip"] = WiFi.localIP().toString();
    publicarJson("status", st, true);
    pubAntigo("status", "online", true);
    char sub[110];
    topicoMedidor(sub, sizeof(sub), "config");
    mqtt.subscribe(sub, 1);
    logSerial("MQTT conectado ao broker %s:%d como %s", BROKER, PORTA, id);
    descarregarPendentes();
    tPub = millis() - (unsigned long)periodoS * 1000UL;   // telemetria sai ja
  } else {
    backoff = min(backoff * 2, 30000UL);
    logSerial("MQTT: falha ao conectar em %s:%d (rc=%d), nova tentativa em %lu s",
              BROKER, PORTA, mqtt.state(), backoff / 1000);
  }
}

void setup() {
  // Buzzer calado antes de qualquer outra coisa.
  pinMode(PIN_BUZZER, OUTPUT); digitalWrite(PIN_BUZZER, LOW);
  Serial.begin(115200);
  delay(300);   // da tempo do monitor serial engatar antes do cabecalho
  Serial.println();
  logSerial("===== AquaNivel MEDIDOR %s (fw %s) =====", DEVICE_ID, FW_VERSAO);
  logSerial("Topico base: %s/%s", PREFIXO, DEVICE_ID);
  logSerial("Comandos do monitor serial: QUEDA | STATUS | I2C");
  pinMode(PIN_TRIG, OUTPUT); pinMode(PIN_ECHO, INPUT);

  carregarConfig();

  Wire.begin(I2C_SDA, I2C_SCL);
  uint8_t ender = varrerI2C("boot");
  if (ender) iniciarTela(ender);
  else logSerial("display: seguindo sem tela, corrija a ligacao e digite I2C no monitor");

  sensorTemp.begin();
  sensorTemp.setWaitForConversion(false);   // conversao sem bloquear o loop
  iniciarWifi();

#if MQTT_TLS
  net.setInsecure();   // sem validar a cadeia; em producao, setCACert(...)
#endif

  mqtt.setServer(BROKER, PORTA);
  mqtt.setCallback(aoReceber);
  mqtt.setBufferSize(512);
}

void loop() {
  unsigned long agora = millis();

  if (agora - tLeitura >= 100) { tLeitura = agora; medir(); avaliarAlerta(); }
  medirTemperatura(agora);

  if (agora - tTela >= 500) { tTela = agora; desenharTela(); }

  // Evento dentro do intervalo minimo fica para a proxima volta do loop: a
  // condicao e reavaliada e sai assim que o segundo fecha.
  if (mqtt.connected() && agora - tPub >= PUB_INTERVALO_MIN) {
    const char* evento = motivoEvento();
    bool periodica = agora - tPub >= (unsigned long)periodoS * 1000UL;
    if (evento || periodica) {
      if (evento) logSerial("telemetria por evento (%s): nivel %d%% -> %d%%", evento, nivelPublicado, nivel);
      tPub = agora;
      publicarTelemetria();
    }
  }

  // Topicos de texto da N1, no mesmo ritmo de antes (2 s).
  if (mqtt.connected() && agora - tPubAntigo >= 2000 && distFiltrada >= 0) {
    tPubAntigo = agora;
    char v[12];
    snprintf(v, sizeof(v), "%d", nivel);            pubAntigo("nivel", v);
    snprintf(v, sizeof(v), "%.1f", distFiltrada);   pubAntigo("distancia", v);
    snprintf(v, sizeof(v), "%.1f", temperatura);    pubAntigo("temperatura", v);
  }

  if (agora - tStatus >= 10000) { tStatus = agora; imprimirStatus(); }

  atualizarBuzzer();
  supervisionarWifi();
  lerSerial();
  conectarMqtt();
  mqtt.loop();
}
