"""Validação dos payloads do contrato v1 (app/contrato.py)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app import contrato as c

ENTRADA = [
    ("telemetria", c.Telemetria),
    ("alerta", c.Alerta),
    ("ack", c.Ack),
    ("estado", c.Estado),
    ("status", c.Status),
    ("cmd", c.Comando),
    ("heartbeat", c.Heartbeat),
    ("config_medidor", c.ConfigMedidor),
    ("config_controlador", c.ConfigControlador),
]


@pytest.mark.parametrize("nome,modelo", ENTRADA)
def test_exemplos_do_contrato_sao_aceitos(exemplo, nome, modelo):
    modelo.model_validate(exemplo(nome))


def test_campo_a_mais_e_ignorado(exemplo):
    t = c.Telemetria.model_validate(exemplo("telemetria", campoNovo=123))
    assert not hasattr(t, "campoNovo")


@pytest.mark.parametrize("nome,modelo", [e for e in ENTRADA if e[0] not in ("heartbeat", "cmd")
                                         and not e[0].startswith("config")])
def test_sem_v_e_invalido(exemplo, nome, modelo):
    dados = exemplo(nome)
    del dados["v"]
    with pytest.raises(ValidationError):
        modelo.model_validate(dados)


def test_versao_diferente_e_incompativel(exemplo):
    with pytest.raises(c.VersaoIncompativel):
        c.verificar_versao(exemplo("telemetria", v=2))
    c.verificar_versao(exemplo("telemetria"))   # v=1 passa
    c.verificar_versao({"sem": "v"})            # sem v fica para o Pydantic


@pytest.mark.parametrize("campo", ["deviceId", "seq", "nivel", "distancia", "temperatura", "sensor"])
def test_telemetria_sem_campo_obrigatorio(exemplo, campo):
    dados = exemplo("telemetria")
    del dados[campo]
    with pytest.raises(ValidationError):
        c.Telemetria.model_validate(dados)


@pytest.mark.parametrize("campo,unidade", [("nivel", "cm"), ("distancia", "m"), ("temperatura", "C")])
def test_telemetria_com_unidade_errada(exemplo, campo, unidade):
    dados = exemplo("telemetria")
    dados[campo]["unidade"] = unidade
    with pytest.raises(ValidationError):
        c.Telemetria.model_validate(dados)


def test_telemetria_seq_negativo(exemplo):
    with pytest.raises(ValidationError):
        c.Telemetria.model_validate(exemplo("telemetria", seq=-1))


def test_sem_ts_aceita_relogio_nao_sincronizado(exemplo):
    dados = exemplo("telemetria", relogio="NAO_SINCRONIZADO")
    del dados["ts"]
    t = c.Telemetria.model_validate(dados)
    chegada = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    assert t.instante(chegada) == chegada


def test_sem_ts_e_sem_relogio_e_invalido(exemplo):
    dados = exemplo("telemetria")
    del dados["ts"]
    with pytest.raises(ValidationError):
        c.Telemetria.model_validate(dados)


def test_instante_usa_ts_do_dispositivo(exemplo):
    t = c.Telemetria.model_validate(exemplo("telemetria"))
    assert t.instante(datetime.now(timezone.utc)) == datetime(2026, 11, 10, 22, 15, 30, tzinfo=timezone.utc)


def test_sensor_ok(exemplo):
    assert c.Telemetria.model_validate(exemplo("telemetria")).sensor_ok()
    assert not c.Telemetria.model_validate(exemplo("telemetria", sensor="FALHA")).sensor_ok()
    assert not c.Telemetria.model_validate(exemplo("telemetria", nivel={"valor": None, "unidade": "%"})).sensor_ok()


@pytest.mark.parametrize("campo,valor", [("nivel", 120.0), ("nivel", -3.0), ("distancia", 1.0),
                                         ("distancia", 500.0), ("temperatura", 85.0), ("temperatura", -20.0)])
def test_fora_da_faixa_e_falha_de_sensor_e_nao_payload_invalido(exemplo, campo, valor):
    dados = exemplo("telemetria")
    dados[campo]["valor"] = valor
    t = c.Telemetria.model_validate(dados)   # continua válido pelo contrato
    assert t.fora_da_faixa() == f"{campo}={valor}"
    assert not t.sensor_ok()


@pytest.mark.parametrize("campo,valor", [("nivel", 0.0), ("nivel", 100.0), ("distancia", 2.0),
                                         ("distancia", 400.0), ("temperatura", -10.0), ("temperatura", 60.0)])
def test_limites_da_faixa_sao_aceitos(exemplo, campo, valor):
    dados = exemplo("telemetria")
    dados[campo]["valor"] = valor
    assert c.Telemetria.model_validate(dados).fora_da_faixa() is None


def test_alerta_codigo_desconhecido(exemplo):
    with pytest.raises(ValidationError):
        c.Alerta.model_validate(exemplo("alerta", codigo="QUALQUER"))


@pytest.mark.parametrize("etapa", ["RECEBIDO", "EXECUTADO", "REJEITADO"])
def test_ack_etapas(exemplo, etapa):
    c.Ack.model_validate(exemplo("ack", etapa=etapa))


def test_ack_etapa_invalida_e_command_id_vazio(exemplo):
    with pytest.raises(ValidationError):
        c.Ack.model_validate(exemplo("ack", etapa="ENVIADO"))
    with pytest.raises(ValidationError):
        c.Ack.model_validate(exemplo("ack", commandId=""))


def test_estado_modo_invalido(exemplo):
    with pytest.raises(ValidationError):
        c.Estado.model_validate(exemplo("estado", modo="TURBO"))


def test_comando_acao_desconhecida(exemplo):
    with pytest.raises(ValidationError):
        c.Comando.model_validate(exemplo("cmd", acao="EXPLODIR"))


def test_heartbeat_gerado_tem_o_formato_do_contrato():
    hb = c.Heartbeat(ts=datetime(2026, 11, 10, 22, 16, 10, tzinfo=timezone.utc), seq=381,
                     liberaBomba=False, motivo="NIVEL_ALTO")
    dados = hb.model_dump(mode="json")
    assert dados == {"v": 1, "ts": "2026-11-10T22:16:10Z", "seq": 381, "liberaBomba": False, "motivo": "NIVEL_ALTO"}


def test_heartbeat_motivo_fora_da_lista():
    with pytest.raises(ValidationError):
        c.Heartbeat(ts=datetime.now(timezone.utc), seq=1, liberaBomba=False, motivo="OUTRO")


# ---------- config ----------

@pytest.mark.parametrize("campo,valor", [("d_vazio_cm", 4), ("d_vazio_cm", 401), ("alerta_baixo_pct", 51),
                                         ("alerta_alto_pct", 49), ("alerta_alto_pct", 101),
                                         ("periodo_s", 1), ("periodo_s", 301)])
def test_config_medidor_fora_da_faixa(exemplo, campo, valor):
    with pytest.raises(ValidationError):
        c.ConfigMedidor.model_validate(exemplo("config_medidor", **{campo: valor}))


def test_config_medidor_coerencia(exemplo):
    with pytest.raises(ValidationError):   # cheio a menos de 2 cm do vazio
        c.ConfigMedidor.model_validate(exemplo("config_medidor", d_cheio_cm=22.0))
    with pytest.raises(ValidationError):   # alerta baixo igual ao alto
        c.ConfigMedidor.model_validate(exemplo("config_medidor", alerta_baixo_pct=50, alerta_alto_pct=50))


@pytest.mark.parametrize("campo,valor", [("t_manual_s", 29), ("t_manual_s", 3601), ("t_max_ligada_s", 10),
                                         ("hb_timeout_s", 14), ("hb_timeout_s", 301)])
def test_config_controlador_fora_da_faixa(exemplo, campo, valor):
    with pytest.raises(ValidationError):
        c.ConfigControlador.model_validate(exemplo("config_controlador", **{campo: valor}))


def test_config_padrao_e_valida():
    for device, modelo in c.CONFIG_MODELO.items():
        modelo.model_validate(c.CONFIG_PADRAO[device].model_dump())
