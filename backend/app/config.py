"""Configuração do backend, lida das variáveis de ambiente (.env do compose)."""

import os
from dataclasses import dataclass
from typing import Optional


def _texto(nome: str, padrao: Optional[str] = None) -> Optional[str]:
    valor = os.getenv(nome)
    return valor if valor not in (None, "") else padrao


def _booleano(nome: str, padrao: bool = False) -> bool:
    valor = _texto(nome)
    return padrao if valor is None else valor.strip().lower() in ("1", "true", "sim", "yes")


@dataclass(frozen=True)
class Config:
    database_url: str
    mqtt_host: str
    mqtt_porta: int
    mqtt_usuario: Optional[str]
    mqtt_senha: Optional[str]
    mqtt_tls: bool
    mqtt_ca: Optional[str]
    prefixo: str


def carregar() -> Config:
    url = _texto("DATABASE_URL")
    if url is None:
        url = "postgresql+psycopg://{u}:{s}@{h}:{p}/{d}".format(
            u=_texto("POSTGRES_USER", "iot"), s=_texto("POSTGRES_PASSWORD", ""),
            h=_texto("POSTGRES_HOST", "localhost"), p=_texto("POSTGRES_PORT_INTERNO", "5432"),
            d=_texto("POSTGRES_DB", "reservatorio"))
    tls = _booleano("MQTT_TLS")
    return Config(
        database_url=url,
        mqtt_host=_texto("MQTT_HOST", "mosquitto"),
        mqtt_porta=int(_texto("MQTT_PORT", "8883" if tls else "1883")),
        mqtt_usuario=_texto("MQTT_USUARIO"),
        mqtt_senha=_texto("MQTT_SENHA"),
        mqtt_tls=tls,
        mqtt_ca=_texto("MQTT_CA"),
        prefixo=_texto("MQTT_PREFIXO", "catolicasc-g4/reservatorio"),
    )
