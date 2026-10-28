#!/bin/bash
# Compila o firmware localmente para a pasta build/.
# O arduino-cli exige que o .ino esteja numa pasta de mesmo nome, por isso
# a cópia para um diretório temporário — o credenciais.h vai junto.
#
#   ./compilar.sh
#
# A escolha entre cluster HiveMQ (TLS) e broker público está no
# credenciais.h, na constante MQTT_TLS. Esse arquivo não é versionado.
set -e
cd "$(dirname "$0")"

if [ ! -f credenciais.h ]; then
  echo "erro: credenciais.h não existe." >&2
  echo "      crie com: cp credenciais.exemplo.h credenciais.h" >&2
  exit 1
fi

TMP=$(mktemp -d)
mkdir -p "$TMP/sketch"
cp sketch.ino credenciais.h "$TMP/sketch/"
arduino-cli compile \
  --fqbn esp32:esp32:esp32doit-devkit-v1 \
  --output-dir "$PWD/build" \
  "$TMP/sketch"
rm -rf "$TMP"

TLS=$(grep -E '^#define MQTT_TLS' credenciais.h | awk '{print $3}')
if [ "$TLS" = "1" ]; then
  BROKER=$(awk '/^#if MQTT_TLS/{f=1} /^#else/{f=0} f && /MQTT_BROKER/{gsub(/"/,"",$3); print $3}' credenciais.h)
  MODO="cluster HiveMQ com TLS: $BROKER:8883"
else
  BROKER=$(awk '/^#else/{f=1} /^#endif/{f=0} f && /MQTT_BROKER/{gsub(/"/,"",$3); print $3}' credenciais.h)
  MODO="MQTT sem TLS: $BROKER:1883"
fi
echo "Firmware pronto em build/ ($MODO)"
echo "  simulador: F1 > Wokwi: Start Simulator | placa real: grave build/sketch.ino.merged.bin"
