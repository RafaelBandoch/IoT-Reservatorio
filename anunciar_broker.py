"""
Anuncia este computador na rede local como aquanivel.local (mDNS), para os
ESP32 acharem o broker sem saber o IP.

O IP do computador no hotspot do celular muda entre sessões, e o
certificado do broker é emitido para o nome aquanivel.local. Este script
responde às perguntas "quem é aquanivel.local?" com o IP atual do
computador na rede Wi-Fi e acompanha a troca de IP enquanto roda.

Roda no Windows, fora do Docker (o Docker Desktop não repassa multicast):

    pip install zeroconf
    python anunciar_broker.py              # descobre o IP da rede sozinho
    python anunciar_broker.py --ip 10.21.166.151

Deixe aberto enquanto o sistema estiver no ar. Ctrl+C encerra.
Precisa da 5353/UDP liberada no firewall (liberar-firewall.ps1).
"""

import argparse
import socket
import sys
import time

from zeroconf import IPVersion, ServiceInfo, Zeroconf

NOME = "aquanivel"
PORTA = 8883
VERIFICA_IP_S = 15


def ip_da_rede() -> str:
    """IP da interface que tem a rota padrão (a do Wi-Fi). Não envia nada."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def anunciar(ip: str) -> tuple[Zeroconf, ServiceInfo]:
    zc = Zeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
    info = ServiceInfo(
        "_mqtt._tcp.local.",
        f"AquaNivel broker._mqtt._tcp.local.",
        addresses=[socket.inet_aton(ip)],
        port=PORTA,
        server=f"{NOME}.local.",
        properties={"tls": "1"},
    )
    zc.register_service(info, allow_name_change=True)
    return zc, info


def main() -> None:
    par = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    par.add_argument("--ip", help="IP deste computador na rede dos ESP32 (padrão: descobre sozinho)")
    args = par.parse_args()

    ip = args.ip or ip_da_rede()
    zc, info = anunciar(ip)
    print(f"Anunciando {NOME}.local -> {ip} (broker na porta {PORTA}). Ctrl+C encerra.", flush=True)
    try:
        while True:
            time.sleep(VERIFICA_IP_S)
            if args.ip:
                continue
            try:
                novo = ip_da_rede()
            except OSError:
                # Wi-Fi caiu ou o hotspot está trocando de rede: espera voltar.
                print("sem rede no momento, tentando de novo", flush=True)
                continue
            if novo != ip:
                print(f"IP mudou de {ip} para {novo}, anunciando de novo", flush=True)
                zc.unregister_service(info)
                zc.close()
                ip = novo
                zc, info = anunciar(ip)
    except KeyboardInterrupt:
        pass
    finally:
        zc.unregister_service(info)
        zc.close()
        print("Anúncio encerrado.")


if __name__ == "__main__":
    sys.exit(main())
