#include <WiFi.h>
#include "credenciais.h"   // copie de credenciais.exemplo.h; nao e versionado
#if MQTT_TLS
  #include <WiFiClientSecure.h>
#endif
#include <PubSubClient.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <time.h>
#include <stdarg.h>

// ---------- pinos ----------
// O HC-SR04 e alimentado com 5V, entao o ECHO responde em 5V. Nenhum pino do
// ESP32 e 5V-tolerante -- o datasheet poe o teto da entrada em VDD+0,3V -- por
// isso o ECHO nao entra direto: chega no GPIO34 pelo meio de um divisor
// resistivo de 1k/1k (linha 40 -> R -> linha 46 -> R -> linha 51 no GND, com
// o GPIO34 ligado na linha 46, o meio do divisor), que entrega 5 * 1/(1+1) =
// 2,5 V. O GPIO34 e entrada pura, sem pull-up nem pull-down interno, entao
// nada disputa com o divisor.
#define PIN_TRIG    33
#define PIN_ECHO    34
#define PIN_TEMP    4
#define PIN_LED     26
#define PIN_BUZZER  27

// ---------- display no barramento I2C ----------
// Sao so quatro fios: alimentacao, terra e o par do barramento I2C. O ESP32
// nao tem os pinos de I2C fixos em hardware, mas 21 e 22 sao os que a
// biblioteca Wire assume por padrao -- e sao os usados nesta montagem.
#define I2C_SDA     21
#define I2C_SCL     22
#define LCD_COLUNAS 16
#define LCD_LINHAS  2

// ---------- calibracao ----------
// Calibrado para o recipiente real usado nos testes: 23,5cm de altura.
// D_VAZIO e a distancia do sensor ate o fundo seco. D_CHEIO fica em 4cm
// em vez de 0 de proposito -- o HC-SR04 tem uma zona morta de ~2cm onde
// nao enxerga nada, entao 4cm da uma margem de seguranca sem cair nessa
// zona mesmo com a agua balancando.
const float D_VAZIO  = 23.5;   // cm do sensor ate o fundo
const float D_CHEIO  = 4.0;    // cm do sensor ate o nivel maximo
const float ALFA_EMA = 0.30;

// ---------- limiares ----------
const int NIVEL_LIGA     = 10;
const int NIVEL_DESLIGA  = 90;
const int NIVEL_BAIXO    = 15;
const int NIVEL_CRITICO  = 95;

// ---------- rede ----------
// Todos os valores vem de credenciais.h, que fica fora do versionamento.
const char* SSID    = WIFI_SSID;
const char* SENHA   = WIFI_SENHA;
const char* BROKER  = MQTT_BROKER;
const int   PORTA   = MQTT_PORTA;
const char* PREFIXO = MQTT_PREFIXO;

// ---------- wi-fi: reconexao e relogio ----------
// Espera entre tentativas de reconexao. Dobra a cada falha ate o teto para
// nao inundar o ar com pedidos de associacao enquanto o AP esta fora.
const unsigned long WIFI_ESPERA_INICIAL = 5000;
const unsigned long WIFI_ESPERA_MAXIMA  = 30000;
const long NTP_FUSO = -3 * 3600;   // horario de Brasilia (UTC-3)

// Com TLS o cluster exige WiFiClientSecure e autenticacao. Sem TLS, o
// broker publico aceita conexao anonima por WiFiClient.
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
LiquidCrystal_I2C* lcd = nullptr;   // so nasce se um LCD responder
// SEM_TELA enquanto ninguem respondeu: o firmware segue sem display, porque
// medicao, bomba e MQTT nao dependem dele.
enum TipoTela { SEM_TELA, TELA_LCD };
TipoTela tipoTela = SEM_TELA;
OneWire fio(PIN_TEMP);
DallasTemperature sensorTemp(&fio);

// ---------- estado ----------
float buf[5]; uint8_t bufIdx = 0; bool bufCheio = false;
float distFiltrada = -1, temperatura = 25.0;
unsigned long falhasSensor = 0;   // leituras invalidas seguidas do HC-SR04
unsigned long falhasTemp = 0;      // leituras invalidas seguidas do DS18B20
int nivel = 0;
bool bombaLigada = false, origemAuto = false, inibido = false;
String alerta = "NORMAL";
unsigned long tLeitura = 0, tTela = 0, tPub = 0, tTemp = 0, tMqtt = 0;
unsigned long backoff = 1000;

// ---------- estado da rede ----------
bool wifiConectado = false, horaSincronizada = false;
unsigned long tWifi = 0, tTentativa = 0, tQueda = 0, tSemWifi = 0, tStatus = 0;
unsigned long esperaWifi = WIFI_ESPERA_INICIAL;
unsigned long tentativasWifi = 0, quedasWifi = 0;

// ---------- log carimbado ----------
// Toda linha sai com [data hora | uptime] na frente. E isso que transforma o
// print do monitor serial em evidencia: da para conferir a hora do log com a
// hora do relogio do computador na mesma tela.
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
  char msg[220];
  va_list args;
  va_start(args, fmt);
  vsnprintf(msg, sizeof(msg), fmt, args);
  va_end(args);
  Serial.print(ts); Serial.print(' '); Serial.println(msg);
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
// mais forte o sinal que chega na antena.
const char* qualidadeRssi(int rssi) {
  if (rssi >= -60) return "boa";
  if (rssi >= -70) return "aceitavel";
  if (rssi >= -80) return "fraca";
  return "critica";
}

void imprimirStatus() {
  wl_status_t s = WiFi.status();
  if (s == WL_CONNECTED) {
    int rssi = WiFi.RSSI();
    logSerial("ATIVO -- IP=%s RSSI=%d dBm (%s) | MQTT=%s | nivel=%d%% temp=%.1fC bomba=%s | quedas=%lu",
              WiFi.localIP().toString().c_str(), rssi, qualidadeRssi(rssi),
              mqtt.connected() ? "conectado" : "desconectado",
              nivel, temperatura, estadoBomba(), quedasWifi);
  } else {
    logSerial("OFFLINE -- status=%s | tentativas=%lu | quedas=%lu",
              nomeStatusWifi(s), tentativasWifi, quedasWifi);
  }
}

void iniciarWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);        // nao regrava as credenciais na flash a cada boot
  WiFi.setAutoReconnect(false);  // a reconexao e feita por supervisionarWifi(), logo
                                 // abaixo: um mecanismo so, e com log de cada passo
  logSerial("Wi-Fi: associando ao SSID \"%s\" e pedindo endereco por DHCP...", SSID);
  WiFi.begin(SSID, SENHA);
  tTentativa = millis();
  tentativasWifi = 1;
}

void aoConectarWifi() {
  wifiConectado = true;
  esperaWifi = WIFI_ESPERA_INICIAL;
  backoff = 1000;   // solta a espera do MQTT: com rede de volta ele tenta na hora
  tMqtt = 0;
  int rssi = WiFi.RSSI();
  logSerial("Wi-Fi CONECTADO -- SSID=%s canal=%d RSSI=%d dBm (%s)",
            WiFi.SSID().c_str(), WiFi.channel(), rssi, qualidadeRssi(rssi));
  logSerial("DHCP entregou: IP=%s mascara=%s gateway=%s DNS=%s",
            WiFi.localIP().toString().c_str(), WiFi.subnetMask().toString().c_str(),
            WiFi.gatewayIP().toString().c_str(), WiFi.dnsIP().toString().c_str());
  if (tQueda) {
    logSerial("RECONEXAO AUTOMATICA concluida em %.1f s apos a queda #%lu, "
              "sem mexer no codigo e sem upload novo",
              (millis() - tQueda) / 1000.0, quedasWifi);
    tQueda = 0;
  }
  if (!horaSincronizada) {
    configTime(NTP_FUSO, 0, "pool.ntp.org", "a.st1.ntp.br");
    logSerial("NTP: pedindo a hora certa para carimbar as proximas linhas");
  }
}

// Um caminho unico para registrar a perda, venha ela de uma queda real ou do
// comando QUEDA digitado no monitor serial.
void registrarQueda(const char* motivo) {
  if (!wifiConectado) return;
  wifiConectado = false;
  quedasWifi++;
  tQueda = millis();
  tSemWifi = 0;
  logSerial("Wi-Fi PERDIDO (queda #%lu) -- %s", quedasWifi, motivo);
  mqtt.disconnect();   // sem rede o MQTT so acumularia timeout; ele volta depois
  esperaWifi = WIFI_ESPERA_INICIAL;
  tTentativa = millis() - esperaWifi;   // primeira tentativa sai imediatamente
}

// Esta e a logica que detecta e reage a uma queda de Wi-Fi. Roda a cada 250 ms
// dentro do loop(), sem bloquear: o sensor, a tela e a bomba continuam
// funcionando enquanto a rede nao volta.
void supervisionarWifi() {
  unsigned long agora = millis();
  if (agora - tWifi < 250) return;
  tWifi = agora;

  wl_status_t s = WiFi.status();

  if (s == WL_CONNECTED) {
    if (!wifiConectado) aoConectarWifi();
    if (!horaSincronizada && time(nullptr) > 1700000000) {
      horaSincronizada = true;
      logSerial("NTP: relogio sincronizado -- as linhas acima tinham so o uptime");
    }
    return;
  }

  if (wifiConectado) {
    char motivo[80];
    snprintf(motivo, sizeof(motivo), "queda detectada pelo firmware (status=%s)", nomeStatusWifi(s));
    registrarQueda(motivo);
  }

  // Na queda, uma linha por segundo deixa a interrupcao visivel no print.
  // No boot, a cada 2 s, para nao empurrar o DHCP para fora da tela.
  if (agora - tSemWifi >= (tQueda ? 1000UL : 2000UL)) {
    tSemWifi = agora;
    if (tQueda) logSerial("sem Wi-Fi ha %.1f s (status=%s) -- o ESP32 esta tentando voltar sozinho",
                          (agora - tQueda) / 1000.0, nomeStatusWifi(s));
    else        logSerial("ainda sem IP (status=%s) -- aguardando o Wi-Fi subir", nomeStatusWifi(s));
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
const uint16_t PAD_CONCLUIDO[] = {300, 200, 300, 200, 300, 0};
const uint16_t PAD_ALTO[]      = {800, 400, 0};
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

// ---------- medicao ----------
float lerDistancia() {
  float v = 331.4 + 0.606 * temperatura;      // m/s
  float cmPorUs = v / 10000.0;
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(4);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long dur = pulseIn(PIN_ECHO, HIGH, 30000UL);
  if (dur == 0) return -1.0;
  float d = (dur * cmPorUs) / 2.0;

  // O HC-SR04 nao enxerga nada abaixo de uns 2 cm: e o tempo que o proprio
  // modulo gasta entre disparar e ouvir. Valor menor que isso e pulso espurio
  // de pino flutuante -- sensor sem alimentacao ou fio do ECHO solto -- e nao
  // pode virar "reservatorio cheio", que era o que acontecia antes.
  if (d < 2.0) return -1.0;
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
  if (d < 0 || d > 450) {
    // Uma falha isolada e normal (eco perdido). Muitas seguidas sao fiacao:
    // o log diz de quanto tempo e como esta o ECHO em repouso, para separar
    // "sensor sem alimentacao" de "fio solto".
    falhasSensor++;
    if (falhasSensor % 50 == 0)
      logSerial("HC-SR04: %lu leituras invalidas seguidas (ECHO em repouso=%d) -- confira "
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
  float pct = (D_VAZIO - distFiltrada) / (D_VAZIO - D_CHEIO) * 100.0;
  nivel = constrain((int)round(pct), 0, 100);
}

// ---------- publicacao ----------
void pub(const char* sufixo, const char* payload, bool retained = false) {
  if (!mqtt.connected()) return;
  char topico[100];
  snprintf(topico, sizeof(topico), "%s/%s", PREFIXO, sufixo);
  mqtt.publish(topico, payload, retained);
}

void publicarBomba() {
  const char* v = !bombaLigada ? "DESLIGADA" : (origemAuto ? "LIGADA:AUTO" : "LIGADA:MANUAL");
  pub("bomba", v, true);
}

// Usado no log serial e no LCD -- os dois mostram a mesma coisa, so que um
// preso a formatacao de linha unica e o outro a 16 colunas.
const char* estadoBomba() {
  if (!bombaLigada) return "PARADA";
  return origemAuto ? "AUTO" : "MANUAL";
}

void definirAlerta(String novo) {
  if (novo == alerta) return;
  alerta = novo;
  pub("alerta/nivel", alerta.c_str());
}

// ---------- logica da bomba ----------
void ligar(bool automatico) {
  if (bombaLigada) return;
  bombaLigada = true; origemAuto = automatico;
  digitalWrite(PIN_LED, HIGH);
  publicarBomba();
  if (automatico) definirAlerta("ACIONAMENTO_AUTOMATICO");
}

void parar(bool manual) {
  if (!bombaLigada) return;
  bombaLigada = false;
  digitalWrite(PIN_LED, LOW);
  if (manual && nivel <= NIVEL_LIGA) inibido = true;
  publicarBomba();
  if (!manual) { tocar(PAD_CONCLUIDO, false); definirAlerta("ENCHIMENTO_CONCLUIDO"); }
}

// Recebe o nivel de volta pelo topico MQTT (aoReceber), nunca a variavel
// local direto: a decisao automatica so pode agir sobre uma leitura que ja
// saiu para a rede, entao o disparo e sempre a mensagem que o proprio ESP32
// assina de volta, nao o calculo que medir() acabou de fazer.
void controlar(int nivelRecebido) {
  if (nivelRecebido > NIVEL_BAIXO) inibido = false;
  if (!bombaLigada && nivelRecebido <= NIVEL_LIGA && !inibido) ligar(true);
  else if (bombaLigada && nivelRecebido >= NIVEL_DESLIGA) parar(false);

  if (nivelRecebido >= NIVEL_CRITICO) { definirAlerta("CRITICO_ALTO"); tocar(PAD_ALTO, true); }
  else {
    if (padRepete) pararBuzzer();
    if (nivelRecebido < NIVEL_BAIXO) definirAlerta("NIVEL_BAIXO");
    else if (alerta == "CRITICO_ALTO" || alerta == "NIVEL_BAIXO") definirAlerta("NORMAL");
  }
}

// ---------- barramento I2C ----------
// Varre o barramento inteiro e escreve no log quem respondeu. Roda no boot e
// tambem pelo comando "I2C" do monitor serial: um display mudo nao diz, por
// si so, se o problema e endereco, fio trocado ou falta de alimentacao --
// esta varredura diz. Devolve o endereco do modulo do LCD (0x27 ou 0x3F)
// quando aparece, senao o primeiro que responder.
// Le o nivel eletrico das duas linhas com os pull-ups internos do ESP32
// desligados. Em repouso, um modulo alimentado segura as duas em nivel alto
// pelos proprios resistores -- e isso separa "modulo mudo" de "modulo que
// nem chegou a ser alimentado", sem precisar de multimetro.
void diagnosticarLinhasI2C() {
  pinMode(I2C_SDA, INPUT);
  pinMode(I2C_SCL, INPUT);
  delayMicroseconds(200);
  int sdaSolto = digitalRead(I2C_SDA), sclSolto = digitalRead(I2C_SCL);

  // Segunda leitura com o resistor interno do ESP32 puxando para 3,3 V. Se a
  // linha continuar em 0, ela esta presa no GND -- fio no trilho errado, ou
  // modulo com VCC e GND invertidos, que faz os pull-ups dele puxarem para o
  // terra. Se subir para 1, a linha estava apenas solta.
  pinMode(I2C_SDA, INPUT_PULLUP);
  pinMode(I2C_SCL, INPUT_PULLUP);
  delayMicroseconds(400);
  int sdaPuxado = digitalRead(I2C_SDA), sclPuxado = digitalRead(I2C_SCL);

  logSerial("I2C: sem pull-up SDA(D%d)=%d SCL(D%d)=%d | com pull-up interno SDA=%d SCL=%d",
            I2C_SDA, sdaSolto, I2C_SCL, sclSolto, sdaPuxado, sclPuxado);
  logSerial("I2C: 1/1 sem pull-up = modulo alimentado | 0 que vira 1 = linha solta | "
            "0 que continua 0 = presa no GND");
  Wire.begin(I2C_SDA, I2C_SCL);   // devolve os pinos ao barramento
}

// Varre o barramento com um par de pinos escolhido. Deixa o Wire configurado
// com esse par, entao quem achar um endereco ja pode falar com o display.
uint8_t varrerBarramento(const char* quando, uint8_t pinoSda, uint8_t pinoScl) {
  Wire.begin(pinoSda, pinoScl);
  uint8_t preferido = 0, primeiro = 0;
  for (uint8_t ender = 0x08; ender < 0x78; ender++) {
    Wire.beginTransmission(ender);
    if (Wire.endTransmission() != 0) continue;
    if (!primeiro) primeiro = ender;
    // Endereco de display conhecido: o modulo I2C do LCD 16x2, que costuma
    // vir em 0x27 ou 0x3F. Qualquer outro so serve de ultimo recurso.
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

  // Ninguem respondeu na ordem normal. Antes de desistir, tenta com os dois
  // fios trocados: e o erro de montagem mais comum e o barramento nao da
  // nenhum sinal dele -- as duas linhas ficam em nivel alto do mesmo jeito,
  // so que conversa nenhuma acontece.
  ender = varrerBarramento(quando, I2C_SCL, I2C_SDA);
  if (ender) {
    logSerial("I2C: respondeu com os fios TROCADOS -- hoje o SDA esta no D%d e o SCL no D%d. "
              "Funciona assim; o certo seria SDA no D%d e SCL no D%d",
              I2C_SCL, I2C_SDA, I2C_SDA, I2C_SCL);
    return ender;
  }

  logSerial("I2C (%s): ninguem respondeu nas duas ordens de fio -- confira VCC, GND, "
            "SDA no D%d e SCL no D%d, ou o modulo pode estar danificado",
            quando, I2C_SDA, I2C_SCL);
  Wire.begin(I2C_SDA, I2C_SCL);   // volta ao padrao
  return 0;
}

// So existe o LCD 16x2 nesta montagem, entao qualquer endereco que a
// varredura encontrar vira ele -- normalmente 0x27 ou 0x3F.
void iniciarTela(uint8_t ender) {
  lcd = new LiquidCrystal_I2C(ender, LCD_COLUNAS, LCD_LINHAS);
  lcd->init();
  lcd->backlight();
  lcd->setCursor(0, 0); lcd->print("Reservatorio");
  lcd->setCursor(0, 1); lcd->print("Iniciando...");
  tipoTela = TELA_LCD;
  logSerial("LCD 16x2 em uso no endereco 0x%02X", ender);
}

// ---------- comandos ----------
void aplicarComando(String c) {
  c.trim(); c.toUpperCase();
  if (c == "LIGAR")      { ligar(false); pub("comando/bomba/confirmacao", "LIGAR:OK"); }
  else if (c == "PARAR") { parar(true);  pub("comando/bomba/confirmacao", "PARAR:OK"); }
  else                     pub("comando/bomba/confirmacao", "ERRO:comando_invalido");
}

void aoReceber(char* topico, byte* payload, unsigned int len) {
  String msg; for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  String t = topico;
  // Este e o proprio nivel que o ESP32 publicou, assinado de volta: e ele
  // quem dispara a decisao automatica, nao o valor recem-calculado por
  // medir(). Ve "controlar()" para o porque.
  if (t.endsWith("/nivel")) controlar(msg.toInt());
  else                      aplicarComando(msg);
}

// ---------- comandos digitados no monitor serial ----------
// QUEDA existe para a evidencia de reconexao: derruba o Wi-Fi de proposito sem
// editar o codigo e sem upload novo, entao o que acontece depois e do firmware.
void processarComandoSerial(String c) {
  c.trim(); c.toUpperCase();
  if (c == "QUEDA") {
    logSerial("COMANDO \"QUEDA\": derrubando o Wi-Fi de proposito para testar a reconexao");
    registrarQueda("desconexao provocada pelo comando QUEDA no monitor serial");
    WiFi.disconnect(false, false);   // so desassocia; nao apaga as credenciais
  } else if (c == "STATUS") {
    imprimirStatus();
  } else if (c == "LIGAR" || c == "PARAR") {
    aplicarComando(c);
    logSerial("COMANDO \"%s\" aplicado pelo monitor serial", c.c_str());
  } else if (c == "I2C") {
    // Varre de novo sem regravar: util para conferir a ligacao do LCD com a
    // placa em bancada, mexendo nos fios e repetindo o comando.
    uint8_t ender = varrerI2C("comando");
    if (ender && tipoTela == SEM_TELA) iniciarTela(ender);
  } else {
    logSerial("comando \"%s\" desconhecido -- use QUEDA, STATUS, LIGAR, PARAR ou I2C", c.c_str());
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
      Serial.write(c);   // eco: o terminal do Wokwi no VS Code nao ecoa sozinho,
                         // entao sem isto o comando digitado nao sai no print
    }
  }
}

// ---------- conexao ----------
void conectarMqtt() {
  // Usa o estado que supervisionarWifi() publica, e nao WiFi.status() direto:
  // assim a linha do MQTT nunca aparece antes das linhas de Wi-Fi e DHCP.
  if (mqtt.connected() || !wifiConectado) return;
  if (millis() - tMqtt < backoff) return;
  tMqtt = millis();
  char id[40], lwt[110];
  snprintf(id, sizeof(id), "esp32-reserv-%06X", (uint32_t)ESP.getEfuseMac());
  snprintf(lwt, sizeof(lwt), "%s/status", PREFIXO);
  if (mqtt.connect(id, MQTT_USER, MQTT_PASS, lwt, 1, true, "offline")) {
    backoff = 1000;
    pub("status", "online", true);
    publicarBomba();
    char sub[110];
    snprintf(sub, sizeof(sub), "%s/comando/bomba", PREFIXO);
    mqtt.subscribe(sub, 1);
    // Tambem assina o proprio nivel: e essa mensagem, assinada de volta
    // depois de ter saido para a rede, que aciona a decisao automatica
    // (controlar(), chamado a partir de aoReceber()).
    snprintf(sub, sizeof(sub), "%s/nivel", PREFIXO);
    mqtt.subscribe(sub, 0);
    logSerial("MQTT conectado ao broker %s:%d como %s", BROKER, PORTA, id);
  } else {
    backoff = min(backoff * 2, 30000UL);
    logSerial("MQTT: falha ao conectar em %s:%d (rc=%d) -- nova tentativa em %lu s",
              BROKER, PORTA, mqtt.state(), backoff / 1000);
  }
}

// ---------- tela ----------
// No LCD 16x2 cabem 32 caracteres. Cada linha e montada inteira com snprintf
// e escrita por cima da anterior: o preenchimento ate as 16 colunas apaga o
// que sobrou da linha antiga, sem precisar de clear() -- que faria piscar.
void desenharLcd() {
  char l1[17], l2[17];
  snprintf(l1, sizeof(l1), "Nivel %3d%% %4.1fC", nivel, temperatura);
  if (alerta == "NORMAL")
    snprintf(l2, sizeof(l2), "Bomba %-6s %s", estadoBomba(), wifiConectado ? "NET" : "---");
  else
    snprintf(l2, sizeof(l2), "%-16.16s", alerta.c_str());
  lcd->setCursor(0, 0); lcd->print(l1);
  lcd->setCursor(0, 1); lcd->print(l2);
}

// Sem display, nao faz nada: medicao, bomba e MQTT seguem iguais.
void desenharTela() {
  if (tipoTela == TELA_LCD) desenharLcd();
}

void setup() {
  Serial.begin(115200);
  delay(300);   // da tempo do monitor serial engatar antes do cabecalho
  Serial.println();
  logSerial("===== Reservatorio IoT -- firmware iniciado =====");
  logSerial("Dispositivo esp32-reserv-%06X | topico base: %s",
            (uint32_t)ESP.getEfuseMac(), PREFIXO);
  logSerial("Comandos do monitor serial: QUEDA | STATUS | LIGAR | PARAR | I2C");
  pinMode(PIN_TRIG, OUTPUT); pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_LED, OUTPUT);  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_LED, LOW); digitalWrite(PIN_BUZZER, LOW);

  // Quem responder no barramento vira o display. Sem resposta, o firmware
  // segue sem tela -- medicao, bomba e MQTT nao dependem dela -- e a
  // varredura pode ser repetida a qualquer momento pelo comando "I2C".
  Wire.begin(I2C_SDA, I2C_SCL);
  uint8_t ender = varrerI2C("boot");
  if (ender) iniciarTela(ender);
  else logSerial("display: seguindo sem tela -- corrija a ligacao e digite I2C no monitor");

  sensorTemp.begin();
  iniciarWifi();

#if MQTT_TLS
  // Aceita o certificado do broker sem validar a cadeia. Suficiente
  // para o trabalho e evita embutir o CA raiz no firmware; em
  // producao o certo seria net.setCACert(...) com o CA do cluster.
  net.setInsecure();
#endif

  mqtt.setServer(BROKER, PORTA);
  mqtt.setCallback(aoReceber);
}

void loop() {
  unsigned long agora = millis();

  // So mede e filtra aqui -- rapido, a cada 100ms, pra media movel ficar
  // estavel. A decisao (controlar()) nao roda neste bloco: ela so dispara
  // em aoReceber(), quando o nivel publicado abaixo volta pelo topico.
  if (agora - tLeitura > 100) { tLeitura = agora; medir(); }

  if (agora - tTemp > 3000) {
    tTemp = agora;
    sensorTemp.requestTemperatures();
    float t = sensorTemp.getTempCByIndex(0);
    if (t > -50 && t < 90) {
      temperatura = t;
      falhasTemp = 0;
    } else {
      falhasTemp++;
      if (falhasTemp % 5 == 0)
        logSerial("DS18B20: %lu leituras invalidas seguidas (t=%.1f, %d sensor(es) no barramento) -- "
                  "confira VCC, GND, DATA no D%d e o pull-up de 4.7k",
                  falhasTemp, t, sensorTemp.getDeviceCount(), PIN_TEMP);
    }
  }

  if (agora - tTela > 500) { tTela = agora; desenharTela(); }

  if (agora - tPub > 2000 && mqtt.connected()) {
    tPub = agora;
    char v[12];
    snprintf(v, sizeof(v), "%d", nivel);            pub("nivel", v);
    snprintf(v, sizeof(v), "%.1f", distFiltrada);   pub("distancia", v);
    snprintf(v, sizeof(v), "%.1f", temperatura);    pub("temperatura", v);
  }

  // Batimento de vida no serial: prova que a conexao segue de pe entre um
  // evento e outro, mesmo quando nada mais esta acontecendo.
  if (agora - tStatus > 5000) {
    tStatus = agora;
    if (wifiConectado) imprimirStatus();
  }

  atualizarBuzzer();
  supervisionarWifi();
  lerSerial();
  conectarMqtt();
  mqtt.loop();
}