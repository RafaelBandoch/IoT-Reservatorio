#pragma once
// Conexao com o broker do projeto (Mosquitto do docker compose) por TLS,
// comum aos dois firmwares. Incluido depois do credenciais.h.
//
// Como o ESP32 acha o computador que roda o broker: o IP do computador no
// hotspot muda entre sessoes, entao ele e procurado pelo nome
// MQTT_BROKER_NOME.local (mDNS, anunciado pelo anunciar_broker.py). Se o
// mDNS nao responder, vale o IP gravado na NVS pelo comando BROKER <ip>
// do monitor serial.
//
// O certificado do broker e conferido contra a CA embutida (certificado_ca.h)
// e contra o nome MQTT_BROKER_NOME.local, mesmo quando a conexao vai por IP.
#include "credenciais.h"

#if MQTT_TLS
#include <WiFiClientSecure.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include "certificado_ca.h"   // gerado por mosquitto/gerar-certificados.sh

#define BROKER_HOST MQTT_BROKER_NOME ".local"

// O PubSubClient conecta pelo IP. Por padrao o TLS do ESP32 conferiria o
// certificado contra o proprio IP, que muda; aqui ele confere contra o nome.
class ClienteBroker : public WiFiClientSecure {
 public:
  using WiFiClientSecure::connect;
  int connect(IPAddress ip, uint16_t porta) override {
    return WiFiClientSecure::connect(ip, porta, BROKER_HOST, CA_BROKER, nullptr, nullptr);
  }
};

// ---------- IP de reserva na NVS ----------
inline IPAddress brokerSalvo() {
  Preferences p;
  p.begin("broker", true);
  uint32_t v = p.getUInt("ip", 0);
  p.end();
  return IPAddress(v);
}

inline void salvarBroker(IPAddress ip) {
  Preferences p;
  p.begin("broker", false);
  if ((uint32_t)ip == 0) p.remove("ip");
  else p.putUInt("ip", (uint32_t)ip);
  p.end();
}

// Procura o broker: mDNS primeiro, depois o IP salvo. Bloqueia no maximo
// timeoutMs (consulta mDNS), e so e chamado a cada tentativa de conexao.
// origem recebe "mDNS", "NVS" ou "nenhum".
inline IPAddress acharBroker(const char** origem, uint32_t timeoutMs = 1500) {
  IPAddress ip = MDNS.queryHost(MQTT_BROKER_NOME, timeoutMs);
  if ((uint32_t)ip != 0) { *origem = "mDNS"; return ip; }
  ip = brokerSalvo();
  *origem = (uint32_t)ip != 0 ? "NVS" : "nenhum";
  return ip;
}
#endif
