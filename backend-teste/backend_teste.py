"""
Backend provisório do AquaNível, só para testar os dois ESP32 até o
FastAPI existir. Segue o contrato v1 (CONTRATOS.md).

- Assina telemetria, alerta e status do medidor; ack, estado e status do
  controlador.
- Publica o heartbeat a cada 10 s com liberaBomba:
    false com nível >= 90 %, até cair para 87 %     (motivo NIVEL_ALTO)
    false sem leitura há mais de 30 s ou sensor FALHA (motivo SEM_LEITURA)
  e na hora em que liberaBomba muda, antes de qualquer comando.
- Aplica a histerese 10 %/90 % a cada telemetria, mandando cmd com
  origem=regra, só quando o controlador está em AUTOMATICO. Um novo cmd da
  regra só sai depois do EXECUTADO ou REJEITADO do anterior (ou 5 s).
- Aceita comandos no terminal (digite "ajuda").

    pip install paho-mqtt
    python backend_teste.py                 # broker.hivemq.com:1883
    python backend_teste.py --sem-medidor   # libera a bomba sem medidor
"""

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

MEDIDOR = "rsv-medidor-01"
CONTROLADOR = "rsv-controlador-01"

PERIODO_HB_S = 10
LEITURA_VELHA_S = 30
NIVEL_LIGA = 10
NIVEL_DESLIGA = 90
NIVEL_ALTO = 90       # liberaBomba=false (NIVEL_ALTO) a partir daqui...
NIVEL_LIBERA = 87     # ...e só volta a true quando o nível cair até aqui
TIMEOUT_REGRA_S = 5   # espera pelo EXECUTADO/REJEITADO do cmd da regra

AJUDA = """comandos:
  ligar     LIGAR manual            desligar  DESLIGAR manual
  auto      MODO_AUTO               liberar   LIBERAR (sai do SEGURO por TEMPO_MAXIMO)
  repetir   reenvia o último cmd com o mesmo commandId (testa COMANDO_DUPLICADO)
  pausa     para o heartbeat        volta     retoma o heartbeat
  status    mostra o que o backend sabe
  sair"""


def agora_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


class Backend:
    def __init__(self, args):
        self.args = args
        self.prefixo = args.prefixo
        self.trava = threading.Lock()
        self.rodando = True
        self.heartbeat_ativo = True
        self.seq_hb = 0
        self.seq_cmd = 0
        self.ultimo_cmd = None          # (topico, payload) para "repetir"
        self.libera_publicada = None     # (liberaBomba, motivo) do último heartbeat
        self.bloqueio_alto = False       # NIVEL_ALTO ativo (com margem até NIVEL_LIBERA)
        # cmd da regra aguardando resposta: commandId, instante do envio e
        # instante da telemetria que o disparou (para medir a latência)
        self.cmd_regra = None
        # O que se sabe dos dispositivos
        self.telemetria = None
        self.t_telemetria = 0.0
        self.estado = None
        self.online = {MEDIDOR: None, CONTROLADOR: None}

        try:
            self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                       client_id=f"catolicasc-g4-backend-teste-{int(time.time())}")
        except AttributeError:   # paho-mqtt 1.x
            self.cliente = mqtt.Client(client_id=f"catolicasc-g4-backend-teste-{int(time.time())}")
        self.cliente.on_connect = self.ao_conectar
        self.cliente.on_disconnect = self.ao_desconectar
        self.cliente.on_message = self.ao_receber

    def topico(self, *partes):
        return "/".join((self.prefixo,) + partes)

    # ---------- MQTT ----------
    def ao_conectar(self, cliente, userdata, flags, rc, *_):
        if rc != 0:
            log(f"MQTT: falha ao conectar (rc={rc})")
            return
        log(f"MQTT conectado em {self.args.broker}:{self.args.porta}")
        for dispositivo, sufixo, qos in (
            (MEDIDOR, "telemetria", 0), (MEDIDOR, "alerta", 1), (MEDIDOR, "status", 1),
            (CONTROLADOR, "ack", 1), (CONTROLADOR, "estado", 1), (CONTROLADOR, "status", 1),
        ):
            cliente.subscribe(self.topico(dispositivo, sufixo), qos)

    def ao_desconectar(self, cliente, userdata, *args):
        log("MQTT desconectado; o paho tenta reconectar sozinho")

    def ao_receber(self, cliente, userdata, msg):
        try:
            dados = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            if msg.payload:
                log(f"PAYLOAD_INVALIDO em {msg.topic}: {msg.payload[:80]!r}")
            return
        if not isinstance(dados, dict) or dados.get("v") != 1:
            log(f"VERSAO_INCOMPATIVEL ou PAYLOAD_INVALIDO em {msg.topic}")
            return
        sufixo = msg.topic[len(self.prefixo) + 1:]
        dispositivo, _, tipo = sufixo.partition("/")
        with self.trava:
            if tipo == "telemetria":
                self.ao_telemetria(dados)
            elif tipo == "alerta":
                nivel = (dados.get("nivel") or {}).get("valor")
                log(f"ALERTA do medidor: {dados.get('codigo')} (nível {nivel} %)")
            elif tipo == "status":
                self.online[dispositivo] = dados.get("online")
                extra = f" fw={dados.get('fw')} ip={dados.get('ip')}" if dados.get("online") else ""
                log(f"status {dispositivo}: {'ONLINE' if dados.get('online') else 'OFFLINE'}{extra}")
            elif tipo == "ack":
                motivo = f" motivo={dados['motivo']}" if dados.get("motivo") else ""
                log(f"ack {dados.get('commandId')}: {dados.get('etapa')}{motivo}")
                self.ao_ack_regra(dados)
            elif tipo == "estado":
                self.estado = dados
                seguro = f" ({dados.get('motivoSeguro')})" if dados.get("modo") == "SEGURO" else ""
                ligada = (dados.get("ligadaHa") or {}).get("valor")
                log(f"estado controlador: modo={dados.get('modo')}{seguro} bomba={dados.get('bomba')}"
                    f" ligadaHa={ligada}s ultimoCommandId={dados.get('ultimoCommandId')}")
                self.aplicar_regra()

    def ao_telemetria(self, dados):
        self.telemetria = dados
        self.t_telemetria = time.time()
        nivel = (dados.get("nivel") or {}).get("valor")
        dist = (dados.get("distancia") or {}).get("valor")
        temp = (dados.get("temperatura") or {}).get("valor")
        log(f"telemetria seq={dados.get('seq')}: nível={nivel} % dist={dist} cm "
            f"temp={temp} °C sensor={dados.get('sensor')}")
        self.verificar_liberacao()
        self.aplicar_regra(self.t_telemetria)

    # ---------- decisão ----------
    def liberacao(self):
        """Devolve (liberaBomba, motivo) do próximo heartbeat."""
        if self.args.sem_medidor:
            return True, None
        t = self.telemetria
        if t is None or time.time() - self.t_telemetria > LEITURA_VELHA_S or t.get("sensor") != "OK":
            return False, "SEM_LEITURA"
        nivel = (t.get("nivel") or {}).get("valor")
        if nivel is None:
            return False, "SEM_LEITURA"
        # Margem: bloqueia em NIVEL_ALTO e só libera de novo em NIVEL_LIBERA.
        # Sem ela, um nível oscilando em torno de 90 % alternaria a liberação
        # (e o heartbeat imediato) a cada telemetria.
        if nivel >= NIVEL_ALTO:
            self.bloqueio_alto = True
        elif nivel <= NIVEL_LIBERA:
            self.bloqueio_alto = False
        if self.bloqueio_alto:
            return False, "NIVEL_ALTO"
        return True, None

    def verificar_liberacao(self):
        """Publica o heartbeat na hora se liberaBomba mudou desde o último."""
        if not self.heartbeat_ativo or self.libera_publicada is None:
            return
        atual = self.liberacao()
        if atual[0] != self.libera_publicada[0]:
            log(f"liberaBomba mudou para {str(atual[0]).lower()}: heartbeat imediato")
            self.publicar_heartbeat()

    def ao_ack_regra(self, dados):
        if not self.cmd_regra or dados.get("commandId") != self.cmd_regra[0]:
            return
        if dados.get("etapa") not in ("EXECUTADO", "REJEITADO"):
            return
        cid, t_envio, t_origem = self.cmd_regra
        agora = time.time()
        origem = f"telemetria -> {dados.get('etapa')} = {(agora - t_origem) * 1000:.0f} ms, " if t_origem else ""
        log(f"LATÊNCIA {cid}: {origem}cmd -> {dados.get('etapa')} = {(agora - t_envio) * 1000:.0f} ms")
        self.cmd_regra = None

    def aplicar_regra(self, t_origem=None):
        """Histerese 10 %/90 %, só com o controlador em AUTOMATICO."""
        if self.args.sem_medidor or not self.estado or self.estado.get("modo") != "AUTOMATICO":
            return
        libera, _ = self.liberacao()
        nivel = (self.telemetria or {}).get("nivel", {}).get("valor")
        if nivel is None:
            return
        bomba = self.estado.get("bomba")
        acao = None
        if libera and nivel <= NIVEL_LIGA and bomba == "DESLIGADA":
            acao = "LIGAR"
        elif nivel >= NIVEL_DESLIGA and bomba == "LIGADA":
            acao = "DESLIGAR"
        if acao is None:
            return
        if self.cmd_regra:
            if time.time() - self.cmd_regra[1] < TIMEOUT_REGRA_S:
                return
            log(f"cmd {self.cmd_regra[0]}: sem EXECUTADO/REJEITADO em {TIMEOUT_REGRA_S} s, liberando a regra")
        log(f"REGRA: nível {nivel} % com bomba {bomba} -> {acao}")
        cid = self.enviar_cmd(acao, "regra")
        self.cmd_regra = (cid, time.time(), t_origem)

    # ---------- publicação ----------
    def enviar_cmd(self, acao, origem):
        self.seq_cmd += 1
        agora = datetime.now()
        # Data e hora no commandId: um backend reiniciado não repete id que
        # ainda esteja na lista de 8 do controlador.
        cid = f"c-{agora:%Y%m%d-%H%M%S}-{self.seq_cmd:03d}"
        payload = json.dumps({"v": 1, "commandId": cid, "origem": origem, "acao": acao,
                              "usuario": "admin" if origem == "manual" else "backend",
                              "ts": agora_iso()})
        topico = self.topico(CONTROLADOR, "cmd")
        self.ultimo_cmd = (topico, payload, cid)
        self.cliente.publish(topico, payload, qos=1, retain=False)
        log(f"cmd {cid}: {acao} origem={origem} ENVIADO")
        return cid

    def repetir_cmd(self):
        if not self.ultimo_cmd:
            log("nenhum cmd enviado ainda")
            return
        topico, payload, cid = self.ultimo_cmd
        self.cliente.publish(topico, payload, qos=1, retain=False)
        log(f"cmd {cid}: REENVIADO com o mesmo commandId (esperado: REJEITADO COMANDO_DUPLICADO)")

    def laco_heartbeat(self):
        proximo = time.time()
        while self.rodando:
            if time.time() >= proximo:
                proximo += PERIODO_HB_S
                with self.trava:
                    if self.heartbeat_ativo:
                        self.publicar_heartbeat()
                    self.aplicar_regra()
            else:
                # A liberação também muda sozinha quando a leitura envelhece.
                with self.trava:
                    self.verificar_liberacao()
            time.sleep(0.2)

    def publicar_heartbeat(self):
        self.seq_hb += 1
        libera, motivo = self.liberacao()
        self.libera_publicada = (libera, motivo)
        payload = json.dumps({"v": 1, "ts": agora_iso(), "seq": self.seq_hb,
                              "liberaBomba": libera, "motivo": motivo})
        self.cliente.publish(self.topico("backend", "heartbeat"), payload, qos=0, retain=False)
        log(f"heartbeat seq={self.seq_hb} liberaBomba={str(libera).lower()}"
            f"{' motivo=' + motivo if motivo else ''}")

    def mostrar_status(self):
        with self.trava:
            libera, motivo = self.liberacao()
            idade = f"{time.time() - self.t_telemetria:.0f}s" if self.telemetria else "nunca"
            nivel = (self.telemetria or {}).get("nivel", {}).get("valor")
            e = self.estado or {}
            log(f"broker {self.args.broker}:{self.args.porta} | heartbeat "
                f"{'ATIVO' if self.heartbeat_ativo else 'PAUSADO'} seq={self.seq_hb} "
                f"liberaBomba={str(libera).lower()}{' (' + motivo + ')' if motivo else ''}")
            log(f"medidor: online={self.online[MEDIDOR]} última telemetria={idade} nível={nivel}"
                f"{' (ignorado: --sem-medidor)' if self.args.sem_medidor else ''}")
            log(f"controlador: online={self.online[CONTROLADOR]} modo={e.get('modo')} "
                f"motivoSeguro={e.get('motivoSeguro')} bomba={e.get('bomba')}")

    # ---------- terminal ----------
    def executar(self):
        log(f"conectando em {self.args.broker}:{self.args.porta}, prefixo {self.prefixo}")
        if self.args.sem_medidor:
            log("--sem-medidor: liberaBomba=true sempre e sem histerese. Só para testar o controlador!")
        self.cliente.reconnect_delay_set(1, 30)
        self.cliente.connect_async(self.args.broker, self.args.porta, keepalive=30)
        self.cliente.loop_start()
        threading.Thread(target=self.laco_heartbeat, daemon=True).start()
        print(AJUDA, flush=True)

        acoes = {"ligar": "LIGAR", "desligar": "DESLIGAR", "auto": "MODO_AUTO", "liberar": "LIBERAR"}
        try:
            while True:
                try:
                    linha = input().strip().lower()
                except EOFError:
                    break
                if not linha:
                    continue
                if linha in acoes:
                    with self.trava:
                        self.enviar_cmd(acoes[linha], "manual")
                elif linha == "repetir":
                    with self.trava:
                        self.repetir_cmd()
                elif linha == "pausa":
                    self.heartbeat_ativo = False
                    log(f"heartbeat PAUSADO: o controlador deve entrar em SEGURO em ~30 s")
                elif linha == "volta":
                    self.heartbeat_ativo = True
                    log("heartbeat RETOMADO: o controlador sai do SEGURO após 3 heartbeats")
                elif linha == "status":
                    self.mostrar_status()
                elif linha in ("sair", "exit", "quit"):
                    break
                else:
                    print(AJUDA, flush=True)
        except KeyboardInterrupt:
            pass
        self.rodando = False
        self.cliente.loop_stop()
        self.cliente.disconnect()
        log("backend encerrado")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Backend provisório do AquaNível (contrato MQTT v1)")
    p.add_argument("--broker", default="broker.hivemq.com")
    p.add_argument("--porta", type=int, default=1883)
    p.add_argument("--prefixo", default="catolicasc-g4/reservatorio")
    p.add_argument("--sem-medidor", action="store_true",
                   help="libera a bomba sem medidor (só para testar o controlador)")
    Backend(p.parse_args()).executar()


if __name__ == "__main__":
    main()
