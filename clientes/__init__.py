"""
clientes/
Módulo "Clientes e Processos" do Altcom 365. Blueprint isolado — não
importa nem referencia o código do Laudos (app.py, models.py, engine_*).

Autorização própria, independente de altcom_auth/g.identidade — ver
clientes/auth.py e tests/test_clientes_isolamento.py.
"""
from .routes import clientes_bp

__all__ = ['clientes_bp']
