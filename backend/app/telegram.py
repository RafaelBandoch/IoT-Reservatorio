"""
Alertas pelo Telegram (R15).

A Ponte chama notificar(device_id, codigo, texto) a cada alerta: os do
medidor (NIVEL_CRITICO, NIVEL_TRANSBORDO, SENSOR_FALHA, NORMALIZADO) e os
gerados pelo backend (OFFLINE, ONLINE, MODO_SEGURO, SENSOR_FALHA por leitura
fora da faixa). A chamada só põe a mensagem numa fila e volta na hora: o
envio pela Bot API acontece em segundo plano, então um Telegram lento ou
fora do ar nunca atrasa a decisão sobre a bomba.

O mesmo alerta do mesmo dispositivo não é repetido dentro de
REPETICAO_S (ex.: SENSOR_FALHA vindo do medidor e do backend ao mesmo tempo).

Configuração no .env: TELEGRAM_TOKEN (do @BotFather) e TELEGRAM_CHAT_ID.
Sem os dois, os alertas continuam só no banco e no painel.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import httpx

log = logging.getLogger("aquanivel.telegram")

REPETICAO_S = 600
TIMEOUT_S = 10
FUSO = timezone(timedelta(hours=-3))   # horário de Brasília, só para o texto

NOMES = {"rsv-medidor-01": "medidor", "rsv-controlador-01": "controlador"}

TITULOS = {
    "NIVEL_CRITICO": "ALERTA: nível crítico na caixa d'água",
    "NIVEL_TRANSBORDO": "ALERTA: caixa d'água perto de transbordar",
    "SENSOR_FALHA": "ALERTA: falha no sensor de nível",
    "NORMALIZADO": "Nível da caixa d'água normalizado",
    "MODO_SEGURO": "ALERTA: bomba bloqueada (controlador em modo seguro)",
    "OFFLINE": "ALERTA: dispositivo sem conexão",
    "ONLINE": "Dispositivo conectado de novo",
    "BOMBA_LIGADA": "Bomba ligada automaticamente (nível baixo)",
    "BOMBA_DESLIGADA": "Bomba desligada automaticamente (caixa cheia)",
}


def formatar(device_id: str, codigo: str, texto: str, quando: Optional[datetime] = None) -> str:
    quando = (quando or datetime.now(timezone.utc)).astimezone(FUSO)
    linhas = [f"AquaNível: {TITULOS.get(codigo, codigo)}"]
    # Em OFFLINE e ONLINE o texto só repete o nome do dispositivo, que já vai abaixo.
    if texto and codigo not in ("OFFLINE", "ONLINE"):
        linhas.append(texto[0].upper() + texto[1:])
    linhas.append(f"Dispositivo: {NOMES.get(device_id, device_id)} ({device_id})")
    linhas.append(f"Horário: {quando:%d/%m %H:%M:%S}")
    return "\n".join(linhas)


class Telegram:
    def __init__(self, token: str, chat_id: str,
                 enviar: Optional[Callable[[str], Awaitable[None]]] = None,
                 relogio: Callable[[], float] = time.monotonic):
        self.token, self.chat_id = token, chat_id
        self._enviar = enviar or self._enviar_bot_api
        self._relogio = relogio
        self._ultimo: dict[str, tuple[str, float]] = {}   # device -> (chave, instante)
        self._fila: Optional[asyncio.Queue] = None
        self._tarefa: Optional[asyncio.Task] = None

    def _repetido(self, device_id: str, codigo: str, texto: str) -> bool:
        # MODO_SEGURO distingue o motivo (BOOT, SEM_HEARTBEAT, OTA...).
        chave = f"{codigo}|{texto}" if codigo == "MODO_SEGURO" else codigo
        agora = self._relogio()
        anterior = self._ultimo.get(device_id)
        self._ultimo[device_id] = (chave, agora)
        return anterior is not None and anterior[0] == chave and agora - anterior[1] < REPETICAO_S

    async def __call__(self, device_id: str, codigo: str, texto: str) -> None:
        if self._repetido(device_id, codigo, texto):
            log.info("telegram: %s de %s repetido, não reenviado", codigo, device_id)
            return
        if self._fila is None:
            self._fila = asyncio.Queue(maxsize=50)
            self._tarefa = asyncio.create_task(self._trabalhar())
        try:
            self._fila.put_nowait(formatar(device_id, codigo, texto))
        except asyncio.QueueFull:
            log.warning("telegram: fila cheia, alerta %s descartado", codigo)

    async def _trabalhar(self) -> None:
        while True:
            mensagem = await self._fila.get()
            try:
                await self._enviar(mensagem)
                log.info("telegram: alerta enviado (%s)", mensagem.splitlines()[0])
            except Exception as erro:
                log.warning("telegram: falha ao enviar: %s", erro)

    async def _enviar_bot_api(self, mensagem: str) -> None:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as cliente:
            r = await cliente.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                                   data={"chat_id": self.chat_id, "text": mensagem})
            if r.status_code != 200:
                # A URL tem o token: nunca registrar a URL, só o status e a descrição.
                raise RuntimeError(f"HTTP {r.status_code}: {r.json().get('description', '')}")

    async def fechar(self) -> None:
        if self._tarefa:
            self._tarefa.cancel()


def criar_do_ambiente() -> Optional[Telegram]:
    token, chat = os.getenv("TELEGRAM_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        log.info("telegram desligado (TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID vazio)")
        return None
    log.info("telegram ligado: alertas vão para o chat %s", chat)
    return Telegram(token, chat)
