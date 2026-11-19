"""Rotas REST do backend. O painel usa só isto; nunca fala com o broker."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from . import contrato as c
from .autenticacao import exigir_admin, usuario_atual
from .logica import LEITURA_VELHA_S
from .ponte import Ponte, SemConexao
from .repositorio import Repositorio, agora_utc

ESPERA_ACK_S = 4

# Toda rota daqui exige login; as que mudam algo exigem o perfil admin.
rotas = APIRouter(prefix="/api", dependencies=[Depends(usuario_atual)])


def _ponte(req: Request) -> Ponte:
    return req.app.state.ponte


def _repo(req: Request) -> Repositorio:
    return req.app.state.repo


def _periodo(inicio: Optional[datetime], fim: Optional[datetime], padrao: timedelta) -> tuple[datetime, datetime]:
    fim = _utc(fim) if fim else agora_utc()
    inicio = _utc(inicio) if inicio else fim - padrao
    if inicio >= fim:
        raise HTTPException(422, "inicio deve ser anterior a fim")
    return inicio, fim


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _idade(quando: Optional[datetime]) -> Optional[float]:
    return None if quando is None else round((agora_utc() - quando).total_seconds(), 1)


@rotas.get("/nivel")
async def nivel(req: Request):
    """Última leitura do medidor e a liberação que o backend está mandando."""
    p = _ponte(req)
    t = p.vivo["telemetria"] or await _repo(req).ultima_medicao()
    if t is None:
        raise HTTPException(404, "nenhuma leitura recebida ainda")
    idade = _idade(t["recebido_em"])
    return {**t, "idade_s": idade, "leitura_velha": idade is None or idade > LEITURA_VELHA_S,
            "heartbeat": p.vivo["heartbeat"]}


@rotas.get("/historico")
async def historico(req: Request, inicio: Optional[datetime] = None, fim: Optional[datetime] = None):
    """Médias de nível, temperatura e distância no período (padrão: última hora)."""
    i, f = _periodo(inicio, fim, timedelta(hours=1))
    return {"inicio": i, "fim": f, **await _repo(req).historico(i, f)}


@rotas.get("/analise")
async def analise(req: Request, inicio: Optional[datetime] = None, fim: Optional[datetime] = None):
    """Nível médio, mínimo e máximo, tempo de bomba ligada e acionamentos (padrão: 24 h)."""
    i, f = _periodo(inicio, fim, timedelta(hours=24))
    return await _repo(req).analise(i, f)


@rotas.get("/alertas")
async def alertas(req: Request, inicio: Optional[datetime] = None, fim: Optional[datetime] = None,
                  limite: int = Query(50, ge=1, le=500)):
    i, f = _periodo(inicio, fim, timedelta(days=7))
    return await _repo(req).alertas(i, f, limite)


@rotas.get("/dispositivos")
async def dispositivos(req: Request):
    """Online/offline dos dois ESP32 (Last Will) e o estado do controlador."""
    p = _ponte(req)
    resposta = {}
    for chave, device_id in (("medidor", c.MEDIDOR), ("controlador", c.CONTROLADOR)):
        vivo = dict(p.vivo[chave])
        if vivo["online"] is None:   # backend recém-iniciado: usa o banco
            ultima = await _repo(req).ultima_conexao(device_id)
            if ultima:
                vivo.update(online=ultima["online"], fw=ultima["fw"], ip=ultima["ip"], mudou_em=ultima["recebido_em"])
        resposta[chave] = {"deviceId": device_id, **vivo, "sem_dados_s": _idade(vivo["visto_em"])}
    resposta["controlador"]["estado"] = p.vivo["estado"] or await _repo(req).ultimo_estado()
    resposta["backend"] = {"mqtt_conectado": p.conectado, "heartbeat": p.vivo["heartbeat"]}
    return resposta


# ---------- comandos ----------
class ComandoEntrada(BaseModel):
    acao: c.Acao


@rotas.get("/comandos")
async def comandos(req: Request, limite: int = Query(20, ge=1, le=200)):
    return await _repo(req).comandos(limite)


@rotas.get("/comandos/{command_id}")
async def comando(req: Request, command_id: str):
    cmd = await _repo(req).comando(command_id)
    if cmd is None:
        raise HTTPException(404, "comando não encontrado")
    return cmd


@rotas.post("/comandos")
async def enviar_comando(req: Request, entrada: ComandoEntrada, u: dict = Depends(exigir_admin)):
    """Comando manual (origem=manual). Responde com o ciclo do comando já com o ack, se chegou."""
    return await enviar_comando_manual(req, entrada.acao, u["usuario"])


async def enviar_comando_manual(req: Request, acao: str, usuario: str) -> dict:
    """Envia o comando e espera até ESPERA_ACK_S pelo EXECUTADO ou REJEITADO."""
    p = _ponte(req)
    if p.vivo["controlador"]["online"] is False:
        raise HTTPException(409, "controlador offline: o comando não foi enviado")
    try:
        cid = await p.enviar_comando(acao, "manual", usuario)
    except SemConexao as erro:
        raise HTTPException(503, str(erro))
    await p.esperar_resposta(cid, ESPERA_ACK_S)
    return await _repo(req).comando(cid)


# ---------- configuração ----------
def _device(device_id: str) -> str:
    if device_id not in c.CONFIG_MODELO:
        raise HTTPException(404, f"dispositivo desconhecido: {device_id}")
    return device_id


async def config_vigente(req: Request, device_id: str) -> dict:
    """A do retain no broker; senão a última que o backend mandou; senão o padrão."""
    vivo = _ponte(req).vivo["config"].get(device_id)
    if vivo:
        return vivo
    ultima = await _repo(req).ultima_config(device_id)
    return ultima["payload"] if ultima else c.CONFIG_PADRAO[device_id].model_dump()


@rotas.get("/config/{device_id}")
async def ler_config(req: Request, device_id: str):
    return await config_vigente(req, _device(device_id))


@rotas.put("/config/{device_id}")
async def alterar_config_rota(req: Request, device_id: str, mudancas: dict = Body(...),
                              u: dict = Depends(exigir_admin)):
    """Altera parâmetros (só os campos enviados). Publica no tópico config com retain."""
    return await alterar_config(req, device_id, mudancas, u["usuario"])


async def alterar_config(req: Request, device_id: str, mudancas: dict, usuario: str) -> dict:
    """Junta as mudanças à configuração vigente, valida as faixas e publica com retain."""
    _device(device_id)
    modelo = c.CONFIG_MODELO[device_id]
    base = {**c.CONFIG_PADRAO[device_id].model_dump(), **await config_vigente(req, device_id)}
    desconhecidos = set(mudancas) - set(modelo.model_fields) - {"v"}
    if desconhecidos:
        raise HTTPException(422, f"campos desconhecidos: {sorted(desconhecidos)}")
    try:
        nova = modelo.model_validate({**base, **mudancas, "v": 1}).model_dump()
    except ValidationError as erro:
        raise HTTPException(422, {"codigo": "VALOR_FORA_FAIXA", "erros": [_erro_pt(e) for e in erro.errors()]})
    try:
        await _ponte(req).publicar_config(device_id, nova)
    except SemConexao as e:
        raise HTTPException(503, str(e))
    await _repo(req).gravar_config(device_id, nova, usuario)
    return nova


def _erro_pt(e: dict) -> dict:
    """Erro de validação do Pydantic em português, para o painel mostrar."""
    campo = ".".join(str(x) for x in e["loc"]) or "config"
    ctx = e.get("ctx") or {}
    tipo = e["type"]
    if tipo in ("greater_than_equal", "greater_than"):
        texto = f"mínimo {ctx.get('ge', ctx.get('gt'))}"
    elif tipo in ("less_than_equal", "less_than"):
        texto = f"máximo {ctx.get('le', ctx.get('lt'))}"
    elif tipo in ("int_parsing", "float_parsing", "int_from_float", "int_type", "float_type"):
        texto = "precisa ser um número" + (" inteiro" if tipo.startswith("int") else "")
    elif tipo == "value_error":
        texto = str(ctx.get("error", e["msg"]))
    else:
        texto = e["msg"]
    return {"campo": campo, "mensagem": texto}


@rotas.get("/invalidos")
async def invalidos(req: Request, limite: int = Query(50, ge=1, le=500)):
    """Mensagens descartadas por não seguirem o contrato."""
    return await _repo(req).invalidos(limite)
