import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Repo root no path — env.py roda de dentro de migrations/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ── Metadata explícito, importado aqui e só aqui ────────────────────────────
# Autogenerate compara o banco contra target_metadata. Se um modelo não for
# importado, o Alembic não sabe que a tabela dele deve existir e, na próxima
# vez que alguém rodar `alembic revision --autogenerate`, vai propor DROP
# TABLE para ela achando que foi removida do código.
#
# Import deliberadamente NÃO inclui models.py (Laudos): aquele arquivo não é
# usado pelo app.py hoje (nenhuma rota importa `models`; as duas que
# referenciam ClientesMap/DispositivosMap quebrariam com NameError se fossem
# chamadas) e suas tabelas nunca foram criadas em banco nenhum. Elas ficam
# fora do metadata deste módulo até uma decisão explícita de trazê-las.
import extensoes          # noqa: E402  (db = SQLAlchemy())
import portal.models       # noqa: E402,F401  (usuario, audit_log, sessao_log)
import clientes.models     # noqa: E402,F401  (cliente, cliente_contato, plano, faixa_rollout)

target_metadata = extensoes.db.metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DATABASE_URL do Render vem como 'postgres://'; SQLAlchemy 2.x exige
# 'postgresql://', e sem driver explícito tenta psycopg2 (não instalado —
# o driver pinado no requirements.txt é psycopg 3). Normalizado aqui, não
# em app.py (passo 3).
_db_url = os.environ.get('DATABASE_URL', '')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql+psycopg://', 1)
elif _db_url.startswith('postgresql://'):
    _db_url = _db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
if _db_url:
    config.set_main_option('sqlalchemy.url', _db_url)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
