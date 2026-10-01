#pragma once
// Configuração de rede e broker do firmware.
//
//   cp credenciais.exemplo.h credenciais.h
//
// O credenciais.h fica fora do versionamento (.gitignore). Este arquivo
// vem pronto para o Mosquitto do docker compose rodando no computador,
// visto de dentro do simulador.

// ---------- Wi-Fi ----------
// "Wokwi-GUEST" com senha vazia é a rede do simulador. Na placa real,
// use a rede de verdade — sempre 2,4 GHz, o ESP32 não enxerga 5 GHz.
#define WIFI_SSID   "Wokwi-GUEST"
#define WIFI_SENHA  ""

// ---------- broker MQTT ----------
// 1 = cluster HiveMQ Cloud: TLS na 8883, exige usuário e senha.
// 0 = sem TLS na 1883, conexão anônima: o Mosquitto do docker compose
//     (padrão) ou o broker público broker.hivemq.com.
#define MQTT_TLS    0

#if MQTT_TLS
  #define MQTT_BROKER  "seu-cluster.s1.eu.hivemq.cloud"
  #define MQTT_PORTA   8883
  #define MQTT_USUARIO "esp32"
  #define MQTT_SENHA   "troque-aqui"
#else
  // Onde está o Mosquitto do docker compose:
  //   simulador Wokwi -> "host.wokwi.internal" (é o computador que roda o Wokwi)
  //   placa real      -> IP do Mac na mesma rede Wi-Fi, ex. "192.168.1.112"
  //                      (descubra com: ipconfig getifaddr en0)
  #define MQTT_BROKER  "host.wokwi.internal"
  #define MQTT_PORTA   1883
#endif

// Prefixo dos tópicos. Com broker próprio não há colisão com outras
// equipes, mas o coletor e o painel assinam exatamente este prefixo.
#define MQTT_PREFIXO "catolicasc-g4/reservatorio"
