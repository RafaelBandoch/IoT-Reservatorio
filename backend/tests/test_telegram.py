"""Alertas pelo Telegram (app/telegram.py), sem rede: o envio é trocado por uma lista."""

import asyncio
from datetime import datetime, timezone

from app.telegram import REPETICAO_S, Telegram, criar_do_ambiente, formatar


class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def bot(enviadas, relogio=None):
    async def enviar(msg):
        enviadas.append(msg)
    return Telegram("token", "123", enviar=enviar, relogio=relogio or Relogio())


async def esvaziar(t):
    await asyncio.sleep(0)
    while t._fila is not None and not t._fila.empty():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0)


def test_conexao_nao_repete_o_nome():
    msg = formatar("rsv-controlador-01", "ONLINE", "rsv-controlador-01 voltou",
                   datetime(2026, 11, 18, 18, 30, tzinfo=timezone.utc))
    assert "voltou" not in msg and "Dispositivo: controlador (rsv-controlador-01)" in msg


def test_formatar():
    msg = formatar("rsv-medidor-01", "NIVEL_CRITICO", "nível 8 %",
                   datetime(2026, 11, 18, 18, 30, tzinfo=timezone.utc))
    assert msg.splitlines() == [
        "AquaNível: ALERTA: nível crítico na caixa d'água",
        "Nível 8 %",
        "Dispositivo: medidor (rsv-medidor-01)",
        "Horário: 18/11 15:30:00",
    ]


async def test_envia_e_nao_repete_em_sequencia():
    enviadas, relogio = [], Relogio()
    t = bot(enviadas, relogio)
    await t("rsv-medidor-01", "SENSOR_FALHA", "falha")
    await t("rsv-medidor-01", "SENSOR_FALHA", "leitura fora da faixa")   # mesmo código, logo depois
    await esvaziar(t)
    assert len(enviadas) == 1
    relogio.t += REPETICAO_S + 1                                          # muito depois: avisa de novo
    await t("rsv-medidor-01", "SENSOR_FALHA", "falha")
    await esvaziar(t)
    assert len(enviadas) == 2
    await t.fechar()


async def test_alerta_diferente_ou_outro_dispositivo_passa():
    enviadas = []
    t = bot(enviadas)
    await t("rsv-medidor-01", "NIVEL_CRITICO", "nível 8 %")
    await t("rsv-medidor-01", "NORMALIZADO", "nível 15 %")
    await t("rsv-controlador-01", "OFFLINE", "rsv-controlador-01 ficou offline")
    await t("rsv-medidor-01", "OFFLINE", "rsv-medidor-01 ficou offline")
    await esvaziar(t)
    assert len(enviadas) == 4
    await t.fechar()


async def test_modo_seguro_distingue_o_motivo():
    enviadas = []
    t = bot(enviadas)
    await t("rsv-controlador-01", "MODO_SEGURO", "bomba desligada: sem comunicação com o backend")
    await t("rsv-controlador-01", "MODO_SEGURO", "bomba desligada: atualização de firmware")
    await t("rsv-controlador-01", "MODO_SEGURO", "bomba desligada: atualização de firmware")
    await esvaziar(t)
    assert len(enviadas) == 2
    await t.fechar()


async def test_sem_internet_reenvia_na_ordem_quando_volta():
    enviadas, esperas, falhas = [], [], [3]

    async def enviar(msg):
        if falhas[0]:
            falhas[0] -= 1
            raise OSError("Name or service not known")
        enviadas.append(msg)

    async def esperar(s):
        esperas.append(s)

    t = Telegram("token", "123", enviar=enviar, relogio=Relogio(), esperar=esperar)
    await t("rsv-controlador-01", "OFFLINE", "rsv-controlador-01 ficou offline")
    await t("rsv-controlador-01", "ONLINE", "rsv-controlador-01 voltou")
    await esvaziar(t)
    await asyncio.sleep(0.05)
    assert esperas == [5, 10, 20]                   # recuo entre as tentativas
    assert "sem conexão" in enviadas[0] and "conectado de novo" in enviadas[1]   # nada perdido, na ordem
    await t.fechar()


async def test_desiste_depois_do_tempo_maximo():
    from app.telegram import REENVIO_MAX_S
    enviadas, esperas = [], []

    async def enviar(msg):
        if "crítico" in msg:
            raise OSError("sem rede")
        enviadas.append(msg)

    async def esperar(s):
        esperas.append(s)

    t = Telegram("token", "123", enviar=enviar, relogio=Relogio(), esperar=esperar)
    await t("rsv-medidor-01", "NIVEL_CRITICO", "nível 8 %")
    await t("rsv-medidor-01", "NORMALIZADO", "nível 15 %")
    await esvaziar(t)
    await asyncio.sleep(0.05)
    assert sum(esperas) <= REENVIO_MAX_S
    assert len(enviadas) == 1 and "normalizado" in enviadas[0]   # o seguinte não fica preso
    await t.fechar()


def test_sem_configuracao_fica_desligado(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    assert criar_do_ambiente() is None
    monkeypatch.setenv("TELEGRAM_TOKEN", "abc")
    assert isinstance(criar_do_ambiente(), Telegram)
