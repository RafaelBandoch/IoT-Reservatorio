"""
Regras de decisão do backend, sem MQTT nem banco: recebem o que chegou e
o instante (em segundos, de time.monotonic) e dizem o que publicar. Assim
os testes conseguem simular o tempo sem esperar de verdade.

- liberaBomba do heartbeat:
    false com nível >= NIVEL_ALTO, até cair para NIVEL_LIBERA (NIVEL_ALTO)
    false sem leitura há mais de LEITURA_VELHA_S, com sensor em FALHA ou
    com o medidor offline (SEM_LEITURA)
- histerese: LIGAR em <= NIVEL_LIGA, DESLIGAR em >= NIVEL_DESLIGA, só com
  o controlador em AUTOMATICO;
- um comando da regra por vez: o próximo só sai depois do EXECUTADO ou
  REJEITADO do anterior, ou depois de TIMEOUT_REGRA_S.
"""

from dataclasses import dataclass
from typing import Optional

NIVEL_LIGA = 10
NIVEL_DESLIGA = 90
NIVEL_ALTO = 90       # liberaBomba=false (NIVEL_ALTO) a partir daqui...
NIVEL_LIBERA = 87     # ...e só volta a true quando o nível cair até aqui
LEITURA_VELHA_S = 30
TIMEOUT_REGRA_S = 5
PERIODO_HB_S = 10


@dataclass
class Leitura:
    nivel: Optional[float]
    sensor_ok: bool
    instante: float


@dataclass
class ComandoRegra:
    command_id: str
    acao: str
    enviado: float
    origem: Optional[float]   # instante da telemetria que disparou


@dataclass
class Resposta:
    command_id: str
    etapa: str
    ms_desde_envio: float
    ms_desde_telemetria: Optional[float]
    acao: str = ""


class Regras:
    def __init__(self):
        self.leitura: Optional[Leitura] = None
        self.medidor_online: Optional[bool] = None
        self.controlador_online: Optional[bool] = None
        self.modo: Optional[str] = None
        self.bomba: Optional[str] = None
        self.bloqueio_alto = False
        self.libera_publicada: Optional[tuple[bool, Optional[str]]] = None
        self.cmd_regra: Optional[ComandoRegra] = None

    # ---------- entradas ----------
    def registrar_telemetria(self, nivel: Optional[float], sensor_ok: bool, agora: float) -> None:
        self.leitura = Leitura(nivel, sensor_ok, agora)
        # A margem acompanha cada leitura válida, não só o heartbeat.
        if sensor_ok and nivel is not None:
            if nivel >= NIVEL_ALTO:
                self.bloqueio_alto = True
            elif nivel <= NIVEL_LIBERA:
                self.bloqueio_alto = False

    def registrar_status(self, medidor: bool, online: bool) -> None:
        if medidor:
            self.medidor_online = online
        else:
            self.controlador_online = online

    def registrar_estado(self, modo: str, bomba: str) -> None:
        self.modo, self.bomba = modo, bomba

    # ---------- heartbeat ----------
    def liberacao(self, agora: float) -> tuple[bool, Optional[str]]:
        """(liberaBomba, motivo) do próximo heartbeat."""
        l = self.leitura
        if (l is None or self.medidor_online is False or not l.sensor_ok
                or l.nivel is None or agora - l.instante > LEITURA_VELHA_S):
            return False, "SEM_LEITURA"
        if self.bloqueio_alto:
            return False, "NIVEL_ALTO"
        return True, None

    def liberacao_mudou(self, agora: float) -> bool:
        """True quando o liberaBomba atual difere do último heartbeat publicado."""
        if self.libera_publicada is None:
            return False
        return self.liberacao(agora)[0] != self.libera_publicada[0]

    def heartbeat_publicado(self, libera: bool, motivo: Optional[str]) -> None:
        self.libera_publicada = (libera, motivo)

    # ---------- histerese ----------
    def decidir_regra(self, agora: float) -> Optional[str]:
        """Ação da regra (LIGAR ou DESLIGAR) a enviar agora, ou None."""
        if self.modo != "AUTOMATICO" or self.controlador_online is False or self.leitura is None:
            return None
        nivel = self.leitura.nivel
        if nivel is None or not self.leitura.sensor_ok:
            return None
        libera, _ = self.liberacao(agora)
        acao = None
        if libera and nivel <= NIVEL_LIGA and self.bomba == "DESLIGADA":
            acao = "LIGAR"
        elif nivel >= NIVEL_DESLIGA and self.bomba == "LIGADA":
            acao = "DESLIGAR"
        if acao is None:
            return None
        if self.cmd_regra and agora - self.cmd_regra.enviado < TIMEOUT_REGRA_S:
            return None
        return acao

    def regra_aguardando_expirou(self, agora: float) -> bool:
        return self.cmd_regra is not None and agora - self.cmd_regra.enviado >= TIMEOUT_REGRA_S

    def regra_enviada(self, command_id: str, acao: str, agora: float, origem: Optional[float]) -> None:
        self.cmd_regra = ComandoRegra(command_id, acao, agora, origem)

    def resposta(self, command_id: str, etapa: str, agora: float) -> Optional[Resposta]:
        """Libera a regra quando chega o EXECUTADO ou REJEITADO do comando dela."""
        c = self.cmd_regra
        if c is None or c.command_id != command_id or etapa not in ("EXECUTADO", "REJEITADO"):
            return None
        self.cmd_regra = None
        return Resposta(command_id, etapa, (agora - c.enviado) * 1000,
                        (agora - c.origem) * 1000 if c.origem is not None else None, c.acao)
