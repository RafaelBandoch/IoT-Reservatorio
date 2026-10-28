// ESP32 CONTROLADOR (rsv-controlador-01) do AquaNivel.
//
// Executa os comandos do backend sobre a bomba (rele no GPIO26) e aplica as
// protecoes locais da secao 5.3 do CP12:
//   BOOT -> SEGURO -> AUTOMATICO <-> MANUAL
// Nunca assina a telemetria do medidor. So ouve tres topicos:
//   .../rsv-controlador-01/cmd       comandos com commandId
//   .../backend/heartbeat            prova de vida do backend, a cada 10 s
//   .../rsv-controlador-01/config    parametros (retain), gravados na NVS
// e publica ack, estado (retain) e status (Last Will).
//
// Tudo com millis(), sem delay no loop: a protecao por tempo nao pode
// esperar a rede.
#include <WiFi.h>
#include "credenciais.h"   // copie de credenciais.exemplo.h; nao e versionado
#if MQTT_TLS
  #include <WiFiClientSecure.h>
#endif
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <time.h>
#include <stdarg.h>

#define DEVICE_ID "rsv-controlador-01"
#define FW_VERSAO "1.0.0"

// ---------- pinos ----------
// GPIO26 ALTO = bomba ligada. Em reset o pino fica em alta impedancia e o
// resistor de 10k ao GND segura o IN do rele em BAIXO, entao a bomba so liga
// quando este firmware mandar.
#define PIN_BOMBA     26
#define PIN_LED_OK    25   // verde: operando (AUTOMATICO fixo, MANUAL piscando)
#define PIN_LED_SEG   27   // vermelho: modo SEGURO

// ---------- parametros (padrao; o valor vigente vem da NVS) ----------
const uint32_t PADRAO_T_MANUAL_S = 300;   // MANUAL volta para AUTOMATICO
const uint32_t PADRAO_T_MAX_S    = 600;   // tempo maximo com a bomba ligada
const uint32_t PADRAO_HB_TO_S    = 30;    // sem heartbeat por isto -> SEGURO

// Faixas aceitas no topico config. O heartbeat chega a cada 10 s, entao um
// timeout abaixo de 15 s derrubaria o controlador a cada perda isolada.
const uint32_t T_MANUAL_MIN = 30,  T_MANUAL_MAX = 3600;
const uint32_t T_MAX_MIN    = 30,  T_MAX_MAX    = 3600;
const uint32_t HB_TO_MIN    = 15,  HB_TO_MAX    = 300;

const uint8_t HB_PARA_SAIR = 3;      // heartbeats validos seguidos para sair do SEGURO
const uint8_t N_COMANDOS   = 8;      // commandId guardados contra duplicata

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

// ---------- parametros vigentes ----------
uint32_t tManualS = PADRAO_T_MANUAL_S, tMaxS = PADRAO_T_MAX_S, hbTimeoutS = PADRAO_HB_TO_S;

// ---------- maquina de estados ----------
enum Modo { BOOT, SEGURO, AUTOMATICO, MANUAL };
Modo modo = BOOT;
// Por que entrou no SEGURO. Decide a saida: BOOT e SEM_HEARTBEAT saem
// sozinhos com heartbeats; TEMPO_MAXIMO so com o comando LIBERAR.
const char* motivoSeguro = "BOOT";

bool bombaLigada = false;
unsigned long tBombaLigou = 0;
unsigned long tUltimoManual = 0;
String ultimoCommandId = "";

// ---------- heartbeat ----------
bool hbRecebido = false;          // algum heartbeat valido desde o boot
unsigned long tUltimoHb = 0;
uint8_t hbSeguidos = 0;
bool liberaBomba = false;
String motivoHb = "SEM_LEITURA";  // motivo do ultimo liberaBomba=false
long ultimoSeqHb = -1;

// ---------- commandId ja processados ----------
String comandos[N_COMANDOS];
uint8_t idxComando = 0;

// ---------- estado da rede ----------
bool wifiConectado = false, horaSincronizada = false;
unsigned long tWifi = 0, tTentativa = 0, tQueda = 0, tSemWifi = 0, tStatus = 0, tMqtt = 0;
unsigned long esperaWifi = WIFI_ESPERA_INICIAL;
unsigned long tentativasWifi = 0, quedasWifi = 0;
unsigned long backoff = 1000;
unsigned long tLeds = 0;

// ---------- log carimbado ----------
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

// ts em ISO 8601 UTC; sem NTP, o contrato pede "relogio":"NAO_SINCRONIZADO".
void carimbarJson(JsonDocument& doc) {
  if (!horaSincronizada) { doc["relogio"] = "NAO_SINCRONIZADO"; return; }
  char ts[25];
  time_t agora = time(nullptr);
  struct tm t;
  gmtime_r(&agora, &t);
  strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &t);
  doc["ts"] = String(ts);
}

const char* nomeModo(Modo m) {
  switch (m) {
    case BOOT:       return "BOOT";
    case SEGURO:     return "SEGURO";
    case AUTOMATICO: return "AUTOMATICO";
    default:         return "MANUAL";
  }
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

const char* qualidadeRssi(int rssi) {
  if (rssi >= -60) return "boa";
  if (rssi >= -70) return "aceitavel";
  if (rssi >= -80) return "fraca";
  return "critica";
}

void imprimirStatus() {
  unsigned long agora = millis();
  char hb[24];
  if (hbRecebido) snprintf(hb, sizeof(hb), "%lus atras", (agora - tUltimoHb) / 1000);
  else            snprintf(hb, sizeof(hb), "nunca");
  logSerial("modo=%s%s%s bomba=%s%s | heartbeat %s (libera=%s, seguidos=%u) | Wi-Fi=%s IP=%s MQTT=%s",
            nomeModo(modo), modo == SEGURO ? "/" : "", modo == SEGURO ? motivoSeguro : "",
            bombaLigada ? "LIGADA" : "DESLIGADA",
            bombaLigada ? String(" ha " + String((agora - tBombaLigou) / 1000) + "s").c_str() : "",
            hb, liberaBomba ? "sim" : "nao", hbSeguidos,
            nomeStatusWifi(WiFi.status()), WiFi.localIP().toString().c_str(),
            mqtt.connected() ? "conectado" : "desconectado");
}

// ---------- wi-fi ----------
void iniciarWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(false);  // a reconexao e feita por supervisionarWifi()
  logSerial("Wi-Fi: associando ao SSID \"%s\" e pedindo endereco por DHCP...", SSID);
  WiFi.begin(SSID, SENHA);
  tTentativa = millis();
  tentativasWifi = 1;
}

void aoConectarWifi() {
  wifiConectado = true;
  esperaWifi = WIFI_ESPERA_INICIAL;
  backoff = 1000;
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
    logSerial("NTP: pedindo a hora certa");
  }
}

void registrarQueda(const char* motivo) {
  if (!wifiConectado) return;
  wifiConectado = false;
  quedasWifi++;
  tQueda = millis();
  tSemWifi = 0;
  logSerial("Wi-Fi PERDIDO (queda #%lu): %s", quedasWifi, motivo);
  mqtt.disconnect();
  esperaWifi = WIFI_ESPERA_INICIAL;
  tTentativa = millis() - esperaWifi;
}

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

// ---------- publicacao ----------
void topicoControlador(char* saida, size_t n, const char* sufixo) {
  snprintf(saida, n, "%s/%s/%s", PREFIXO, DEVICE_ID, sufixo);
}

bool publicarJson(const char* sufixo, JsonDocument& doc, bool retained = false) {
  if (!mqtt.connected()) return false;
  char topico[110], payload[400];
  topicoControlador(topico, sizeof(topico), sufixo);
  serializeJson(doc, payload, sizeof(payload));
  return mqtt.publish(topico, payload, retained);
}

void publicarEstado() {
  JsonDocument doc;
  doc["v"] = 1;
  doc["deviceId"] = DEVICE_ID;
  carimbarJson(doc);
  doc["modo"] = modo == BOOT ? "SEGURO" : nomeModo(modo);
  if (modo == SEGURO || modo == BOOT) doc["motivoSeguro"] = motivoSeguro;
  doc["bomba"] = bombaLigada ? "LIGADA" : "DESLIGADA";
  JsonObject l = doc["ligadaHa"].to<JsonObject>();
  l["valor"] = bombaLigada ? (millis() - tBombaLigou) / 1000 : 0;
  l["unidade"] = "s";
  if (ultimoCommandId.length()) doc["ultimoCommandId"] = ultimoCommandId;
  else                          doc["ultimoCommandId"] = nullptr;
  doc["fw"] = FW_VERSAO;
  publicarJson("estado", doc, true);
}

void publicarAck(const String& commandId, const char* etapa, const char* motivo) {
  JsonDocument doc;
  doc["v"] = 1;
  doc["deviceId"] = DEVICE_ID;
  doc["commandId"] = commandId;
  doc["etapa"] = etapa;
  if (motivo) doc["motivo"] = motivo; else doc["motivo"] = nullptr;
  carimbarJson(doc);
  publicarJson("ack", doc);
  logSerial("ACK %s %s%s%s", commandId.c_str(), etapa, motivo ? " motivo=" : "", motivo ? motivo : "");
}

// ---------- atuador ----------
// Unico lugar que mexe no GPIO26.
void acionarBomba(bool ligar) {
  if (ligar == bombaLigada) return;
  bombaLigada = ligar;
  digitalWrite(PIN_BOMBA, ligar ? HIGH : LOW);
  if (ligar) tBombaLigou = millis();
  logSerial("BOMBA %s (GPIO%d=%s)", ligar ? "LIGADA" : "DESLIGADA", PIN_BOMBA, ligar ? "HIGH" : "LOW");
}

void entrarSeguro(const char* motivo) {
  acionarBomba(false);
  bool mudou = modo != SEGURO || strcmp(motivoSeguro, motivo) != 0;
  modo = SEGURO;
  motivoSeguro = motivo;
  hbSeguidos = 0;
  if (mudou) {
    logSerial("MODO SEGURO (motivo=%s): bomba desligada, LIGAR sera rejeitado", motivo);
    publicarEstado();
  }
}

void mudarModo(Modo novo, bool publicar = true) {
  if (novo == modo) return;
  logSerial("modo %s -> %s", nomeModo(modo), nomeModo(novo));
  modo = novo;
  if (novo == MANUAL) tUltimoManual = millis();
  if (publicar) publicarEstado();
}

void atualizarLeds() {
  unsigned long agora = millis();
  if (agora - tLeds < 100) return;
  tLeds = agora;
  bool seguro = modo == SEGURO || modo == BOOT;
  digitalWrite(PIN_LED_SEG, seguro ? HIGH : LOW);
  bool verde = modo == AUTOMATICO || (modo == MANUAL && (agora / 500) % 2 == 0);
  digitalWrite(PIN_LED_OK, verde ? HIGH : LOW);
}

// ---------- NVS ----------
void carregarConfig() {
  nvs.begin("controlador", true);
  tManualS   = nvs.getUInt("t_manual", PADRAO_T_MANUAL_S);
  tMaxS      = nvs.getUInt("t_max", PADRAO_T_MAX_S);
  hbTimeoutS = nvs.getUInt("hb_to", PADRAO_HB_TO_S);
  nvs.end();
  logSerial("NVS: t_manual=%lus t_max_ligada=%lus hb_timeout=%lus", tManualS, tMaxS, hbTimeoutS);
}

void gravarConfig() {
  nvs.begin("controlador", false);
  nvs.putUInt("t_manual", tManualS);
  nvs.putUInt("t_max", tMaxS);
  nvs.putUInt("hb_to", hbTimeoutS);
  nvs.end();
}

// Campos ausentes mantem o valor atual; qualquer campo fora da faixa recusa
// a mensagem inteira (VALOR_FORA_FAIXA) e o anterior e mantido.
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
  uint32_t nManual = doc["t_manual_s"] | tManualS;
  uint32_t nMax    = doc["t_max_ligada_s"] | tMaxS;
  uint32_t nHb     = doc["hb_timeout_s"] | hbTimeoutS;
  bool ok = nManual >= T_MANUAL_MIN && nManual <= T_MANUAL_MAX &&
            nMax >= T_MAX_MIN && nMax <= T_MAX_MAX &&
            nHb >= HB_TO_MIN && nHb <= HB_TO_MAX;
  if (!ok) {
    logSerial("config: VALOR_FORA_FAIXA (t_manual=%lu t_max=%lu hb_timeout=%lu), mantendo a atual",
              nManual, nMax, nHb);
    return;
  }
  if (nManual == tManualS && nMax == tMaxS && nHb == hbTimeoutS) {
    logSerial("config: recebida, igual a vigente");
    return;
  }
  tManualS = nManual; tMaxS = nMax; hbTimeoutS = nHb;
  gravarConfig();
  logSerial("config: aplicada e gravada na NVS (t_manual=%lus t_max=%lus hb_timeout=%lus)",
            tManualS, tMaxS, hbTimeoutS);
}

// ---------- heartbeat ----------
void aoHeartbeat(const byte* payload, unsigned int len) {
  JsonDocument doc;
  bool valido = !deserializeJson(doc, payload, len) && doc["v"] == 1 &&
                doc["liberaBomba"].is<bool>() && doc["seq"].is<long>();
  if (!valido) {
    hbSeguidos = 0;
    logSerial("heartbeat INVALIDO (PAYLOAD_INVALIDO ou VERSAO_INCOMPATIVEL), descartado");
    return;
  }
  unsigned long agora = millis();
  // "Seguidos" quer dizer sem buraco maior que o timeout entre um e outro.
  if (hbRecebido && agora - tUltimoHb > hbTimeoutS * 1000UL) hbSeguidos = 0;
  hbRecebido = true;
  tUltimoHb = agora;
  if (hbSeguidos < 255) hbSeguidos++;
  ultimoSeqHb = doc["seq"].as<long>();
  liberaBomba = doc["liberaBomba"].as<bool>();
  const char* m = doc["motivo"] | "SEM_LEITURA";
  motivoHb = liberaBomba ? "" : m;

  if (modo == SEGURO) {
    bool saiSozinho = strcmp(motivoSeguro, "TEMPO_MAXIMO") != 0;
    logSerial("heartbeat seq=%ld libera=%s (%u/%u seguidos%s)", ultimoSeqHb,
              liberaBomba ? "sim" : "nao", hbSeguidos, HB_PARA_SAIR,
              saiSozinho ? "" : ", aguardando LIBERAR");
    if (saiSozinho && hbSeguidos >= HB_PARA_SAIR) {
      logSerial("SAINDO do SEGURO: %u heartbeats validos seguidos", hbSeguidos);
      mudarModo(AUTOMATICO);
    }
    return;
  }

  // Intertravamento: o backend tirou a liberacao com a bomba ligada.
  if (!liberaBomba && bombaLigada) {
    logSerial("heartbeat seq=%ld com liberaBomba=false (%s): desligando a bomba", ultimoSeqHb, motivoHb.c_str());
    acionarBomba(false);
    publicarEstado();
  }
}

// ---------- comandos ----------
bool comandoJaVisto(const String& id) {
  for (uint8_t i = 0; i < N_COMANDOS; i++)
    if (comandos[i].length() && comandos[i] == id) return true;
  return false;
}

void guardarComando(const String& id) {
  comandos[idxComando] = id;
  idxComando = (idxComando + 1) % N_COMANDOS;
}

void rejeitar(const String& id, const char* motivo) {
  publicarAck(id, "REJEITADO", motivo);
}

void executado(const String& id) {
  ultimoCommandId = id;
  publicarAck(id, "EXECUTADO", nullptr);
  publicarEstado();
}

void aoComando(const byte* payload, unsigned int len) {
  JsonDocument doc;
  DeserializationError erro = deserializeJson(doc, payload, len);
  String id = doc["commandId"] | "";
  if (erro || id.length() == 0) {
    // Sem commandId nao ha como responder no ack: so o log registra.
    logSerial("cmd descartado: PAYLOAD_INVALIDO (%s)", erro ? erro.c_str() : "sem commandId");
    return;
  }
  // Toda mensagem com commandId entra na lista, inclusive as rejeitadas: uma
  // reentrega do QoS 1 nunca pode executar o que antes foi recusado.
  if (comandoJaVisto(id)) { rejeitar(id, "COMANDO_DUPLICADO"); return; }
  guardarComando(id);

  if (doc["v"] != 1) { rejeitar(id, "VERSAO_INCOMPATIVEL"); return; }
  String acao = doc["acao"] | "";
  String origem = doc["origem"] | "";
  if (origem != "regra" && origem != "manual") { rejeitar(id, "PAYLOAD_INVALIDO"); return; }
  if (acao != "LIGAR" && acao != "DESLIGAR" && acao != "MODO_AUTO" && acao != "LIBERAR") {
    rejeitar(id, "COMANDO_DESCONHECIDO");
    return;
  }
  bool manual = origem == "manual";
  logSerial("cmd %s: acao=%s origem=%s (modo=%s)", id.c_str(), acao.c_str(), origem.c_str(), nomeModo(modo));

  // O estado sai uma vez so, em executado(), ja com modo e bomba novos.
  if (acao == "DESLIGAR") {
    // Desligar e sempre aceito, em qualquer modo. Manual no AUTOMATICO
    // passa para MANUAL; a regra no MANUAL desliga sem mexer no timer.
    publicarAck(id, "RECEBIDO", nullptr);
    if (manual && modo == AUTOMATICO) mudarModo(MANUAL, false);
    if (manual && modo == MANUAL) tUltimoManual = millis();
    acionarBomba(false);
    executado(id);
    return;
  }

  if (acao == "LIGAR") {
    if (modo == SEGURO || modo == BOOT) { rejeitar(id, "MODO_SEGURO"); return; }
    if (!liberaBomba) { rejeitar(id, motivoHb == "NIVEL_ALTO" ? "NIVEL_ALTO" : "SEM_LEITURA"); return; }
    // A regra do backend nao passa por cima do usuario durante o MANUAL.
    if (modo == MANUAL && !manual) { rejeitar(id, "MODO_MANUAL"); return; }
    publicarAck(id, "RECEBIDO", nullptr);
    if (manual) { mudarModo(MANUAL, false); tUltimoManual = millis(); }
    acionarBomba(true);
    executado(id);
    return;
  }

  if (acao == "MODO_AUTO") {
    if (modo == SEGURO || modo == BOOT) { rejeitar(id, "MODO_SEGURO"); return; }
    publicarAck(id, "RECEBIDO", nullptr);
    mudarModo(AUTOMATICO, false);
    executado(id);
    return;
  }

  // LIBERAR: so tira do SEGURO quem entrou por TEMPO_MAXIMO. Os outros
  // motivos saem sozinhos com heartbeats e nao podem ser pulados.
  if (modo == SEGURO && strcmp(motivoSeguro, "TEMPO_MAXIMO") != 0) { rejeitar(id, "MODO_SEGURO"); return; }
  publicarAck(id, "RECEBIDO", nullptr);
  if (modo == SEGURO) {
    logSerial("LIBERAR: saindo do SEGURO por TEMPO_MAXIMO");
    mudarModo(AUTOMATICO, false);
  }
  executado(id);
}

// ---------- protecoes por tempo ----------
void supervisionar() {
  unsigned long agora = millis();
  if (modo != AUTOMATICO && modo != MANUAL) return;

  // Sem heartbeat: o relogio corre mesmo sem Wi-Fi, entao a bomba desliga
  // tambem quando a rede do controlador cai.
  if (!hbRecebido || agora - tUltimoHb > hbTimeoutS * 1000UL) {
    logSerial("sem heartbeat ha mais de %lus", hbTimeoutS);
    entrarSeguro("SEM_HEARTBEAT");
    return;
  }
  if (bombaLigada && agora - tBombaLigou >= tMaxS * 1000UL) {
    logSerial("bomba ligada ha %lus, limite de %lus", (agora - tBombaLigou) / 1000, tMaxS);
    entrarSeguro("TEMPO_MAXIMO");
    return;
  }
  if (modo == MANUAL && agora - tUltimoManual >= tManualS * 1000UL) {
    logSerial("MANUAL sem comando ha %lus, voltando ao AUTOMATICO", tManualS);
    mudarModo(AUTOMATICO);
  }
}

// ---------- comandos digitados no monitor serial ----------
void processarComandoSerial(String c) {
  c.trim(); c.toUpperCase();
  if (c == "STATUS") {
    imprimirStatus();
  } else if (c == "QUEDA") {
    logSerial("COMANDO \"QUEDA\": derrubando o Wi-Fi de proposito para testar a reconexao");
    registrarQueda("desconexao provocada pelo comando QUEDA no monitor serial");
    WiFi.disconnect(false, false);
  } else {
    logSerial("comando \"%s\" desconhecido: use STATUS ou QUEDA (a bomba so obedece ao backend)", c.c_str());
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
    } else if (c == 8 || c == 127) {
      if (linha.length()) {
        linha.remove(linha.length() - 1);
        Serial.print("\b \b");
      }
    } else if (linha.length() < 32) {
      linha += c;
      Serial.write(c);
    }
  }
}

// ---------- MQTT ----------
char topicoCmd[110], topicoConfig[110], topicoHb[110];

void aoReceber(char* topico, byte* payload, unsigned int len) {
  if (strcmp(topico, topicoHb) == 0)          aoHeartbeat(payload, len);
  else if (strcmp(topico, topicoCmd) == 0)    aoComando(payload, len);
  else if (strcmp(topico, topicoConfig) == 0) aplicarConfig(payload, len);
}

void conectarMqtt() {
  if (mqtt.connected() || !wifiConectado) return;
  if (millis() - tMqtt < backoff) return;
  tMqtt = millis();
  char id[48], lwt[110], payloadLwt[80];
  snprintf(id, sizeof(id), "catolicasc-g4-%s-%06X", DEVICE_ID, (uint32_t)ESP.getEfuseMac());
  topicoControlador(lwt, sizeof(lwt), "status");
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
    publicarEstado();
    mqtt.subscribe(topicoCmd, 1);
    mqtt.subscribe(topicoConfig, 1);
    mqtt.subscribe(topicoHb, 0);
    logSerial("MQTT conectado ao broker %s:%d como %s", BROKER, PORTA, id);
    logSerial("assinando: %s | %s | %s", topicoCmd, topicoHb, topicoConfig);
  } else {
    backoff = min(backoff * 2, 30000UL);
    logSerial("MQTT: falha ao conectar em %s:%d (rc=%d), nova tentativa em %lu s",
              BROKER, PORTA, mqtt.state(), backoff / 1000);
  }
}

void setup() {
  // Primeira coisa: bomba desligada. Nada antes disto pode atrasar o LOW.
  pinMode(PIN_BOMBA, OUTPUT);
  digitalWrite(PIN_BOMBA, LOW);
  pinMode(PIN_LED_OK, OUTPUT);  digitalWrite(PIN_LED_OK, LOW);
  pinMode(PIN_LED_SEG, OUTPUT); digitalWrite(PIN_LED_SEG, HIGH);

  Serial.begin(115200);
  delay(300);   // so no setup: da tempo do monitor serial engatar
  Serial.println();
  logSerial("===== AquaNivel CONTROLADOR %s (fw %s) =====", DEVICE_ID, FW_VERSAO);
  logSerial("BOOT: GPIO%d em LOW, bomba desligada", PIN_BOMBA);
  logSerial("Comandos do monitor serial: STATUS | QUEDA");

  carregarConfig();
  snprintf(topicoCmd, sizeof(topicoCmd), "%s/%s/cmd", PREFIXO, DEVICE_ID);
  snprintf(topicoConfig, sizeof(topicoConfig), "%s/%s/config", PREFIXO, DEVICE_ID);
  snprintf(topicoHb, sizeof(topicoHb), "%s/backend/heartbeat", PREFIXO);

  entrarSeguro("BOOT");
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
  supervisionar();
  atualizarLeds();
  if (agora - tStatus >= 10000) { tStatus = agora; imprimirStatus(); }
  supervisionarWifi();
  lerSerial();
  conectarMqtt();
  mqtt.loop();
}
