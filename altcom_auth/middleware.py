"""
altcom_auth/middleware.py
Hook Flask before_request: autenticação de todas as rotas.

Comportamento:
  AUTH_ENABLED=false  → passa tudo, g.identidade = None (modo emergência)
  AUTH_BYPASS_PATHS   → paths isentos (health check, etc.)
  JWT inválido        → 403 (motivo no log, não no corpo da resposta)
  JWT válido          → acesso total (grupo tecnico_gestao)
                        Autorização de quem pode acessar é responsabilidade do
                        Cloudflare Access (grupo Entra ID Altcom365-Laudos).
"""
import os, logging
from flask import request, g, jsonify
from .jwt import validar_token
from .identidade import GRUPOS

logger = logging.getLogger(__name__)

_BYPASS_FIXO = {'/health', '/healthz'}
_GRUPO_PADRAO = 'tecnico_gestao'


def _auth_habilitado() -> bool:
    return os.environ.get('AUTH_ENABLED', 'true').lower() not in ('false', '0', 'no')


def _bypass_paths() -> set:
    raw = os.environ.get('AUTH_BYPASS_PATHS', '')
    extras = {p.strip() for p in raw.split(',') if p.strip()}
    return _BYPASS_FIXO | extras


def registrar(app):
    @app.before_request
    def _verificar_auth():
        if not _auth_habilitado():
            g.identidade = None
            return
        if request.path in _bypass_paths():
            g.identidade = None
            return
        token = (request.headers.get('Cf-Access-Jwt-Assertion') or
                 request.cookies.get('CF_Authorization', ''))
        try:
            email = validar_token(token)
        except ValueError as motivo:
            logger.warning(
                "AUTH NEGADO | rota=%s | método=%s | motivo=%s",
                request.path, request.method, motivo
            )
            return jsonify({"erro": "Acesso não autorizado"}), 403
        g.identidade = {
            "email":       email,
            "nome":        email,
            "grupo":       _GRUPO_PADRAO,
            "capacidades": GRUPOS[_GRUPO_PADRAO],
        }
        logger.info(
            "AUTH OK | email=%s | grupo=%s | rota=%s | método=%s",
            email, _GRUPO_PADRAO, request.path, request.method
        )
