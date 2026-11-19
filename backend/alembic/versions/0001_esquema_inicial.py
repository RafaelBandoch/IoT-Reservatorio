"""Esquema inicial do backend: medições, alertas, comandos, acks, estado,
conexões, configurações, payloads inválidos e usuários.

Revisão: 0001
Anterior: nenhuma
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", sa.BigInteger, primary_key=True)


def _quando(nome, nulo=True):
    return sa.Column(nome, sa.DateTime(timezone=True), nullable=nulo)


def upgrade() -> None:
    op.create_table(
        "medicoes", _id(),
        _quando("ts", False), _quando("recebido_em", False),
        sa.Column("device_id", sa.String(40), nullable=False),
        sa.Column("seq", sa.BigInteger),
        sa.Column("nivel", sa.Float), sa.Column("distancia", sa.Float), sa.Column("temperatura", sa.Float),
        sa.Column("sensor", sa.String(10), nullable=False),
        sa.Column("relogio_sincronizado", sa.Boolean, nullable=False),
    )
    op.create_index("ix_medicoes_ts", "medicoes", ["ts"])

    op.create_table(
        "alertas", _id(),
        _quando("ts", False), _quando("recebido_em", False),
        sa.Column("device_id", sa.String(40), nullable=False),
        sa.Column("codigo", sa.String(40), nullable=False),
        sa.Column("nivel", sa.Float),
        sa.Column("detalhe", sa.Text),
    )
    op.create_index("ix_alertas_ts", "alertas", ["ts"])

    op.create_table(
        "comandos", _id(),
        sa.Column("command_id", sa.String(64), nullable=False, unique=True),
        sa.Column("acao", sa.String(20), nullable=False),
        sa.Column("origem", sa.String(10), nullable=False),
        sa.Column("usuario", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(40)),
        _quando("enviado_em", False), _quando("recebido_em"), _quando("executado_em"), _quando("confirmado_em"),
        sa.Column("latencia_ms", sa.Integer),
    )
    op.create_index("ix_comandos_status", "comandos", ["status"])
    op.create_index("ix_comandos_enviado_em", "comandos", ["enviado_em"])

    op.create_table(
        "acks", _id(),
        sa.Column("command_id", sa.String(64), nullable=False),
        sa.Column("etapa", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(40)),
        _quando("ts", False), _quando("recebido_em", False),
    )
    op.create_index("ix_acks_command_id", "acks", ["command_id"])

    op.create_table(
        "estado", _id(),
        _quando("ts", False), _quando("recebido_em", False),
        sa.Column("device_id", sa.String(40), nullable=False),
        sa.Column("modo", sa.String(20), nullable=False),
        sa.Column("motivo_seguro", sa.String(40)),
        sa.Column("bomba", sa.String(10), nullable=False),
        sa.Column("ligada_ha_s", sa.Float),
        sa.Column("ultimo_command_id", sa.String(64)),
        sa.Column("fw", sa.String(20)),
    )
    op.create_index("ix_estado_recebido_em", "estado", ["recebido_em"])

    op.create_table(
        "conexoes", _id(),
        _quando("recebido_em", False),
        sa.Column("device_id", sa.String(40), nullable=False),
        sa.Column("online", sa.Boolean, nullable=False),
        sa.Column("fw", sa.String(20)),
        sa.Column("ip", sa.String(45)),
    )
    op.create_index("ix_conexoes_recebido_em", "conexoes", ["recebido_em"])
    op.create_index("ix_conexoes_device_id", "conexoes", ["device_id"])

    op.create_table(
        "configuracoes", _id(),
        sa.Column("device_id", sa.String(40), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        _quando("enviado_em", False),
        sa.Column("usuario", sa.String(60), nullable=False),
    )
    op.create_index("ix_configuracoes_device_id", "configuracoes", ["device_id"])

    op.create_table(
        "payloads_invalidos", _id(),
        _quando("recebido_em", False),
        sa.Column("topico", sa.String(200), nullable=False),
        sa.Column("codigo", sa.String(40), nullable=False),
        sa.Column("erro", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
    )
    op.create_index("ix_payloads_invalidos_recebido_em", "payloads_invalidos", ["recebido_em"])

    op.create_table(
        "usuarios", _id(),
        sa.Column("nome", sa.String(60), nullable=False, unique=True),
        sa.Column("senha_hash", sa.String(100), nullable=False),
        sa.Column("perfil", sa.String(20), nullable=False),
        _quando("criado_em", False),
    )


def downgrade() -> None:
    for tabela in ("usuarios", "payloads_invalidos", "configuracoes", "conexoes", "estado",
                   "acks", "comandos", "alertas", "medicoes"):
        op.drop_table(tabela)
