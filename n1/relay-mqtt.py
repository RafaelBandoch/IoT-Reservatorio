"""Repassa a porta 8883 do Mac para o broker remoto.

Existe por causa de uma limitacao do Docker Desktop nesta rede: o Wi-Fi so
entrega IPv6, o macOS alcanca a internet IPv4 por traducao (CLAT/464XLAT,
o endereco 192.0.0.2 da interface) e os containers nao herdam esse tradutor.
Resultado: a bridge do mosquitto nunca chega ao HiveMQ Cloud.

O relay roda no proprio Mac, entao a conexao sai pelo caminho que funciona.
A bridge passa a apontar para host.docker.internal e o resto da stack
segue igual. O TLS continua ponta a ponta entre a bridge e o HiveMQ:
aqui so trafegam bytes, sem descriptografar nada.

Uso:
    python3 relay-mqtt.py

E no .env:
    MQTT_REMOTE_HOST=host.docker.internal

Depois: docker compose up -d --force-recreate mosquitto
"""

import os
import re
import socket
import threading

CREDENCIAIS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credenciais.h")
ESCUTA = ("0.0.0.0", 8883)


def broker_do_firmware():
    """Le o host do credenciais.h para relay e firmware nao divergirem."""
    fonte = open(CREDENCIAIS).read()
    achado = re.search(r'#define\s+MQTT_BROKER\s+"([^"]+)"', fonte)
    return achado.group(1)


def empurrar(origem, destino):
    try:
        while True:
            dados = origem.recv(65536)
            if not dados:
                break
            destino.sendall(dados)
    except OSError:
        pass
    finally:
        for lado in (origem, destino):
            try:
                lado.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def atender(cliente, endereco, destino):
    try:
        remoto = socket.create_connection(destino, timeout=20)
        # O timeout acima vale só para o estabelecimento da conexão. Se ele
        # continuar valendo, o recv() abaixo estoura durante o silêncio normal
        # do MQTT -- o keepalive da bridge é de 60 s -- e a conexão cai a cada
        # 20 s. Daqui em diante o socket bloqueia sem prazo.
        remoto.settimeout(None)
    except OSError as erro:
        print(f"[relay] {endereco}: nao alcancou o broker: {erro}", flush=True)
        cliente.close()
        return
    print(f"[relay] {endereco} <-> {destino[0]}", flush=True)
    for a, b in ((cliente, remoto), (remoto, cliente)):
        threading.Thread(target=empurrar, args=(a, b), daemon=True).start()


def main():
    destino = (broker_do_firmware(), 8883)
    servidor = socket.socket()
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind(ESCUTA)
    servidor.listen(8)
    print(f"[relay] {ESCUTA[0]}:{ESCUTA[1]} -> {destino[0]}:{destino[1]}", flush=True)
    print("[relay] Ctrl+C para parar", flush=True)
    while True:
        cliente, endereco = servidor.accept()
        threading.Thread(target=atender, args=(cliente, endereco, destino), daemon=True).start()


if __name__ == "__main__":
    main()
