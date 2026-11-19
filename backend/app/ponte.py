"""
Ponte entre o MQTT e o resto do backend (aiomqtt).

- Assina telemetria, alerta e status do medidor; ack, estado e status do
  controlador; e os dois tópicos config (retain), para saber a
  configuração vigente.
- Valida tudo contra o contrato; o inválido é descartado e gravado em
  payloads_invalidos, sem derrubar o serviço.
- Publica o heartbeat a cada 10 s e na hora em que liberaBomba muda, sempre
  antes de qualquer comando, e os comandos da regra e do painel.

A decisão vem antes da gravação no banco: o banco nunca atrasa a bomba.
"""

import asyncio
import json
import logging
import ssl
import time
import uuid
from datetime import datetime
from typing import Awaitable, Callable, Optional

import aiomqtt
from pydantic import ValidationError

from . import contrato as c
from .config import Config
from .logica import PERIODO_HB_S, Regras
from .repositorio import Repositorio, agora_utc

log = logging.getLogger("aquanivel.ponte")

ESPERA_RECONEXAO_S = 3
EXPIRA_COMANDO_S = 15

MOTIVOS_SEGURO = {
    "BOOT": "controlador acabou de ligar",
    "SEM_HEARTBEAT": "sem comunicação com o backend",
    "TEMPO_MAXIMO": "bomba atingiu o tempo máximo ligada",
    "OTA": "atualização de firmware pelo Wi-Fi em andamento",
    "OTA_FALHOU": "atualização de firmware falhou, firmware anterior mantido",
}

MODELOS = {
    (c.MEDIDOR, "telemetria"): c.Telemetria,
    (c.MEDIDOR, "alerta"): c.Alerta,
    (c.MEDIDOR, "status"): c.Status,
    (c.CONTROLADOR, "ack"): c.Ack,
    (c.CONTROLADOR, "estado"): c.Estado,
    (c.CONTROLADOR, "status"): c.Status,
}


class SemConexao(RuntimeError):
    """O backend está sem conexão com o broker."""


def _ts() -> datetime:
    return agora_utc().replace(microsecond=0)


class Ponte:
    def __init__(self, cfg: Config, repo: Repositorio, regras: Regras,
                 notificar: Optional[Callable[[str, str, str], Awaitable[None]]] = None):
        self.cfg = cfg
        self.repo = repo
        self.regras = regras
        # notificar(device_id, codigo, texto): avisos externos (Telegram, ver telegram.py)
        self.notificar = notificar
        self.cliente: Optional[aiomqtt.Client] = None
        self.seq_hb = 0
        self.seq_cmd = 0
        self.t_ultimo_hb = 0.0
        self.esperas: dict[str, asyncio.Future] = {}
        self.fora_da_faixa = False
        # Retrato do que chegou por último, para a API responder sem ir ao banco.
        self.vivo: dict = {
            "medidor": {"online": None, "fw": None, "ip": None, "mudou_em": None, "visto_em": None},
            "controlador": {"online": None, "fw": None, "ip": None, "mudou_em": None, "visto_em": None},
            "telemetria": None, "estado": None, "heartbeat": None, "config": {},
        }

    @property
    def conectado(self) -> bool:
        return self.cliente is not None

    def topico(self, *partes: str) -> str:
        return "/".join((self.cfg.prefixo,) + partes)

    # ---------- conexão ----------
    async def executar(self) -> None:
        """Mantém a conexão com o broker para sempre, reconectando sozinho."""
        periodico = asyncio.create_task(self._laco_periodico())
        try:
            while True:
                try:
                    await self._sessao()
                except aiomqtt.MqttError as erro:
                    log.warning("MQTT: %s; nova tentativa em %d s", erro, ESPERA_RECONEXAO_S)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("erro inesperado na sessão MQTT")
                self.cliente = None
                await asyncio.sleep(ESPERA_RECONEXAO_S)
        finally:
            periodico.cancel()

    async def _sessao(self) -> None:
        tls = None
        if self.cfg.mqtt_tls:
            tls = aiomqtt.TLSParameters(ca_certs=self.cfg.mqtt_ca, cert_reqs=ssl.CERT_REQUIRED)
        async with aiomqtt.Client(
            hostname=self.cfg.mqtt_host, port=self.cfg.mqtt_porta,
            username=self.cfg.mqtt_usuario, password=self.cfg.mqtt_senha,
            identifier=f"aquanivel-backend-{uuid.uuid4().hex[:8]}", keepalive=30, tls_params=tls,
        ) as cliente:
            for dispositivo, tipo in MODELOS:
                qos = 0 if tipo == "telemetria" else 1
                await cliente.subscribe(self.topico(dispositivo, tipo), qos=qos)
            for dispositivo in (c.MEDIDOR, c.CONTROLADOR):
                await cliente.subscribe(self.topico(dispositivo, "config"), qos=1)
            self.cliente = cliente
            log.info("MQTT conectado em %s:%d (TLS=%s)", self.cfg.mqtt_host, self.cfg.mqtt_porta, self.cfg.mqtt_tls)
            await self._publicar_heartbeat(time.monotonic())
            async for msg in cliente.messages:
                payload = msg.payload if isinstance(msg.payload, (bytes, bytearray)) else str(msg.payload or "").encode()
                await self.tratar(str(msg.topic), bytes(payload))

    async def _laco_periodico(self) -> None:
        """Heartbeat a cada 10 s, liberação que muda com o tempo, timeout da regra e comandos expirados."""
        ultima_limpeza = 0.0
        while True:
            await asyncio.sleep(0.25)
            agora = time.monotonic()
            try:
                if self.conectado:
                    if agora - self.t_ultimo_hb >= PERIODO_HB_S or self.regras.liberacao_mudou(agora):
                        await self._publicar_heartbeat(agora)
                    if self.regras.regra_aguardando_expirou(agora):
                        log.warning("cmd %s da regra sem EXECUTADO/REJEITADO em 5 s, liberando a regra",
                                    self.regras.cmd_regra.command_id)
                        self.regras.cmd_regra = None
                        await self._aplicar_regra(agora, None)
                if agora - ultima_limpeza >= 2:
                    ultima_limpeza = agora
                    if await self.repo.expirar_comandos(EXPIRA_COMANDO_S):
                        log.warning("comando sem resposta em %d s marcado como EXPIRADO", EXPIRA_COMANDO_S)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("erro no laço periódico")

    # ---------- publicação ----------
    async def _publicar_heartbeat(self, agora: float) -> None:
        if not self.conectado:
            return
        libera, motivo = self.regras.liberacao(agora)
        mudou = self.regras.libera_publicada is None or self.regras.libera_publicada[0] != libera
        self.seq_hb += 1
        hb = c.Heartbeat(ts=_ts(), seq=self.seq_hb, liberaBomba=libera, motivo=motivo)
        await self.cliente.publish(self.topico("backend", "heartbeat"), hb.model_dump_json(), qos=0, retain=False)
        self.regras.heartbeat_publicado(libera, motivo)
        self.t_ultimo_hb = agora
        self.vivo["heartbeat"] = hb.model_dump(mode="json")
        if mudou:
            log.info("heartbeat seq=%d liberaBomba=%s%s", self.seq_hb, libera, f" ({motivo})" if motivo else "")

    async def enviar_comando(self, acao: str, origem: str, usuario: str,
                             t_origem: Optional[float] = None) -> str:
        """Publica um cmd e devolve o commandId. Grava ENVIADO antes de publicar,
        para o ack nunca chegar antes do registro do comando."""
        if not self.conectado:
            raise SemConexao("backend sem conexão com o broker")
        self.seq_cmd += 1
        agora = agora_utc()
        cid = f"c-{agora.astimezone():%Y%m%d-%H%M%S}-{self.seq_cmd:03d}"
        cmd = c.Comando(commandId=cid, origem=origem, acao=acao, usuario=usuario, ts=_ts())
        self.esperas[cid] = asyncio.get_running_loop().create_future()
        await self.repo.gravar_comando_enviado(cid, acao, origem, usuario, agora)
        if origem == "regra":
            self.regras.regra_enviada(cid, acao, time.monotonic(), t_origem)
        await self.cliente.publish(self.topico(c.CONTROLADOR, "cmd"), cmd.model_dump_json(), qos=1, retain=False)
        log.info("cmd %s: %s origem=%s usuario=%s ENVIADO", cid, acao, origem, usuario)
        return cid

    async def esperar_resposta(self, cid: str, timeout: float) -> Optional[dict]:
        """Espera o EXECUTADO ou REJEITADO de um comando; None se não chegar a tempo."""
        futuro = self.esperas.get(cid)
        if futuro is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(futuro), timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            if futuro.done():
                self.esperas.pop(cid, None)

    async def publicar_config(self, device_id: str, payload: dict) -> None:
        if not self.conectado:
            raise SemConexao("backend sem conexão com o broker")
        await self.cliente.publish(self.topico(device_id, "config"), json.dumps(payload), qos=1, retain=True)
        self.vivo["config"][device_id] = payload
        log.info("config publicada para %s: %s", device_id, payload)

    # ---------- recepção ----------
    async def tratar(self, topico: str, payload: bytes) -> None:
        """Ponto de entrada de toda mensagem. Nunca levanta exceção."""
        try:
            await self._tratar(topico, payload)
        except Exception:
            log.exception("erro ao tratar %s", topico)

    async def _invalido(self, topico: str, codigo: str, erro: str, payload: bytes) -> None:
        log.warning("%s em %s: %s", codigo, topico, erro[:200])
        try:
            await self.repo.gravar_invalido(topico, codigo, erro, payload)
        except Exception:
            log.exception("falha ao registrar payload inválido")

    async def _tratar(self, topico: str, payload: bytes) -> None:
        chegada, mono = agora_utc(), time.monotonic()
        if not topico.startswith(self.cfg.prefixo + "/") or not payload:
            return   # retain apagado no broker
        dispositivo, _, tipo = topico[len(self.cfg.prefixo) + 1:].partition("/")
        try:
            dados = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as erro:
            return await self._invalido(topico, "PAYLOAD_INVALIDO", f"JSON malformado: {erro}", payload)

        if tipo == "config":
            if isinstance(dados, dict):
                self.vivo["config"][dispositivo] = dados
            return

        modelo = MODELOS.get((dispositivo, tipo))
        if modelo is None:
            return
        try:
            c.verificar_versao(dados)
            m = modelo.model_validate(dados)
        except c.VersaoIncompativel as erro:
            return await self._invalido(topico, "VERSAO_INCOMPATIVEL", str(erro), payload)
        except ValidationError as erro:
            return await self._invalido(topico, "PAYLOAD_INVALIDO", str(erro), payload)
        if m.deviceId != dispositivo:
            return await self._invalido(topico, "PAYLOAD_INVALIDO",
                                        f"deviceId {m.deviceId!r} não bate com o tópico", payload)

        chave = "medidor" if dispositivo == c.MEDIDOR else "controlador"
        self.vivo[chave]["visto_em"] = chegada
        tratador = {"telemetria": self._telemetria, "alerta": self._alerta, "status": self._status,
                    "ack": self._ack, "estado": self._estado}[tipo]
        await tratador(m, chegada, mono)

    async def _verificar_liberacao(self, agora: float) -> None:
        if self.regras.liberacao_mudou(agora):
            await self._publicar_heartbeat(agora)

    async def _aplicar_regra(self, agora: float, t_origem: Optional[float]) -> None:
        acao = self.regras.decidir_regra(agora)
        if acao is None:
            return
        nivel = self.regras.leitura.nivel if self.regras.leitura else None
        log.info("REGRA: nível %s %% com bomba %s -> %s", nivel, self.regras.bomba, acao)
        try:
            await self.enviar_comando(acao, "regra", "backend", t_origem)
        except SemConexao:
            pass

    async def _telemetria(self, m: c.Telemetria, chegada: datetime, mono: float) -> None:
        fora = m.fora_da_faixa()
        ok = m.sensor_ok()
        # Decide primeiro: heartbeat imediato se a liberação mudou, depois a regra.
        self.regras.registrar_telemetria(m.nivel.valor, ok, mono)
        await self._verificar_liberacao(mono)
        await self._aplicar_regra(mono, mono)

        ts = m.instante(chegada)
        sensor = "FALHA" if (m.sensor == "FALHA" or fora) else "OK"
        self.vivo["telemetria"] = {
            "ts": ts, "recebido_em": chegada, "seq": m.seq, "nivel": m.nivel.valor,
            "distancia": m.distancia.valor, "temperatura": m.temperatura.valor, "sensor": sensor,
        }
        await self.repo.gravar_medicao(
            ts=ts, recebido_em=chegada, device_id=m.deviceId, seq=m.seq, nivel=m.nivel.valor,
            distancia=m.distancia.valor, temperatura=m.temperatura.valor,
            sensor=sensor, relogio_sincronizado=m.ts is not None)
        # Fora da faixa é SENSOR_FALHA pelo contrato: registra uma vez por ocorrência.
        if bool(fora) != self.fora_da_faixa:
            self.fora_da_faixa = bool(fora)
            if fora:
                await self._alerta_backend(c.MEDIDOR, "SENSOR_FALHA", m.nivel.valor, f"leitura fora da faixa: {fora}")

    async def _alerta(self, m: c.Alerta, chegada: datetime, mono: float) -> None:
        log.info("alerta do medidor: %s (nível %s %%)", m.codigo, m.nivel.valor)
        await self.repo.gravar_alerta(ts=m.instante(chegada), recebido_em=chegada, device_id=m.deviceId,
                                      codigo=m.codigo, nivel=m.nivel.valor, detalhe=None)
        await self._avisar(m.deviceId, m.codigo, m.nivel.valor)

    async def _status(self, m: c.Status, chegada: datetime, mono: float) -> None:
        chave = "medidor" if m.deviceId == c.MEDIDOR else "controlador"
        anterior = self.vivo[chave]["online"]
        self.vivo[chave].update(online=m.online, fw=m.fw or self.vivo[chave]["fw"],
                                ip=m.ip or self.vivo[chave]["ip"], mudou_em=chegada)
        self.regras.registrar_status(m.deviceId == c.MEDIDOR, m.online)
        log.info("status %s: %s", m.deviceId, "ONLINE" if m.online else "OFFLINE")
        if m.deviceId == c.MEDIDOR:
            await self._verificar_liberacao(mono)   # medidor caiu: SEM_LEITURA na hora
        await self.repo.gravar_conexao(recebido_em=chegada, device_id=m.deviceId, online=m.online,
                                       fw=m.fw, ip=m.ip)
        # Alerta só na mudança vista por este backend (o retain da subida não conta).
        if anterior is not None and anterior != m.online:
            await self._alerta_backend(m.deviceId, "ONLINE" if m.online else "OFFLINE", None,
                                       f"{m.deviceId} {'voltou' if m.online else 'ficou offline'}")

    async def _ack(self, m: c.Ack, chegada: datetime, mono: float) -> None:
        r = self.regras.resposta(m.commandId, m.etapa, mono)
        if r:
            extra = f", telemetria -> {r.etapa} = {r.ms_desde_telemetria:.0f} ms" if r.ms_desde_telemetria else ""
            log.info("LATENCIA %s: cmd -> %s = %.0f ms%s", r.command_id, r.etapa, r.ms_desde_envio, extra)
        log.info("ack %s: %s%s", m.commandId, m.etapa, f" motivo={m.motivo}" if m.motivo else "")
        # Grava antes de avisar quem espera: a API lê o comando do banco logo em seguida.
        await self.repo.gravar_ack(m.commandId, m.etapa, m.motivo, m.instante(chegada), chegada)
        if m.etapa in ("EXECUTADO", "REJEITADO"):
            futuro = self.esperas.get(m.commandId)
            if futuro and not futuro.done():
                futuro.set_result({"etapa": m.etapa, "motivo": m.motivo})

    async def _estado(self, m: c.Estado, chegada: datetime, mono: float) -> None:
        anterior = self.vivo["estado"]
        self.regras.registrar_estado(m.modo, m.bomba)
        self.vivo["estado"] = {
            "ts": m.instante(chegada), "recebido_em": chegada, "modo": m.modo, "motivoSeguro": m.motivoSeguro,
            "bomba": m.bomba, "ligadaHa_s": m.ligadaHa.valor, "ultimoCommandId": m.ultimoCommandId, "fw": m.fw,
        }
        self.vivo["controlador"]["fw"] = m.fw
        log.info("estado: modo=%s%s bomba=%s", m.modo, f" ({m.motivoSeguro})" if m.motivoSeguro else "", m.bomba)
        await self._aplicar_regra(mono, None)
        await self.repo.gravar_estado(ts=m.instante(chegada), recebido_em=chegada, device_id=m.deviceId,
                                      modo=m.modo, motivo_seguro=m.motivoSeguro, bomba=m.bomba,
                                      ligada_ha_s=m.ligadaHa.valor, ultimo_command_id=m.ultimoCommandId, fw=m.fw)
        if m.ultimoCommandId and await self.repo.confirmar_comando(m.ultimoCommandId, chegada):
            log.info("cmd %s CONFIRMADO pelo estado", m.ultimoCommandId)
        if m.modo == "SEGURO" and (anterior is None or anterior["modo"] != "SEGURO"
                                   or anterior["motivoSeguro"] != m.motivoSeguro):
            await self._alerta_backend(c.CONTROLADOR, "MODO_SEGURO", None,
                                       f"bomba desligada: {MOTIVOS_SEGURO.get(m.motivoSeguro, m.motivoSeguro or 'sem motivo')}",
                                       avisar=anterior is not None)

    async def _alerta_backend(self, device_id: str, codigo: str, nivel: Optional[float], detalhe: str,
                              avisar: bool = True) -> None:
        """Alerta gerado pelo próprio backend (não vem do contrato do medidor)."""
        agora = agora_utc()
        await self.repo.gravar_alerta(ts=agora, recebido_em=agora, device_id=device_id, codigo=codigo,
                                      nivel=nivel, detalhe=detalhe)
        if avisar:
            await self._avisar(device_id, codigo, nivel, detalhe)

    async def _avisar(self, device_id: str, codigo: str, nivel: Optional[float], detalhe: Optional[str] = None):
        if self.notificar is None:
            return
        texto = detalhe or (f"nível {nivel:.0f} %" if nivel is not None else "")
        try:
            await self.notificar(device_id, codigo, texto)
        except Exception:
            log.exception("falha ao notificar %s", codigo)
