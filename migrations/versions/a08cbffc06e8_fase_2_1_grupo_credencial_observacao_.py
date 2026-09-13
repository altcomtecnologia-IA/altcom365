"""fase 2.1 - grupo, credencial observacao/atributos, indices, ck_ativo_tipo

Revision ID: a08cbffc06e8
Revises: ec4549622c1d
Create Date: 2026-09-13 16:25:03.668348

PEDIDO_FASE_2_1.md, confirmado contra produção em 13/09/2026 via psql:
alembic_version = ec4549622c1d, ativo/ativo_interface/credencial/
segredo_acesso_log vazias (cliente=27, usuario=4, cliente_contato=26).
Migration puramente aditiva — sem nenhuma linha de dado a migrar, é o
último momento barato para mexer neste schema antes da importação das
252 credenciais.

`alembic heads` foi checado de novo contra o estado real de origin/v2-api
imediatamente antes de escrever esta migration (13/09/2026): head único,
ec4549622c1d, sem novidade desde a Fase 2 — down_revision aponta pra ele
diretamente, sem precisar da correção de linearização que a migration
anterior precisou.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a08cbffc06e8'
down_revision: Union[str, Sequence[str], None] = 'ec4549622c1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # ── Item 1: grupo + cliente.grupo_id ────────────────────────────────
    # eCar / Guia Serviços / Lyon Despachante: três CNPJs, três contratos,
    # uma infraestrutura só. ativo.cliente_id e credencial.cliente_id
    # continuam NOT NULL e SEM grupo_id próprio — a leitura "mostrar
    # também os ativos/credenciais dos outros clientes do grupo" é
    # consulta, fica pra Fase 3 junto com a tela. Aqui só o schema.
    op.create_table(
        'grupo',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('nome', sa.Text(), nullable=False),
        sa.Column('observacao', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nome'),
    )
    op.add_column('cliente', sa.Column('grupo_id', sa.UUID(), nullable=True))
    op.create_index('ix_cliente_grupo', 'cliente', ['grupo_id'], unique=False)
    # Nome explícito (não há naming_convention de FK no metadata — só de
    # índice, ver extensoes.py) para o downgrade poder derrubar a
    # constraint certa sem depender do nome que o Postgres escolheria
    # sozinho.
    op.create_foreign_key('cliente_grupo_id_fkey', 'cliente', 'grupo', ['grupo_id'], ['id'])

    # ── Item 2: credencial.observacao / credencial.atributos ────────────
    # observacao é texto de contexto (planilha real: "Gerenciador no pc
    # do Rodolfo") — NÃO é segredo: fica fora de CAMPOS_REDIGIDOS
    # (clientes/auditoria.py) e fora do retorno de revelar_credencial.
    # atributos é a mesma válvula de cauda longa que ativo.atributos já
    # tem — primeiro uso real é SSID de rede sem fio. Ambos aparecem na
    # listagem (GET /clientes/<cliente_id>/credenciais).
    op.add_column('credencial', sa.Column('observacao', sa.Text(), nullable=True))
    op.add_column(
        'credencial',
        sa.Column(
            'atributos', postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
    )

    # ── Item 3: ck_ativo_tipo estendido ──────────────────────────────────
    # Lista antiga (8 valores) não cobre a planilha real: 27 modems/ONTs,
    # 5 NAS/storage, e impressora é o tipo mais frequente de todos. Não
    # entra nenhuma linha de impressora agora (decisão fechada do
    # Altair) — o valor entra no CHECK porque alargar numa tabela vazia
    # é grátis e depois não é. _TIPOS_ATIVO_VALIDOS em clientes/routes.py
    # foi atualizado com o mesmo conjunto, na mesma ordem de decisão —
    # os dois têm que andar juntos.
    op.drop_constraint('ck_ativo_tipo', 'ativo', type_='check')
    op.create_check_constraint(
        'ck_ativo_tipo', 'ativo',
        "tipo IN ('roteador', 'firewall', 'switch', 'ap', 'link', 'servidor', "
        "'nvr', 'nobreak', 'modem', 'storage', 'camera', 'impressora')",
    )

    # ── Item 4: índices que faltavam nas tabelas da Fase 2 ──────────────
    # FK em Postgres não cria índice sozinha. Os quatro abaixo são btree
    # simples com forma declarativa — já DECLARADOS em __table_args__ dos
    # models correspondentes (mesmo padrão da correção de Quarentena no
    # merge anterior), então este create_index só materializa o que o
    # autogenerate já espera encontrar; não sobra falso positivo para
    # eles.
    op.create_index('ix_ativo_cliente', 'ativo', ['cliente_id'], unique=False)
    op.create_index('ix_ativo_interface_ativo', 'ativo_interface', ['ativo_id'], unique=False)
    op.create_index('ix_credencial_cliente', 'credencial', ['cliente_id'], unique=False)
    op.create_index(
        'ix_segredo_acesso_log_credencial', 'segredo_acesso_log', ['credencial_id'], unique=False,
    )
    # O quinto índice (usuario_id, em DESC) não tem forma declarativa
    # portável nesta versão do SQLAlchemy por causa do DESC — mesma
    # classe dos três falsos positivos já documentados na migration da
    # Fase 2 (ec4549622c1d): fica só aqui, via SQL puro, e o nome dele
    # foi acrescentado ao comentário daquela migration para o próximo
    # autogenerate não assustar ninguém quando propuser apagá-lo.
    op.execute(
        "CREATE INDEX ix_segredo_acesso_log_usuario_em "
        "ON segredo_acesso_log (usuario_id, em DESC)"
    )

    # ── NÃO tocado por esta migration, de propósito ─────────────────────
    # As três linhas de drop_index que o autogenerate propôs aqui de novo
    # (ix_audit_log_usuario_em, ux_faixa_rollout_maquinas_ate,
    # ux_plano_vigente_por_cliente) foram REMOVIDAS à mão — mesmo motivo
    # documentado na migration Fase 2 (ec4549622c1d): são objetos raw SQL
    # sem forma declarativa nesta versão, o autogenerate não os vê no
    # metadata e sempre vai propor apagá-los. Não foram removidos do
    # código; ignorar essas linhas é o padrão, não um esquecimento.


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_segredo_acesso_log_usuario_em")
    op.drop_index('ix_segredo_acesso_log_credencial', table_name='segredo_acesso_log')
    op.drop_index('ix_credencial_cliente', table_name='credencial')
    op.drop_index('ix_ativo_interface_ativo', table_name='ativo_interface')
    op.drop_index('ix_ativo_cliente', table_name='ativo')

    op.drop_constraint('ck_ativo_tipo', 'ativo', type_='check')
    op.create_check_constraint(
        'ck_ativo_tipo', 'ativo',
        "tipo IN ('roteador', 'firewall', 'switch', 'ap', 'link', 'servidor', "
        "'nvr', 'nobreak')",
    )

    op.drop_column('credencial', 'atributos')
    op.drop_column('credencial', 'observacao')

    op.drop_constraint('cliente_grupo_id_fkey', 'cliente', type_='foreignkey')
    op.drop_index('ix_cliente_grupo', table_name='cliente')
    op.drop_column('cliente', 'grupo_id')
    op.drop_table('grupo')
