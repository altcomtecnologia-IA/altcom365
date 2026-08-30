"""
extensoes.py
Instância única do SQLAlchemy compartilhada pelos módulos novos do portal
(Clientes e Processos, e futuramente Maturidade). NÃO é usada pelo Laudos —
models.py mantém sua própria instância, desconectada do app (ver nota em
clientes/models.py sobre por quê).
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def normalizar_database_url(url: str) -> str:
    """
    Render entrega 'postgres://'; SQLAlchemy 2.x exige 'postgresql://', e
    sem driver explícito tenta psycopg2 (não instalado — o driver pinado
    no requirements.txt é psycopg 3).

    Usada por app.py, migrations/env.py e pelos testes (tests/conftest.py)
    — um lugar só, pra normalização nunca divergir entre quem sobe o app
    de verdade e quem roda migration.
    """
    if url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql+psycopg://', 1)
    if url.startswith('postgresql://'):
        return url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return url
