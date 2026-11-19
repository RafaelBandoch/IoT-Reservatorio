"""Regras puras de decisão (app/logica.py). O tempo é simulado: nada de sleep."""

import pytest

from app.logica import (LEITURA_VELHA_S, NIVEL_ALTO, NIVEL_DESLIGA, NIVEL_LIBERA, NIVEL_LIGA,
                        TIMEOUT_REGRA_S, Regras)


def regras_prontas(nivel=50.0, modo="AUTOMATICO", bomba="DESLIGADA", agora=0.0) -> Regras:
    r = Regras()
    r.registrar_status(medidor=True, online=True)
    r.registrar_status(medidor=False, online=True)
    r.registrar_estado(modo, bomba)
    r.registrar_telemetria(nivel, True, agora)
    return r


# ---------- liberaBomba ----------

def test_sem_nenhuma_leitura_nao_libera():
    assert Regras().liberacao(0) == (False, "SEM_LEITURA")


def test_leitura_normal_libera():
    assert regras_prontas(50).liberacao(1) == (True, None)


def test_leitura_velha_nao_libera():
    r = regras_prontas(50, agora=100)
    assert r.liberacao(100 + LEITURA_VELHA_S) == (True, None)          # exatamente 30 s ainda vale
    assert r.liberacao(100 + LEITURA_VELHA_S + 0.1) == (False, "SEM_LEITURA")


def test_sensor_em_falha_nao_libera():
    r = regras_prontas(50)
    r.registrar_telemetria(50, False, 1)
    assert r.liberacao(1) == (False, "SEM_LEITURA")


def test_nivel_nulo_nao_libera():
    r = regras_prontas(50)
    r.registrar_telemetria(None, True, 1)
    assert r.liberacao(1) == (False, "SEM_LEITURA")


def test_medidor_offline_nao_libera():
    r = regras_prontas(50)
    r.registrar_status(medidor=True, online=False)
    assert r.liberacao(1) == (False, "SEM_LEITURA")
    r.registrar_status(medidor=True, online=True)
    assert r.liberacao(1) == (True, None)


def test_sem_leitura_tem_prioridade_sobre_nivel_alto():
    r = regras_prontas(95)
    r.registrar_status(medidor=True, online=False)
    assert r.liberacao(1) == (False, "SEM_LEITURA")


# ---------- margem 90/87 ----------

@pytest.mark.parametrize("nivel", [NIVEL_ALTO, 95, 100])
def test_nivel_alto_bloqueia(nivel):
    assert regras_prontas(nivel).liberacao(1) == (False, "NIVEL_ALTO")


def test_margem_so_libera_ao_cair_ate_87():
    r = regras_prontas(91)
    for nivel in (89.9, 88, NIVEL_LIBERA + 0.1):
        r.registrar_telemetria(nivel, True, 1)
        assert r.liberacao(1) == (False, "NIVEL_ALTO"), nivel
    r.registrar_telemetria(NIVEL_LIBERA, True, 1)
    assert r.liberacao(1) == (True, None)
    r.registrar_telemetria(89, True, 1)   # entre 87 e 90 vindo de baixo: continua liberado
    assert r.liberacao(1) == (True, None)


def test_leitura_com_falha_nao_mexe_na_margem():
    r = regras_prontas(95)
    r.registrar_telemetria(10, False, 1)   # leitura com falha não pode soltar o bloqueio
    r.registrar_telemetria(89, True, 2)
    assert r.liberacao(2) == (False, "NIVEL_ALTO")


# ---------- detecção de mudança do liberaBomba ----------

def test_liberacao_mudou():
    r = regras_prontas(50)
    assert not r.liberacao_mudou(1)          # nada publicado ainda
    r.heartbeat_publicado(*r.liberacao(1))
    assert not r.liberacao_mudou(1)
    r.registrar_telemetria(92, True, 2)
    assert r.liberacao_mudou(2)
    r.heartbeat_publicado(*r.liberacao(2))
    assert not r.liberacao_mudou(2)


def test_leitura_envelhecendo_muda_liberacao():
    r = regras_prontas(50, agora=0)
    r.heartbeat_publicado(*r.liberacao(0))
    assert not r.liberacao_mudou(LEITURA_VELHA_S)
    assert r.liberacao_mudou(LEITURA_VELHA_S + 1)


# ---------- histerese 10/90 ----------

@pytest.mark.parametrize("nivel", [0, 5, NIVEL_LIGA])
def test_liga_no_nivel_baixo(nivel):
    assert regras_prontas(nivel).decidir_regra(1) == "LIGAR"


@pytest.mark.parametrize("nivel", [NIVEL_DESLIGA, 95, 100])
def test_desliga_no_nivel_alto(nivel):
    assert regras_prontas(nivel, bomba="LIGADA").decidir_regra(1) == "DESLIGAR"


@pytest.mark.parametrize("nivel,bomba", [(11, "DESLIGADA"), (50, "DESLIGADA"), (89, "LIGADA"),
                                         (50, "LIGADA"), (5, "LIGADA"), (95, "DESLIGADA")])
def test_faixa_morta_e_bomba_ja_no_estado_certo(nivel, bomba):
    assert regras_prontas(nivel, bomba=bomba).decidir_regra(1) is None


@pytest.mark.parametrize("modo", ["MANUAL", "SEGURO", None])
def test_regra_so_age_em_automatico(modo):
    assert regras_prontas(5, modo=modo).decidir_regra(1) is None
    assert regras_prontas(95, modo=modo, bomba="LIGADA").decidir_regra(1) is None


def test_regra_nao_age_com_controlador_offline():
    r = regras_prontas(5)
    r.registrar_status(medidor=False, online=False)
    assert r.decidir_regra(1) is None


def test_regra_nao_age_com_sensor_em_falha():
    r = regras_prontas(5)
    r.registrar_telemetria(5, False, 1)
    assert r.decidir_regra(1) is None


def test_nao_liga_sem_liberacao():
    r = regras_prontas(5, agora=0)
    assert r.decidir_regra(LEITURA_VELHA_S + 1) is None       # leitura velha
    r = regras_prontas(5)
    r.registrar_status(medidor=True, online=False)
    assert r.decidir_regra(1) is None                         # medidor offline


def test_desliga_mesmo_sem_liberacao():
    """Desligar é sempre seguro: a regra manda DESLIGAR mesmo com bloqueio."""
    r = regras_prontas(95, bomba="LIGADA")
    assert r.liberacao(1) == (False, "NIVEL_ALTO")
    assert r.decidir_regra(1) == "DESLIGAR"


# ---------- um comando por vez e timeout ----------

def test_um_comando_da_regra_por_vez():
    r = regras_prontas(5)
    assert r.decidir_regra(1) == "LIGAR"
    r.regra_enviada("c-1", "LIGAR", 1, origem=0.9)
    assert r.decidir_regra(1.1) is None
    assert r.decidir_regra(1 + TIMEOUT_REGRA_S - 0.01) is None
    assert not r.regra_aguardando_expirou(1 + TIMEOUT_REGRA_S - 0.01)
    assert r.regra_aguardando_expirou(1 + TIMEOUT_REGRA_S)
    assert r.decidir_regra(1 + TIMEOUT_REGRA_S) == "LIGAR"


def test_executado_libera_a_regra_e_mede_latencia():
    r = regras_prontas(5)
    r.regra_enviada("c-1", "LIGAR", 10.0, origem=9.5)
    resp = r.resposta("c-1", "EXECUTADO", 10.8)
    assert resp.command_id == "c-1" and resp.etapa == "EXECUTADO"
    assert resp.ms_desde_envio == pytest.approx(800)
    assert resp.ms_desde_telemetria == pytest.approx(1300)
    assert r.cmd_regra is None
    assert r.decidir_regra(10.9) == "LIGAR"   # bomba ainda DESLIGADA no estado: pode mandar de novo


def test_rejeitado_tambem_libera_a_regra():
    r = regras_prontas(5)
    r.regra_enviada("c-1", "LIGAR", 1, origem=None)
    resp = r.resposta("c-1", "REJEITADO", 2)
    assert resp.etapa == "REJEITADO" and resp.ms_desde_telemetria is None
    assert r.cmd_regra is None


def test_recebido_e_outro_comando_nao_liberam_a_regra():
    r = regras_prontas(5)
    r.regra_enviada("c-1", "LIGAR", 1, origem=None)
    assert r.resposta("c-1", "RECEBIDO", 1.2) is None
    assert r.resposta("c-outro", "EXECUTADO", 1.3) is None
    assert r.cmd_regra is not None


def test_resposta_sem_comando_da_regra():
    assert Regras().resposta("c-1", "EXECUTADO", 1) is None
