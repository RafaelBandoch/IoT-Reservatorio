"""
Modelos Pydantic do contrato MQTT v1 (CONTRATOS.md).

Mensagens que chegam (dos ESP32) são validadas aqui; a que não passa é
descartada e registrada como PAYLOAD_INVALIDO ou VERSAO_INCOMPATIVEL.
Leitura fora da faixa plausível NÃO é payload inválido: pelo contrato
isso é SENSOR_FALHA, tratado na lógica (ver Telemetria.fora_da_faixa).
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSAO = 1

MEDIDOR = "rsv-medidor-01"
CONTROLADOR = "rsv-controlador-01"

ACOES = ("LIGAR", "DESLIGAR", "MODO_AUTO", "LIBERAR")
Acao = Literal["LIGAR", "DESLIGAR", "MODO_AUTO", "LIBERAR"]
Origem = Literal["regra", "manual"]

# Faixas plausíveis do contrato
DIST_MIN, DIST_MAX = 2.0, 400.0
TEMP_MIN, TEMP_MAX = -10.0, 60.0
NIVEL_MIN, NIVEL_MAX = 0.0, 100.0


class VersaoIncompativel(ValueError):
    """Campo v presente, mas diferente de 1."""


def verificar_versao(dados: dict) -> None:
    if isinstance(dados, dict) and "v" in dados and dados["v"] != VERSAO:
        raise VersaoIncompativel(f"v={dados['v']!r}")


class Modelo(BaseModel):
    # Campos a mais são ignorados: uma versão futura pode acrescentar
    # campos sem derrubar quem ainda fala a v1.
    model_config = ConfigDict(extra="ignore")
    v: Literal[1]


class Grandeza(BaseModel):
    valor: Optional[float]
    unidade: str


class MensagemDispositivo(Modelo):
    deviceId: str
    ts: Optional[datetime] = None
    relogio: Optional[Literal["NAO_SINCRONIZADO"]] = None

    @model_validator(mode="after")
    def _ts_ou_relogio(self):
        if self.ts is None and self.relogio is None:
            raise ValueError("falta ts (ou relogio=NAO_SINCRONIZADO)")
        return self

    def instante(self, chegada: datetime) -> datetime:
        """ts do dispositivo; sem NTP, o horário de chegada (regra do contrato)."""
        if self.ts is None:
            return chegada
        return self.ts if self.ts.tzinfo else self.ts.replace(tzinfo=timezone.utc)


class Telemetria(MensagemDispositivo):
    seq: int = Field(ge=0)
    nivel: Grandeza
    distancia: Grandeza
    temperatura: Grandeza
    sensor: Literal["OK", "FALHA"]

    @model_validator(mode="after")
    def _unidades(self):
        for nome, esperada in (("nivel", "%"), ("distancia", "cm"), ("temperatura", "°C")):
            if getattr(self, nome).unidade != esperada:
                raise ValueError(f"{nome}.unidade deve ser {esperada!r}")
        return self

    def fora_da_faixa(self) -> Optional[str]:
        """Descrição da grandeza fora da faixa plausível, ou None."""
        for nome, minimo, maximo in (("nivel", NIVEL_MIN, NIVEL_MAX),
                                     ("distancia", DIST_MIN, DIST_MAX),
                                     ("temperatura", TEMP_MIN, TEMP_MAX)):
            valor = getattr(self, nome).valor
            if valor is not None and not (minimo <= valor <= maximo):
                return f"{nome}={valor}"
        return None

    def sensor_ok(self) -> bool:
        return self.sensor == "OK" and self.nivel.valor is not None and self.fora_da_faixa() is None


class Alerta(MensagemDispositivo):
    codigo: Literal["NIVEL_CRITICO", "NIVEL_TRANSBORDO", "SENSOR_FALHA", "NORMALIZADO"]
    nivel: Grandeza


class Status(Modelo):
    deviceId: str
    online: bool
    fw: Optional[str] = None
    ip: Optional[str] = None


class Ack(MensagemDispositivo):
    commandId: str = Field(min_length=1)
    etapa: Literal["RECEBIDO", "EXECUTADO", "REJEITADO"]
    motivo: Optional[str] = None


class Estado(MensagemDispositivo):
    modo: Literal["AUTOMATICO", "MANUAL", "SEGURO"]
    motivoSeguro: Optional[str] = None
    bomba: Literal["LIGADA", "DESLIGADA"]
    ligadaHa: Grandeza
    ultimoCommandId: Optional[str] = None
    fw: str


# ---------- mensagens que o backend publica ----------

class Heartbeat(Modelo):
    v: Literal[1] = 1
    ts: datetime
    seq: int
    liberaBomba: bool
    motivo: Optional[Literal["NIVEL_ALTO", "SEM_LEITURA"]] = None


class Comando(Modelo):
    v: Literal[1] = 1
    commandId: str
    origem: Origem
    acao: Acao
    usuario: str
    ts: datetime


class ConfigMedidor(Modelo):
    """Faixas iguais às que o firmware do medidor aceita."""
    v: Literal[1] = 1
    d_vazio_cm: float = Field(ge=5, le=DIST_MAX)
    d_cheio_cm: float = Field(ge=DIST_MIN)
    alerta_baixo_pct: int = Field(ge=0, le=50)
    alerta_alto_pct: int = Field(ge=50, le=100)
    periodo_s: int = Field(ge=2, le=300)

    @model_validator(mode="after")
    def _coerencia(self):
        if self.d_cheio_cm > self.d_vazio_cm - 2:
            raise ValueError("d_cheio_cm deve ser pelo menos 2 cm menor que d_vazio_cm")
        if self.alerta_baixo_pct >= self.alerta_alto_pct:
            raise ValueError("alerta_baixo_pct deve ser menor que alerta_alto_pct")
        return self


class ConfigControlador(Modelo):
    """Faixas iguais às que o firmware do controlador aceita."""
    v: Literal[1] = 1
    t_manual_s: int = Field(ge=30, le=3600)
    t_max_ligada_s: int = Field(ge=30, le=3600)
    hb_timeout_s: int = Field(ge=15, le=300)


CONFIG_PADRAO = {
    MEDIDOR: ConfigMedidor(d_vazio_cm=23.5, d_cheio_cm=4.0, alerta_baixo_pct=10,
                           alerta_alto_pct=95, periodo_s=5),
    CONTROLADOR: ConfigControlador(t_manual_s=300, t_max_ligada_s=600, hb_timeout_s=30),
}
CONFIG_MODELO = {MEDIDOR: ConfigMedidor, CONTROLADOR: ConfigControlador}
