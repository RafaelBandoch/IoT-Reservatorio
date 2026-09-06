#pragma once
// Configuração de rede e broker do firmware.
//
//   cp credenciais.exemplo.h credenciais.h
//
// O credenciais.h fica fora do versionamento (.gitignore). Este arquivo
// vem pronto para o broker público, então um clone novo compila e roda
// sem precisar de conta em lugar nenhum.

// ---------- Wi-Fi ----------
// "Wokwi-GUEST" com senha vazia é a rede do simulador.
#define WIFI_SSID   "Wokwi-GUEST"
#define WIFI_SENHA  ""

// ---------- broker MQTT ----------
// 1 = cluster HiveMQ Cloud: TLS na 8883, exige usuário e senha.
// 0 = broker público: sem TLS na 1883, conexão anônima.
#define MQTT_TLS    0

#if MQTT_TLS
  #define MQTT_BROKER  "seu-cluster.s1.eu.hivemq.cloud"
  #define MQTT_PORTA   8883
  #define MQTT_USUARIO "esp32"
  #define MQTT_SENHA   "troque-aqui"
#else
  #define MQTT_BROKER  "broker.hivemq.com"
  #define MQTT_PORTA   1883
#endif

// Prefixo dos tópicos. Troque pelo da sua equipe para não colidir com
// outra pessoa publicando no mesmo broker público.
#define MQTT_PREFIXO "catolicasc-g4/reservatorio"
