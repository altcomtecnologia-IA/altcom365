"""
extensoes.py
Instância única do SQLAlchemy compartilhada pelos módulos novos do portal
(Clientes e Processos, e futuramente Maturidade). NÃO é usada pelo Laudos —
models.py mantém sua própria instância, desconectada do app (ver nota em
clientes/models.py sobre por quê).
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
