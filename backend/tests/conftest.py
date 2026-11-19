"""
Fixtures comuns: exemplos de payload do CONTRATOS.md (v1) e uma Ponte
ligada a um repositório e a um cliente MQTT falsos, em memória. Nenhum
teste precisa de broker nem de banco.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import contrato as c  # noqa: E402
from app.config import Config  # noqa: E402
from app.logica import Regras  # noqa: E402
from app.ponte import Ponte  # noqa: E402

PREFIXO = "teste-g4/reservatorio"

# Exemplos copiados do CONTRATOS.md
EXEMPLOS = {
    "telemetria": {"v": 1, "deviceId": "rsv-medidor-01", "ts": "2026-11-10T22:15:30Z", "seq": 1042,
                   "nivel": {"valor": 63.4, "unidade": "%"},
                   "distancia": {"valor": 10.7, "unidade": "cm"},
                   "temperatura": {"valor": 24.8, "unidade": "°C"},
                   "sensor": "OK"},
    "alerta": {"v": 1, "deviceId": "rsv-medidor-01", "ts": "2026-11-10T22:40:12Z",
               "codigo": "NIVEL_CRITICO", "nivel": {"valor": 8.2, "unidade": "%"}},
    "config_medidor": {"v": 1, "d_vazio_cm": 23.5, "d_cheio_cm": 4.0, "alerta_baixo_pct": 10,
                       "alerta_alto_pct": 95, "periodo_s": 10},
    "cmd": {"v": 1, "commandId": "c-20261110-0007", "origem": "manual",
            "acao": "LIGAR", "usuario": "admin", "ts": "2026-11-10T22:16:02Z"},
    "ack": {"v": 1, "deviceId": "rsv-controlador-01", "commandId": "c-20261110-0007",
            "etapa": "EXECUTADO", "motivo": None, "ts": "2026-11-10T22:16:03Z"},
    "estado": {"v": 1, "deviceId": "rsv-controlador-01", "ts": "2026-11-10T22:16:03Z",
               "modo": "MANUAL", "bomba": "LIGADA", "ligadaHa": {"valor": 1, "unidade": "s"},
               "ultimoCommandId": "c-20261110-0007", "fw": "1.0.0"},
    "config_controlador": {"v": 1, "t_manual_s": 300, "t_max_ligada_s": 600, "hb_timeout_s": 30},
    "heartbeat": {"v": 1, "ts": "2026-11-10T22:16:10Z", "seq": 381, "liberaBomba": True, "motivo": None},
    "status": {"v": 1, "deviceId": "rsv-medidor-01", "online": False},
}


@pytest.fixture
def exemplo():
    """exemplo("telemetria", nivel={"valor": 5, "unidade": "%"}) devolve uma cópia alterada."""
    def _exemplo(nome: str, **mudancas):
        dados = copy.deepcopy(EXEMPLOS[nome])
        dados.update(mudancas)
        return dados
    return _exemplo


class RepositorioFalso:
    """Guarda em listas tudo o que a Ponte mandaria ao banco."""

    def __init__(self):
        self.medicoes, self.alertas, self.estados, self.conexoes = [], [], [], []
        self.invalidos, self.comandos, self.acks, self.confirmados = [], [], [], []
        self.falhar_medicao = False

    async def gravar_medicao(self, **campos):
        if self.falhar_medicao:
            raise RuntimeError("banco fora do ar")
        self.medicoes.append(campos)

    async def gravar_alerta(self, **campos):
        self.alertas.append(campos)

    async def gravar_estado(self, **campos):
        self.estados.append(campos)

    async def gravar_conexao(self, **campos):
        self.conexoes.append(campos)

    async def gravar_invalido(self, topico, codigo, erro, payload):
        self.invalidos.append({"topico": topico, "codigo": codigo, "erro": erro, "payload": payload})

    async def gravar_comando_enviado(self, command_id, acao, origem, usuario, ts):
        self.comandos.append({"command_id": command_id, "acao": acao, "origem": origem, "usuario": usuario})

    async def gravar_ack(self, command_id, etapa, motivo, ts, recebido_em):
        self.acks.append({"command_id": command_id, "etapa": etapa, "motivo": motivo})

    async def confirmar_comando(self, command_id, quando):
        if any(cmd["command_id"] == command_id for cmd in self.comandos):
            self.confirmados.append(command_id)
            return True
        return False

    async def expirar_comandos(self, segundos):
        return 0


class ClienteFalso:
    """Faz o papel do aiomqtt.Client: só registra o que seria publicado."""

    def __init__(self, repo: RepositorioFalso):
        self.publicados = []
        self.repo = repo

    async def publish(self, topico, payload, qos=0, retain=False):
        # Confere a regra "grava ENVIADO antes de publicar o cmd".
        if topico.endswith("/cmd"):
            cid = json.loads(payload)["commandId"]
            assert any(cmd["command_id"] == cid for cmd in self.repo.comandos), "cmd publicado antes de gravado"
        self.publicados.append({"topico": topico, "payload": json.loads(payload), "qos": qos, "retain": retain})

    def de_tipo(self, tipo: str) -> list:
        return [p for p in self.publicados if p["topico"].endswith("/" + tipo)]


@pytest.fixture
def repo():
    return RepositorioFalso()


@pytest.fixture
def avisos():
    return []


@pytest.fixture
def ponte(repo, avisos):
    cfg = Config(database_url="", mqtt_host="falso", mqtt_porta=1883, mqtt_usuario=None,
                 mqtt_senha=None, mqtt_tls=False, mqtt_ca=None, prefixo=PREFIXO)

    async def notificar(device_id, codigo, texto):
        avisos.append((device_id, codigo, texto))

    p = Ponte(cfg, repo, Regras(), notificar=notificar)
    p.cliente = ClienteFalso(repo)
    return p


@pytest.fixture
def enviar(ponte):
    """await enviar("rsv-medidor-01", "telemetria", dados) entrega uma mensagem à Ponte."""
    async def _enviar(dispositivo: str, tipo: str, dados):
        bruto = dados if isinstance(dados, bytes) else json.dumps(dados).encode()
        await ponte.tratar(f"{PREFIXO}/{dispositivo}/{tipo}", bruto)
    return _enviar


@pytest.fixture
def medidor():
    return c.MEDIDOR


@pytest.fixture
def controlador():
    return c.CONTROLADOR
