"""Leitura e escrita no banco. Toda função abre e fecha a própria sessão."""

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .banco import (AckDb, AlertaDb, ComandoDb, ConexaoDb, ConfiguracaoDb, EstadoDb, Medicao,
                    PayloadInvalidoDb, UsuarioDb)

PONTOS_HISTORICO = 500   # no máximo isto de pontos por consulta de histórico


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


class Repositorio:
    def __init__(self, sessoes: async_sessionmaker):
        self.sessoes = sessoes

    async def _gravar(self, objeto) -> None:
        async with self.sessoes() as s:
            s.add(objeto)
            await s.commit()

    # ---------- gravação do que chega pelo MQTT ----------
    async def gravar_medicao(self, **campos) -> None:
        await self._gravar(Medicao(**campos))

    async def gravar_alerta(self, **campos) -> None:
        await self._gravar(AlertaDb(**campos))

    async def gravar_estado(self, **campos) -> None:
        await self._gravar(EstadoDb(**campos))

    async def gravar_conexao(self, **campos) -> None:
        await self._gravar(ConexaoDb(**campos))

    async def gravar_invalido(self, topico: str, codigo: str, erro: str, payload: bytes) -> None:
        await self._gravar(PayloadInvalidoDb(
            recebido_em=agora_utc(), topico=topico[:200], codigo=codigo, erro=erro[:2000],
            payload=payload.decode("utf-8", errors="replace")[:2000]))

    # ---------- ciclo de comando ----------
    async def gravar_comando_enviado(self, command_id: str, acao: str, origem: str, usuario: str,
                                     enviado_em: datetime) -> None:
        await self._gravar(ComandoDb(command_id=command_id, acao=acao, origem=origem, usuario=usuario,
                                     status="ENVIADO", enviado_em=enviado_em))

    async def gravar_ack(self, command_id: str, etapa: str, motivo: Optional[str], ts: datetime,
                         recebido_em: datetime) -> None:
        """Grava o ack e avança o status do comando (nunca para trás)."""
        async with self.sessoes() as s:
            s.add(AckDb(command_id=command_id, etapa=etapa, motivo=motivo, ts=ts, recebido_em=recebido_em))
            cmd = (await s.execute(select(ComandoDb).where(ComandoDb.command_id == command_id))).scalar_one_or_none()
            if cmd is not None:
                if etapa == "RECEBIDO" and cmd.status == "ENVIADO":
                    cmd.status, cmd.recebido_em = "RECEBIDO", recebido_em
                elif etapa in ("EXECUTADO", "REJEITADO") and cmd.status in ("ENVIADO", "RECEBIDO", "EXPIRADO"):
                    cmd.status = etapa
                    cmd.executado_em = recebido_em
                    cmd.latencia_ms = int((recebido_em - cmd.enviado_em).total_seconds() * 1000)
                    if etapa == "REJEITADO":
                        cmd.motivo = motivo
                # Um REJEITADO por COMANDO_DUPLICADO de reenvio não desfaz o EXECUTADO original.
            await s.commit()

    async def confirmar_comando(self, command_id: str, quando: datetime) -> bool:
        """EXECUTADO vira CONFIRMADO quando o estado publicado traz este commandId."""
        async with self.sessoes() as s:
            r = await s.execute(update(ComandoDb)
                                .where(ComandoDb.command_id == command_id, ComandoDb.status == "EXECUTADO")
                                .values(status="CONFIRMADO", confirmado_em=quando))
            await s.commit()
            return r.rowcount > 0

    async def expirar_comandos(self, segundos: int) -> int:
        limite = agora_utc() - timedelta(seconds=segundos)
        async with self.sessoes() as s:
            r = await s.execute(update(ComandoDb)
                                .where(ComandoDb.status.in_(("ENVIADO", "RECEBIDO")), ComandoDb.enviado_em < limite)
                                .values(status="EXPIRADO", motivo="SEM_RESPOSTA"))
            await s.commit()
            return r.rowcount

    async def comando(self, command_id: str) -> Optional[dict]:
        async with self.sessoes() as s:
            cmd = (await s.execute(select(ComandoDb).where(ComandoDb.command_id == command_id))).scalar_one_or_none()
            if cmd is None:
                return None
            acks = (await s.execute(select(AckDb).where(AckDb.command_id == command_id)
                                    .order_by(AckDb.recebido_em))).scalars().all()
            d = _dict(cmd)
            d["acks"] = [_dict(a, sem=("id", "command_id")) for a in acks]
            return d

    async def comandos(self, limite: int) -> list[dict]:
        async with self.sessoes() as s:
            linhas = (await s.execute(select(ComandoDb).order_by(ComandoDb.enviado_em.desc()).limit(limite))).scalars()
            return [_dict(c) for c in linhas]

    # ---------- consultas para a API ----------
    async def ultima_medicao(self) -> Optional[dict]:
        async with self.sessoes() as s:
            m = (await s.execute(select(Medicao).order_by(Medicao.ts.desc()).limit(1))).scalar_one_or_none()
            return _dict(m) if m else None

    async def historico(self, inicio: datetime, fim: datetime) -> dict:
        """Médias por intervalo, com no máximo PONTOS_HISTORICO pontos."""
        segundos = max(5, math.ceil((fim - inicio).total_seconds() / PONTOS_HISTORICO))
        sql = text("""
            SELECT date_bin(make_interval(secs => :b), ts, TIMESTAMPTZ '2000-01-01') AS t,
                   avg(nivel) AS nivel, avg(temperatura) AS temperatura, avg(distancia) AS distancia,
                   count(*) AS amostras
              FROM medicoes
             WHERE ts BETWEEN :i AND :f AND sensor = 'OK'
             GROUP BY t ORDER BY t""")
        async with self.sessoes() as s:
            linhas = (await s.execute(sql, {"b": segundos, "i": inicio, "f": fim})).mappings().all()
        return {"intervalo_s": segundos, "pontos": [
            {"ts": l["t"], "nivel": _arredonda(l["nivel"]), "temperatura": _arredonda(l["temperatura"]),
             "distancia": _arredonda(l["distancia"]), "amostras": l["amostras"]} for l in linhas]}

    async def analise(self, inicio: datetime, fim: datetime) -> dict:
        """Nível médio, mínimo e máximo, tempo de bomba ligada e número de acionamentos."""
        async with self.sessoes() as s:
            n = (await s.execute(text("""
                SELECT avg(nivel) AS media, min(nivel) AS minimo, max(nivel) AS maximo, count(*) AS amostras
                  FROM medicoes WHERE ts BETWEEN :i AND :f AND sensor = 'OK'"""),
                {"i": inicio, "f": fim})).mappings().one()
            anterior = (await s.execute(select(EstadoDb.bomba).where(EstadoDb.recebido_em < inicio)
                                        .order_by(EstadoDb.recebido_em.desc()).limit(1))).scalar_one_or_none()
            estados = (await s.execute(select(EstadoDb.recebido_em, EstadoDb.bomba)
                                       .where(EstadoDb.recebido_em.between(inicio, fim))
                                       .order_by(EstadoDb.recebido_em))).all()
        ligada_s, acionamentos = 0.0, 0
        bomba, desde = anterior, inicio
        for quando, nova in estados:
            if bomba == "LIGADA":
                ligada_s += (quando - desde).total_seconds()
            if nova == "LIGADA" and bomba != "LIGADA":
                acionamentos += 1
            bomba, desde = nova, quando
        if bomba == "LIGADA":
            ligada_s += (min(fim, agora_utc()) - desde).total_seconds()
        return {"inicio": inicio, "fim": fim,
                "nivel_medio": _arredonda(n["media"]), "nivel_minimo": _arredonda(n["minimo"]),
                "nivel_maximo": _arredonda(n["maximo"]), "amostras": n["amostras"],
                "bomba_ligada_s": round(ligada_s), "acionamentos": acionamentos}

    async def alertas(self, inicio: datetime, fim: datetime, limite: int) -> list[dict]:
        async with self.sessoes() as s:
            linhas = (await s.execute(select(AlertaDb).where(AlertaDb.ts.between(inicio, fim))
                                      .order_by(AlertaDb.ts.desc()).limit(limite))).scalars()
            return [_dict(a) for a in linhas]

    async def ultimo_estado(self) -> Optional[dict]:
        async with self.sessoes() as s:
            e = (await s.execute(select(EstadoDb).order_by(EstadoDb.recebido_em.desc()).limit(1))).scalar_one_or_none()
            return _dict(e) if e else None

    async def ultima_conexao(self, device_id: str) -> Optional[dict]:
        async with self.sessoes() as s:
            c = (await s.execute(select(ConexaoDb).where(ConexaoDb.device_id == device_id)
                                 .order_by(ConexaoDb.recebido_em.desc()).limit(1))).scalar_one_or_none()
            return _dict(c) if c else None

    async def invalidos(self, limite: int) -> list[dict]:
        async with self.sessoes() as s:
            linhas = (await s.execute(select(PayloadInvalidoDb).order_by(PayloadInvalidoDb.recebido_em.desc())
                                      .limit(limite))).scalars()
            return [_dict(p) for p in linhas]

    # ---------- configuração ----------
    async def ultima_config(self, device_id: str) -> Optional[dict]:
        async with self.sessoes() as s:
            c = (await s.execute(select(ConfiguracaoDb).where(ConfiguracaoDb.device_id == device_id)
                                 .order_by(ConfiguracaoDb.enviado_em.desc()).limit(1))).scalar_one_or_none()
            return _dict(c) if c else None

    async def gravar_config(self, device_id: str, payload: dict, usuario: str) -> None:
        await self._gravar(ConfiguracaoDb(device_id=device_id, payload=payload,
                                          enviado_em=agora_utc(), usuario=usuario))

    # ---------- usuários ----------
    async def usuario(self, nome: str) -> Optional[UsuarioDb]:
        async with self.sessoes() as s:
            return (await s.execute(select(UsuarioDb).where(UsuarioDb.nome == nome))).scalar_one_or_none()

    async def salvar_usuario(self, nome: str, senha_hash: str, perfil: str) -> None:
        """Cria o usuário, ou atualiza senha e perfil se ele já existe."""
        async with self.sessoes() as s:
            u = (await s.execute(select(UsuarioDb).where(UsuarioDb.nome == nome))).scalar_one_or_none()
            if u is None:
                s.add(UsuarioDb(nome=nome, senha_hash=senha_hash, perfil=perfil, criado_em=agora_utc()))
            else:
                u.senha_hash, u.perfil = senha_hash, perfil
            await s.commit()


def _arredonda(valor) -> Optional[float]:
    return None if valor is None else round(float(valor), 1)


def _dict(objeto, sem: tuple = ("id",)) -> dict:
    return {c.name: getattr(objeto, c.name) for c in objeto.__table__.columns if c.name not in sem}
