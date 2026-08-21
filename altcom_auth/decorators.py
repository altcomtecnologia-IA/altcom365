"""
altcom_auth/decorators.py
Decorator @requer("capacidade") para proteção de rotas individuais.

Uso:
    @app.route('/laudo/<id>/excluir', methods=['POST'])
    @requer('laudo:excluir')
    def excluir_laudo(id): ...

Se AUTH_ENABLED=false, g.identidade é None e o decorator não bloqueia nada.
"""
import logging
from functools import wraps
from flask import g, jsonify, request
from .identidade import tem_capacidade

logger = logging.getLogger(__name__)


def requer(capacidade: str):
    """Exige que o usuário autenticado tenha a capacidade informada."""
    def decorador(fn):
        # Marca a função com a capacidade exigida (útil para introspecção/testes)
        fn._capacidade_requerida = capacidade

        @wraps(fn)
        def wrapper(*args, **kwargs):
            identidade = getattr(g, 'identidade', None)

            # AUTH_ENABLED=false → identidade é None → liberar
            if identidade is None:
                return fn(*args, **kwargs)

            if not tem_capacidade(identidade, capacidade):
                logger.warning(
                    "PERMISSÃO NEGADA | email=%s | grupo=%s | "
                    "capacidade_requerida=%s | rota=%s",
                    identidade.get('email', '?'),
                    identidade.get('grupo', '?'),
                    capacidade,
                    request.path,
                )
                return jsonify({
                    "erro": "Permissão insuficiente",
                    "capacidade_requerida": capacidade,
                }), 403

            return fn(*args, **kwargs)
        return wrapper
    return decorador
