"""
Ponte MQTT (app/ponte.py) com repositório e cliente falsos: valida,
grava, decide e publica, sem broker nem banco.
"""

import json
import time

import pytest

from conftest import PREFIXO


def nivel(valor):
    return {"valor": valor, "unidade": "%"}


async def preparar(ponte, enviar, exemplo, modo="AUTOMATICO", bomba="DESLIGADA"):
    """Medidor e controlador online, estado conhecido e um heartbeat já publicado."""
    await enviar("rsv-medidor-01", "status", {"v": 1, "deviceId": "rsv-medidor-01", "online": True, "fw": "2.0.0"})
    await enviar("rsv-controlador-01", "status", {"v": 1, "deviceId": "rsv-controlador-01", "online": True})
    await enviar("rsv-controlador-01", "estado", exemplo("estado", modo=modo, bomba=bomba, ultimoCommandId=None))
    await ponte._publicar_heartbeat(time.monotonic())
    ponte.cliente.publicados.clear()


# ---------- payload inválido ----------

@pytest.mark.parametrize("tipo,bruto,codigo", [
    ("telemetria", b"{nao e json", "PAYLOAD_INVALIDO"),
    ("telemetria", b"\xff\xfe", "PAYLOAD_INVALIDO"),
    ("ack", b"[1, 2, 3]", "PAYLOAD_INVALIDO"),
])
async def test_json_quebrado_e_registrado(ponte, repo, tipo, bruto, codigo):
    disp = "rsv-medidor-01" if tipo == "telemetria" else "rsv-controlador-01"
    await ponte.tratar(f"{PREFIXO}/{disp}/{tipo}", bruto)
    assert [i["codigo"] for i in repo.invalidos] == [codigo]
    assert repo.medicoes == [] and repo.acks == []


async def test_versao_incompativel(enviar, repo, exemplo):
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", v=2))
    assert repo.invalidos[0]["codigo"] == "VERSAO_INCOMPATIVEL"
    assert repo.medicoes == []


async def test_campo_faltando(enviar, repo, exemplo):
    dados = exemplo("telemetria")
    del dados["seq"]
    await enviar("rsv-medidor-01", "telemetria", dados)
    assert repo.invalidos[0]["codigo"] == "PAYLOAD_INVALIDO"
    assert "seq" in repo.invalidos[0]["erro"]


async def test_unidade_errada(enviar, repo, exemplo):
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", nivel={"valor": 50, "unidade": "cm"}))
    assert repo.invalidos[0]["codigo"] == "PAYLOAD_INVALIDO"


async def test_device_id_que_nao_bate_com_o_topico(enviar, repo, exemplo):
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", deviceId="rsv-medidor-99"))
    assert repo.invalidos[0]["codigo"] == "PAYLOAD_INVALIDO"
    assert "não bate" in repo.invalidos[0]["erro"]
    assert repo.medicoes == []


async def test_topicos_ignorados(ponte, repo):
    await ponte.tratar("outro/prefixo/rsv-medidor-01/telemetria", b"{}")
    await ponte.tratar(f"{PREFIXO}/rsv-medidor-01/telemetria", b"")      # retain apagado
    await ponte.tratar(f"{PREFIXO}/rsv-medidor-01/desconhecido", b"{}")
    assert repo.invalidos == [] and repo.medicoes == []


async def test_erro_no_banco_nao_derruba_a_ponte(enviar, repo, exemplo):
    repo.falhar_medicao = True
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria"))   # não levanta
    repo.falhar_medicao = False
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", seq=1043))
    assert [m["seq"] for m in repo.medicoes] == [1043]


async def test_config_retida_atualiza_o_retrato(enviar, ponte, exemplo):
    await enviar("rsv-medidor-01", "config", exemplo("config_medidor"))
    assert ponte.vivo["config"]["rsv-medidor-01"]["periodo_s"] == 10


# ---------- telemetria e regra ----------

async def test_telemetria_valida_e_gravada(enviar, ponte, repo, exemplo):
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria"))
    m = repo.medicoes[0]
    assert m["nivel"] == 63.4 and m["sensor"] == "OK" and m["relogio_sincronizado"] is True
    assert ponte.vivo["telemetria"]["nivel"] == 63.4


async def test_nivel_baixo_publica_heartbeat_e_depois_ligar(enviar, ponte, repo, exemplo):
    await preparar(ponte, enviar, exemplo)
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", nivel=nivel(5)))
    tipos = [p["topico"].rsplit("/", 1)[1] for p in ponte.cliente.publicados]
    assert tipos == ["heartbeat", "cmd"]   # liberação nova sai antes do comando
    hb, cmd = ponte.cliente.publicados
    assert hb["topico"] == f"{PREFIXO}/backend/heartbeat" and hb["payload"]["liberaBomba"] is True
    assert cmd["topico"] == f"{PREFIXO}/rsv-controlador-01/cmd" and cmd["qos"] == 1 and not cmd["retain"]
    assert cmd["payload"]["acao"] == "LIGAR" and cmd["payload"]["origem"] == "regra"
    assert repo.comandos[0]["acao"] == "LIGAR"


async def test_nivel_alto_bloqueia_e_desliga(enviar, ponte, exemplo):
    await preparar(ponte, enviar, exemplo, bomba="LIGADA")
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", nivel=nivel(50)))
    ponte.cliente.publicados.clear()
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", nivel=nivel(94)))
    hb, cmd = ponte.cliente.publicados
    assert hb["payload"]["liberaBomba"] is False and hb["payload"]["motivo"] == "NIVEL_ALTO"
    assert cmd["payload"]["acao"] == "DESLIGAR"


async def test_ciclo_ack_libera_a_regra_e_resolve_a_espera(enviar, ponte, repo, exemplo):
    await preparar(ponte, enviar, exemplo)
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", nivel=nivel(5)))
    cid = ponte.cliente.de_tipo("cmd")[0]["payload"]["commandId"]
    assert ponte.regras.cmd_regra.command_id == cid
    await enviar("rsv-controlador-01", "ack", exemplo("ack", commandId=cid, etapa="RECEBIDO"))
    assert ponte.regras.cmd_regra is not None
    await enviar("rsv-controlador-01", "ack", exemplo("ack", commandId=cid, etapa="EXECUTADO"))
    assert ponte.regras.cmd_regra is None
    assert [a["etapa"] for a in repo.acks] == ["RECEBIDO", "EXECUTADO"]
    assert await ponte.esperar_resposta(cid, 0.1) == {"etapa": "EXECUTADO", "motivo": None}
    # O estado com o ultimoCommandId confirma o comando.
    await enviar("rsv-controlador-01", "estado",
                 exemplo("estado", modo="AUTOMATICO", bomba="LIGADA", ultimoCommandId=cid))
    assert repo.confirmados == [cid]


async def test_comando_manual_rejeitado(ponte, enviar, exemplo):
    cid = await ponte.enviar_comando("LIGAR", "manual", "admin")
    await enviar("rsv-controlador-01", "ack", exemplo("ack", commandId=cid, etapa="REJEITADO", motivo="NIVEL_ALTO"))
    assert await ponte.esperar_resposta(cid, 0.1) == {"etapa": "REJEITADO", "motivo": "NIVEL_ALTO"}


async def test_esperar_resposta_sem_ack_devolve_none(ponte):
    cid = await ponte.enviar_comando("DESLIGAR", "manual", "admin")
    assert await ponte.esperar_resposta(cid, 0.05) is None


async def test_sem_conexao_nao_envia(ponte):
    from app.ponte import SemConexao
    ponte.cliente = None
    with pytest.raises(SemConexao):
        await ponte.enviar_comando("LIGAR", "manual", "admin")


async def test_regra_parada_em_manual(enviar, ponte, exemplo):
    await preparar(ponte, enviar, exemplo, modo="MANUAL")
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", nivel=nivel(5)))
    assert ponte.cliente.de_tipo("cmd") == []


# ---------- alertas gerados pelo backend ----------

async def test_fora_da_faixa_gera_sensor_falha_uma_vez(enviar, repo, avisos, exemplo):
    for seq in (1, 2, 3):
        await enviar("rsv-medidor-01", "telemetria",
                     exemplo("telemetria", seq=seq, temperatura={"valor": 85.0, "unidade": "°C"}))
    assert [a["codigo"] for a in repo.alertas] == ["SENSOR_FALHA"]
    assert all(m["sensor"] == "FALHA" for m in repo.medicoes)
    assert avisos[0][1] == "SENSOR_FALHA"
    # Voltou ao normal e saiu de novo: novo alerta.
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", seq=4))
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", seq=5, distancia={"valor": 1.0, "unidade": "cm"}))
    assert [a["codigo"] for a in repo.alertas] == ["SENSOR_FALHA", "SENSOR_FALHA"]


async def test_alerta_do_medidor_e_gravado_e_avisado(enviar, repo, avisos, exemplo):
    await enviar("rsv-medidor-01", "alerta", exemplo("alerta"))
    assert repo.alertas[0]["codigo"] == "NIVEL_CRITICO" and repo.alertas[0]["nivel"] == 8.2
    assert avisos == [("rsv-medidor-01", "NIVEL_CRITICO", "nível 8 %")]


async def test_medidor_offline_gera_alerta_e_heartbeat_imediato(enviar, ponte, repo, avisos, exemplo):
    await preparar(ponte, enviar, exemplo)
    await enviar("rsv-medidor-01", "telemetria", exemplo("telemetria", nivel=nivel(50)))
    ponte.cliente.publicados.clear()
    await enviar("rsv-medidor-01", "status", exemplo("status"))   # Last Will: online false
    hb = ponte.cliente.de_tipo("heartbeat")
    assert hb and hb[-1]["payload"]["liberaBomba"] is False and hb[-1]["payload"]["motivo"] == "SEM_LEITURA"
    assert repo.alertas[-1]["codigo"] == "OFFLINE"
    assert avisos[-1][1] == "OFFLINE"
    await enviar("rsv-medidor-01", "status", {"v": 1, "deviceId": "rsv-medidor-01", "online": True})
    assert repo.alertas[-1]["codigo"] == "ONLINE"


async def test_primeiro_status_nao_gera_alerta(enviar, repo):
    await enviar("rsv-medidor-01", "status", {"v": 1, "deviceId": "rsv-medidor-01", "online": False})
    assert repo.alertas == [] and len(repo.conexoes) == 1


async def test_modo_seguro_gera_alerta(enviar, repo, avisos, exemplo):
    await enviar("rsv-controlador-01", "estado",
                 exemplo("estado", modo="SEGURO", motivoSeguro="BOOT", bomba="DESLIGADA"))
    assert repo.alertas[-1]["codigo"] == "MODO_SEGURO"
    assert avisos == []   # o SEGURO do boot, visto na subida do backend, não é avisado
    await enviar("rsv-controlador-01", "estado", exemplo("estado", modo="AUTOMATICO", bomba="DESLIGADA"))
    await enviar("rsv-controlador-01", "estado",
                 exemplo("estado", modo="SEGURO", motivoSeguro="SEM_HEARTBEAT", bomba="DESLIGADA"))
    assert avisos[-1][1] == "MODO_SEGURO" and "backend" in avisos[-1][2]
    # SEGURO da atualização pelo Wi-Fi tem texto próprio.
    await enviar("rsv-controlador-01", "estado", exemplo("estado", modo="SEGURO", motivoSeguro="OTA", bomba="DESLIGADA"))
    assert "atualização de firmware" in avisos[-1][2]
    # Mesmo SEGURO repetido não gera outro alerta.
    n = len(repo.alertas)
    await enviar("rsv-controlador-01", "estado", exemplo("estado", modo="SEGURO", motivoSeguro="OTA", bomba="DESLIGADA"))
    assert len(repo.alertas) == n


async def test_falha_ao_notificar_nao_derruba(ponte, enviar, repo, exemplo):
    async def quebra(*_):
        raise RuntimeError("Telegram fora do ar")
    ponte.notificar = quebra
    await enviar("rsv-medidor-01", "alerta", exemplo("alerta"))
    assert len(repo.alertas) == 1


async def test_publicar_config_com_retain(ponte, exemplo):
    await ponte.publicar_config("rsv-controlador-01", exemplo("config_controlador"))
    p = ponte.cliente.publicados[0]
    assert p["topico"] == f"{PREFIXO}/rsv-controlador-01/config" and p["retain"] and p["qos"] == 1
    assert p["payload"]["hb_timeout_s"] == 30
