"""Ambiente do Alembic: usa a mesma URL do backend (DATABASE_URL ou POSTGRES_*)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.banco import Base
from app.config import carregar

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

URL = carregar().database_url


def executar_offline() -> None:
    context.configure(url=URL, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def executar_online() -> None:
    # O driver psycopg 3 atende tanto o modo assíncrono do backend quanto
    # este modo síncrono do Alembic, com a mesma URL.
    engine = create_engine(URL)
    with engine.connect() as conexao:
        context.configure(connection=conexao, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    executar_offline()
else:
    executar_online()
