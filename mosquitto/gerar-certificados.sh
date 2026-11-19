#!/bin/bash
# Gera a CA própria do projeto e o certificado do broker Mosquitto.
#
#   ./mosquitto/gerar-certificados.sh            # gera, se ainda não existir
#   ./mosquitto/gerar-certificados.sh --forcar   # gera de novo (ver aviso)
#
# Saída (fora do git, ver .gitignore):
#   mosquitto/certs/ca.crt          certificado da CA (vai no firmware e no backend)
#   mosquitto/certs/ca.key          chave da CA (só serve para emitir certificados)
#   mosquitto/certs/broker.crt      certificado do broker
#   mosquitto/certs/broker.key      chave do broker
#   certificado_ca.h                ca.crt pronto para o firmware incluir
#
# GERAR UMA VEZ SÓ E GUARDAR: os ESP32 levam o ca.crt embutido. Se a CA
# mudar, os dois precisam ser gravados de novo (por OTA serve).
#
# O certificado do broker vale para os nomes aquanivel.local (como os ESP32
# acham o computador pelo mDNS), mosquitto (como o backend acha o broker
# dentro do docker compose) e localhost (testes no próprio computador).
# Chaves ECDSA P-256: handshake mais leve para o ESP32 que RSA.
set -e
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1   # Git Bash no Windows: não converter "/CN=..." em caminho

DIR=mosquitto/certs
if [ -f "$DIR/ca.crt" ] && [ "$1" != "--forcar" ]; then
  echo "A CA já existe em $DIR/ca.crt. Nada foi feito."
  echo "Para gerar de novo use --forcar, e depois grave os dois ESP32 de novo."
  exit 0
fi
mkdir -p "$DIR"

openssl ecparam -name prime256v1 -genkey -noout -out "$DIR/ca.key"
openssl req -x509 -new -key "$DIR/ca.key" -sha256 -days 3650 \
  -subj "/O=AquaNivel equipe 4/CN=AquaNivel CA" -out "$DIR/ca.crt"

openssl ecparam -name prime256v1 -genkey -noout -out "$DIR/broker.key"
openssl req -new -key "$DIR/broker.key" -subj "/O=AquaNivel equipe 4/CN=aquanivel.local" \
  -out "$DIR/broker.csr"
cat > "$DIR/broker.ext" <<EXT
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyAgreement
extendedKeyUsage=serverAuth
subjectAltName=DNS:aquanivel.local,DNS:mosquitto,DNS:localhost
EXT
openssl x509 -req -in "$DIR/broker.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial \
  -days 1825 -sha256 -extfile "$DIR/broker.ext" -out "$DIR/broker.crt"
rm -f "$DIR/broker.csr" "$DIR/broker.ext" "$DIR/ca.srl"

{
  echo "#pragma once"
  echo "// Gerado por mosquitto/gerar-certificados.sh. Nao versionado."
  echo "// Certificado da CA que assinou o broker: o ESP32 so aceita um broker"
  echo "// com certificado emitido por esta CA."
  echo "static const char CA_BROKER[] = R\"PEM("
  cat "$DIR/ca.crt"
  echo ")PEM\";"
} > certificado_ca.h

openssl verify -CAfile "$DIR/ca.crt" "$DIR/broker.crt"
echo "Pronto: $DIR/ e certificado_ca.h"
