#!/bin/bash
# Compila o firmware localmente.
# O arduino-cli exige que o .ino esteja numa pasta de mesmo nome, por isso
# a cópia para um diretório temporário. O credenciais.h da raiz vai junto.
#
#   ./compilar.sh               # sketch.ino da N1, saída em build/
#   ./compilar.sh medidor       # medidor/medidor.ino, saída em build/medidor/
#   ./compilar.sh controlador   # controlador/controlador.ino, saída em build/controlador/
#
# A escolha entre cluster HiveMQ (TLS) e broker sem TLS está no
# credenciais.h, na constante MQTT_TLS. Esse arquivo não é versionado.
set -e
cd "$(dirname "$0")"

PLACA="${1:-}"
case "$PLACA" in
  "")                  NOME=sketch; ORIGEM=sketch.ino; SAIDA=build ;;
  medidor|controlador) NOME=$PLACA; ORIGEM=$PLACA/$PLACA.ino; SAIDA=build/$PLACA ;;
  *)
    echo "uso: ./compilar.sh [medidor|controlador]" >&2
    exit 1 ;;
esac

if [ ! -f credenciais.h ]; then
  echo "erro: credenciais.h não existe." >&2
  echo "      crie com: cp credenciais.exemplo.h credenciais.h" >&2
  exit 1
fi

TMP=$(mktemp -d)
mkdir -p "$TMP/$NOME"
cp "$ORIGEM" credenciais.h "$TMP/$NOME/"
arduino-cli compile \
  --fqbn esp32:esp32:esp32doit-devkit-v1 \
  --output-dir "$PWD/$SAIDA" \
  "$TMP/$NOME"
rm -rf "$TMP"

TLS=$(grep -E '^#define MQTT_TLS' credenciais.h | awk '{print $3}')
if [ "$TLS" = "1" ]; then
  BROKER=$(awk '/^#if MQTT_TLS/{f=1} /^#else/{f=0} f && /MQTT_BROKER/{gsub(/"/,"",$3); print $3}' credenciais.h)
  MODO="cluster HiveMQ com TLS: $BROKER:8883"
else
  BROKER=$(awk '/^#else/{f=1} /^#endif/{f=0} f && /MQTT_BROKER/{gsub(/"/,"",$3); print $3}' credenciais.h)
  MODO="MQTT sem TLS: $BROKER:1883"
fi
echo "Firmware pronto em $SAIDA/ ($MODO)"
if [ -z "$PLACA" ]; then
  echo "  simulador: F1 > Wokwi: Start Simulator | placa real: grave build/sketch.ino.merged.bin"
else
  echo "  placa real: arduino-cli upload -p COMx --fqbn esp32:esp32:esp32doit-devkit-v1 --input-dir $SAIDA"
fi
