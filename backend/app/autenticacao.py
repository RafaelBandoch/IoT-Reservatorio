"""
Login com JWT e perfis (CP12, R9 e R16):
  admin        vê tudo, manda comandos e altera parâmetros
  visualizador só vê
Os usuários vêm do .env (ADMIN_USUARIO/ADMIN_SENHA e, opcional,
VISUALIZADOR_USUARIO/VISUALIZADOR_SENHA) e são gravados com a senha em hash
bcrypt na tabela usuarios a cada subida.
"""

import logging
import os
import secrets
from datetime import timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .repositorio import Repositorio, agora_utc

log = logging.getLogger("aquanivel.auth")

ALGORITMO = "HS256"
VALIDADE = timedelta(hours=12)

_segredo = os.getenv("JWT_SEGREDO") or ""
if len(_segredo) < 32:
    log.warning("JWT_SEGREDO ausente ou curto: usando um aleatório (os logins caem a cada reinício)")
    _segredo = secrets.token_urlsafe(48)

_bearer = HTTPBearer(auto_error=False)
rotas_publicas = APIRouter(prefix="/api")


def gerar_hash(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def senha_confere(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode(), senha_hash.encode())
    except ValueError:
        return False


def emitir_token(usuario: str, perfil: str) -> str:
    agora = agora_utc()
    return jwt.encode({"sub": usuario, "perfil": perfil, "iat": agora, "exp": agora + VALIDADE},
                      _segredo, algorithm=ALGORITMO)


async def criar_usuarios_do_env(repo: Repositorio) -> None:
    for prefixo, perfil in (("ADMIN", "admin"), ("VISUALIZADOR", "visualizador")):
        nome, senha = os.getenv(f"{prefixo}_USUARIO"), os.getenv(f"{prefixo}_SENHA")
        if nome and senha:
            await repo.salvar_usuario(nome, gerar_hash(senha), perfil)
            log.info("usuário %s (%s) pronto", nome, perfil)
        elif perfil == "admin":
            log.warning("ADMIN_USUARIO/ADMIN_SENHA não definidos: ninguém consegue mandar comandos")


class Login(BaseModel):
    usuario: str
    senha: str


@rotas_publicas.post("/login")
async def login(req: Request, dados: Login):
    u = await req.app.state.repo.usuario(dados.usuario)
    if u is None or not senha_confere(dados.senha, u.senha_hash):
        raise HTTPException(401, "usuário ou senha incorretos")
    return {"token": emitir_token(u.nome, u.perfil), "usuario": u.nome, "perfil": u.perfil,
            "expira_em_s": int(VALIDADE.total_seconds())}


async def usuario_atual(cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> dict:
    if cred is None:
        raise HTTPException(401, "faça login", headers={"WWW-Authenticate": "Bearer"})
    try:
        dados = jwt.decode(cred.credentials, _segredo, algorithms=[ALGORITMO])
    except jwt.PyJWTError:
        raise HTTPException(401, "sessão inválida ou expirada", headers={"WWW-Authenticate": "Bearer"})
    return {"usuario": dados["sub"], "perfil": dados["perfil"]}


async def exigir_admin(u: dict = Depends(usuario_atual)) -> dict:
    if u["perfil"] != "admin":
        raise HTTPException(403, "só o perfil admin pode fazer isto")
    return u


@rotas_publicas.get("/eu")
async def eu(u: dict = Depends(usuario_atual)):
    return u
