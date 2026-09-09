"""quarentena: historico de dispositivos em acompanhamento comercial

Revision ID: f3a8b9c21d54
Revises: 91b122f37431
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 'f3a8b9c21d54'
down_revision = '91b122f37431'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'quarentena',
        sa.Column('id', UUID(as_uuid=True),
                  server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('dispositivo',    sa.Text(), nullable=False),
        sa.Column('cliente',        sa.Text(), nullable=False),
        sa.Column('motivo',         sa.Text()),
        sa.Column('acao_tomada',    sa.Text()),
        sa.Column('adicionado_por', sa.Text()),
        sa.Column('adicionado_em',  sa.Date(), nullable=False,
                  server_default=sa.text('CURRENT_DATE')),
        sa.Column('expira_em',      sa.Date(), nullable=False),
        sa.Column('status',         sa.Text(), nullable=False,
                  server_default='ativa'),
        sa.CheckConstraint(
            "status IN ('ativa', 'expirada', 'liberada')",
            name='ck_quarentena_status',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_quarentena_disp_cli', 'quarentena',
                    ['dispositivo', 'cliente'])
    op.create_index('ix_quarentena_status',   'quarentena', ['status'])


def downgrade() -> None:
    op.drop_index('ix_quarentena_status',   table_name='quarentena')
    op.drop_index('ix_quarentena_disp_cli', table_name='quarentena')
    op.drop_table('quarentena')
