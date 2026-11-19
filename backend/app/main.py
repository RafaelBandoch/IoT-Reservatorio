"""
Backend do AquaNível: FastAPI + aiomqtt + PostgreSQL.

    uvicorn app.main:app --host 0.0.0.0 --port 8000

Na subida: cria os usuários do .env e abre a ponte MQTT, que roda em
segundo plano enquanto a API atende o painel.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config
from .api import rotas
from .autenticacao import criar_usuarios_do_env, rotas_publicas
from .banco import criar_engine, criar_sessoes
from .logica import Regras
from .ponte import Ponte
from .repositorio import Repositorio, agora_utc
from .telegram import criar_do_ambiente as criar_telegram

logging.basicConfig(level=os.getenv("LOG_NIVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aquanivel")

PAINEL = Path(os.getenv("PAINEL_DIR", Path(__file__).resolve().parent.parent / "painel"))


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    cfg = config.carregar()
    engine = criar_engine(cfg.database_url)
    repo = Repositorio(criar_sessoes(engine))
    await criar_usuarios_do_env(repo)
    telegram = criar_telegram()   # None sem TELEGRAM_TOKEN e TELEGRAM_CHAT_ID
    ponte = Ponte(cfg, repo, Regras(), notificar=telegram)
    app.state.repo, app.state.ponte = repo, ponte
    tarefa = asyncio.create_task(ponte.executar())
    log.info("backend no ar; broker %s:%d, prefixo %s", cfg.mqtt_host, cfg.mqtt_porta, cfg.prefixo)
    try:
        yield
    finally:
        tarefa.cancel()
        if telegram:
            await telegram.fechar()
        await engine.dispose()


app = FastAPI(title="AquaNível", version="1.0.0", lifespan=ciclo_de_vida)
app.include_router(rotas_publicas)
app.include_router(rotas)


@app.get("/api/saude", tags=["publico"])
async def saude():
    ponte = getattr(app.state, "ponte", None)
    return {"mqtt_conectado": bool(ponte and ponte.conectado), "agora": agora_utc()}


# O painel (fase 2) é servido pelo próprio backend, na raiz.
if PAINEL.is_dir():
    app.mount("/", StaticFiles(directory=PAINEL, html=True), name="painel")
