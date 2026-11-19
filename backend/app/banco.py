"""
Tabelas do backend (SQLAlchemy). O esquema é criado e versionado pelo
Alembic (backend/alembic/versions); este arquivo só descreve as tabelas
para o código. As tabelas leituras e eventos do coletor da N1 (n1/db/init.sql)
continuam no banco, sem uso pelo backend.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _id():
    return mapped_column(BigInteger, primary_key=True)


def _quando(indice: bool = False):
    return mapped_column(DateTime(timezone=True), index=indice)


class Medicao(Base):
    """Toda telemetria do medidor (periódica e por evento)."""
    __tablename__ = "medicoes"
    id: Mapped[int] = _id()
    ts: Mapped[datetime] = _quando(indice=True)          # ts do medidor, ou chegada sem NTP
    recebido_em: Mapped[datetime] = _quando()
    device_id: Mapped[str] = mapped_column(String(40))
    seq: Mapped[Optional[int]] = mapped_column(BigInteger)
    nivel: Mapped[Optional[float]] = mapped_column(Float)
    distancia: Mapped[Optional[float]] = mapped_column(Float)
    temperatura: Mapped[Optional[float]] = mapped_column(Float)
    sensor: Mapped[str] = mapped_column(String(10))       # OK ou FALHA (inclui fora da faixa)
    relogio_sincronizado: Mapped[bool] = mapped_column(Boolean)


class AlertaDb(Base):
    """Alertas: os do medidor e os gerados pelo backend (ex.: offline, SEGURO)."""
    __tablename__ = "alertas"
    id: Mapped[int] = _id()
    ts: Mapped[datetime] = _quando(indice=True)
    recebido_em: Mapped[datetime] = _quando()
    device_id: Mapped[str] = mapped_column(String(40))
    codigo: Mapped[str] = mapped_column(String(40))
    nivel: Mapped[Optional[float]] = mapped_column(Float)
    detalhe: Mapped[Optional[str]] = mapped_column(Text)


class ComandoDb(Base):
    """Ciclo de cada comando: ENVIADO, RECEBIDO/REJEITADO, EXECUTADO, CONFIRMADO (ou EXPIRADO)."""
    __tablename__ = "comandos"
    id: Mapped[int] = _id()
    command_id: Mapped[str] = mapped_column(String(64), unique=True)
    acao: Mapped[str] = mapped_column(String(20))
    origem: Mapped[str] = mapped_column(String(10))
    usuario: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), index=True)
    motivo: Mapped[Optional[str]] = mapped_column(String(40))
    enviado_em: Mapped[datetime] = _quando(indice=True)
    recebido_em: Mapped[Optional[datetime]] = _quando()
    executado_em: Mapped[Optional[datetime]] = _quando()
    confirmado_em: Mapped[Optional[datetime]] = _quando()
    latencia_ms: Mapped[Optional[int]] = mapped_column(Integer)   # envio até EXECUTADO/REJEITADO


class AckDb(Base):
    __tablename__ = "acks"
    id: Mapped[int] = _id()
    command_id: Mapped[str] = mapped_column(String(64), index=True)
    etapa: Mapped[str] = mapped_column(String(20))
    motivo: Mapped[Optional[str]] = mapped_column(String(40))
    ts: Mapped[datetime] = _quando()
    recebido_em: Mapped[datetime] = _quando()


class EstadoDb(Base):
    """Cada estado publicado pelo controlador."""
    __tablename__ = "estado"
    id: Mapped[int] = _id()
    ts: Mapped[datetime] = _quando()
    recebido_em: Mapped[datetime] = _quando(indice=True)
    device_id: Mapped[str] = mapped_column(String(40))
    modo: Mapped[str] = mapped_column(String(20))
    motivo_seguro: Mapped[Optional[str]] = mapped_column(String(40))
    bomba: Mapped[str] = mapped_column(String(10))
    ligada_ha_s: Mapped[Optional[float]] = mapped_column(Float)
    ultimo_command_id: Mapped[Optional[str]] = mapped_column(String(64))
    fw: Mapped[Optional[str]] = mapped_column(String(20))


class ConexaoDb(Base):
    """Status online/offline (inclui o Last Will) de cada dispositivo."""
    __tablename__ = "conexoes"
    id: Mapped[int] = _id()
    recebido_em: Mapped[datetime] = _quando(indice=True)
    device_id: Mapped[str] = mapped_column(String(40), index=True)
    online: Mapped[bool] = mapped_column(Boolean)
    fw: Mapped[Optional[str]] = mapped_column(String(20))
    ip: Mapped[Optional[str]] = mapped_column(String(45))


class ConfiguracaoDb(Base):
    """Configurações publicadas pelo backend no tópico config."""
    __tablename__ = "configuracoes"
    id: Mapped[int] = _id()
    device_id: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    enviado_em: Mapped[datetime] = _quando()
    usuario: Mapped[str] = mapped_column(String(60))


class PayloadInvalidoDb(Base):
    """Mensagens descartadas por não seguirem o contrato."""
    __tablename__ = "payloads_invalidos"
    id: Mapped[int] = _id()
    recebido_em: Mapped[datetime] = _quando(indice=True)
    topico: Mapped[str] = mapped_column(String(200))
    codigo: Mapped[str] = mapped_column(String(40))     # PAYLOAD_INVALIDO ou VERSAO_INCOMPATIVEL
    erro: Mapped[str] = mapped_column(Text)
    payload: Mapped[str] = mapped_column(Text)


class UsuarioDb(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = _id()
    nome: Mapped[str] = mapped_column(String(60), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(100))
    perfil: Mapped[str] = mapped_column(String(20))      # admin ou visualizador
    criado_em: Mapped[datetime] = _quando()


def criar_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def criar_sessoes(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
