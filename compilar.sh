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
[ "$TLS" = "1" ] && MODO="cluster HiveMQ com TLS" || MODO="broker público sem TLS"
echo "Firmware pronto em build/ ($MODO) — rode a simulação no Cursor (F1 > Wokwi: Start Simulator)"
