#!/bin/bash
# Grava pelo Wi-Fi (ArduinoOTA) o firmware compilado com ./compilar.sh.
#
#   ./ota.sh medidor                       # acha a placa por rsv-medidor-01.local
#   ./ota.sh controlador 10.28.198.51      # ou pelo IP (painel, serial ou roteador)
#
# A placa precisa estar rodando um firmware com OTA (medidor 2.1.0 ou mais,
# controlador 1.1.0 ou mais) e na mesma rede deste computador. A senha vem do
# OTA_SENHA do credenciais.h.
#
# Como funciona: este computador manda um convite UDP para a porta 3232 da
# placa, e a placa abre uma conexão TCP de volta para a porta 3233 deste
# computador para baixar o firmware. Por isso o firewall do Windows precisa
# liberar a entrada na 3233/TCP: rode uma vez o liberar-firewall.ps1 como
# administrador.
set -e
cd "$(dirname "$0")"

PLACA="${1:-}"
case "$PLACA" in
  medidor)     DEVICE=rsv-medidor-01 ;;
  controlador) DEVICE=rsv-controlador-01 ;;
  *)
    echo "uso: ./ota.sh medidor|controlador [IP]" >&2
    exit 1 ;;
esac
ALVO="${2:-$DEVICE.local}"
PORTA_LOCAL=3233

BIN="build/$PLACA/$PLACA.ino.bin"
if [ ! -f "$BIN" ]; then
  echo "erro: $BIN não existe. Compile antes com: ./compilar.sh $PLACA" >&2
  exit 1
fi
SENHA=$(grep -E '^#define OTA_SENHA' credenciais.h | sed -E 's/^#define OTA_SENHA[[:space:]]+"(.*)".*/\1/')
if [ -z "$SENHA" ]; then
  echo "erro: OTA_SENHA não definida no credenciais.h (ver credenciais.exemplo.h)" >&2
  exit 1
fi
VERSAO=$(grep -E '^#define FW_VERSAO' "$PLACA/$PLACA.ino" | sed -E 's/.*"(.*)".*/\1/')

# espota.exe no Windows (não precisa de Python); espota.py no Linux e no macOS.
ESPOTA=$(ls -d "${LOCALAPPDATA:-/nao}"/Arduino15/packages/esp32/hardware/esp32/*/tools/espota.exe \
              "$HOME"/.arduino15/packages/esp32/hardware/esp32/*/tools/espota.py \
              "$HOME"/Library/Arduino15/packages/esp32/hardware/esp32/*/tools/espota.py 2>/dev/null | sort -V | tail -1)
if [ -z "$ESPOTA" ]; then
  echo "erro: espota não encontrado. Instale o core: arduino-cli core install esp32:esp32" >&2
  exit 1
fi
case "$ESPOTA" in *.py) ESPOTA_CMD=(python3 "$ESPOTA") ;; *) ESPOTA_CMD=("$ESPOTA") ;; esac

echo "Gravando $BIN (fw $VERSAO) em $DEVICE ($ALVO) pelo Wi-Fi..."
[ "$PLACA" = controlador ] && echo "  o controlador desliga a bomba e entra em SEGURO/OTA até reiniciar"
[ "$PLACA" = medidor ] && echo "  o medidor fica offline e o backend bloqueia a bomba (SEM_LEITURA) até ele voltar"
"${ESPOTA_CMD[@]}" -r -i "$ALVO" -p 3232 -P "$PORTA_LOCAL" -a "$SENHA" -f "$BIN"
echo "Pronto. A placa reinicia e o painel deve mostrar o fw $VERSAO em alguns segundos."
