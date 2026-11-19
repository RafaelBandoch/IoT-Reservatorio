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
// 1 = broker do projeto (Mosquitto do docker compose) com TLS na 8883,
//     usuário e senha por dispositivo. O ESP32 acha o computador pelo
//     nome MQTT_BROKER_NOME.local (rode o anunciar_broker.py) e confere o
//     certificado com a CA do certificado_ca.h, gerado por
//     mosquitto/gerar-certificados.sh. Plano B: comando BROKER <ip> no
//     monitor serial grava o IP do computador na NVS.
// 0 = sem TLS na 1883, conexão anônima: simulador Wokwi ou o broker
//     público broker.hivemq.com.
#define MQTT_TLS    1

#if MQTT_TLS
  #define MQTT_BROKER_NOME "aquanivel"   // procurado como aquanivel.local
  #define MQTT_PORTA       8883
  // As mesmas senhas do .env (MQTT_SENHA_MEDIDOR e MQTT_SENHA_CONTROLADOR).
  // O usuário de cada ESP32 é o próprio deviceId.
  #define MQTT_SENHA_MEDIDOR     "troque-aqui"
  #define MQTT_SENHA_CONTROLADOR "troque-aqui"
#else
  // Onde está o broker sem TLS:
  //   simulador Wokwi -> "host.wokwi.internal"
  //   broker público  -> "broker.hivemq.com"
  #define MQTT_BROKER  "host.wokwi.internal"
  #define MQTT_PORTA   1883
#endif

// Prefixo dos tópicos. Com broker próprio não há colisão com outras
// equipes, mas o coletor e o painel assinam exatamente este prefixo.
#define MQTT_PREFIXO "catolicasc-g4/reservatorio"

// ---------- OTA (gravação pelo Wi-Fi) ----------
// Senha que o ArduinoOTA pede antes de aceitar um firmware novo. O ./ota.sh
// lê a senha daqui. Trocar a senha exige gravar a placa de novo.
#define OTA_SENHA "troque-aqui"
