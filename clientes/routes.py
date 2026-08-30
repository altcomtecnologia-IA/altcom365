"""
clientes/routes.py
Rotas do módulo Clientes e Processos.

Fase 1, passo 3: só o essencial para provar blueprint + decorator
funcionando de ponta a ponta em produção. As telas de cliente e plano de
verdade (briefing seção 8, passo 4) ainda não entram aqui.
"""
from flask import Blueprint, jsonify, g

from .auth import requer_clientes

clientes_bp = Blueprint('clientes', __name__, url_prefix='/clientes')


@clientes_bp.route('/')
@requer_clientes('clientes.ler')
def index():
    identidade = g.identidade_clientes
    return jsonify({
        "modulo": "Clientes e Processos",
        "email": identidade["email"],
        "papel": identidade["papel"],
        "status": "Fase 1 — telas de cliente e plano chegam no passo 4",
    })
