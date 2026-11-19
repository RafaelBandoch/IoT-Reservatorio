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


async def test_falha_no_envio_nao_para_a_fila():
    enviadas = []

    async def enviar(msg):
        if not enviadas:
            enviadas.append("falhou")
            raise RuntimeError("Telegram fora do ar")
        enviadas.append(msg)

    t = Telegram("token", "123", enviar=enviar, relogio=Relogio())
    await t("rsv-medidor-01", "NIVEL_CRITICO", "nível 8 %")
    await t("rsv-medidor-01", "NORMALIZADO", "nível 15 %")
    await esvaziar(t)
    assert enviadas[0] == "falhou" and "normalizado" in enviadas[1]
    await t.fechar()


def test_sem_configuracao_fica_desligado(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    assert criar_do_ambiente() is None
    monkeypatch.setenv("TELEGRAM_TOKEN", "abc")
    assert isinstance(criar_do_ambiente(), Telegram)
