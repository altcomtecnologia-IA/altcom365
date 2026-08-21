"""
altcom_auth/middleware.py
Hook Flask before_request: autenticação de todas as rotas.

Comportamento:
  AUTH_ENABLED=false  → passa tudo, g.identidade = None (modo emergência)
  AUTH_BYPASS_PATHS   → paths isentos (health check, etc.)
  JWT inválido        → 403 (motivo no log, não no corpo da resposta)
  Email não na lista  → 403
  OK                  → g.identidade preenchido para uso nos decorators/rotas
"""
import os, logging
from flask import request, g, jsonify
from .jwt import validar_token
from .identidade import obter_identidade, carregar_usuarios

logger = logging.getLogger(__name__)

# Paths sempre isentos de auth — não alterar sem revisão de segurança
_BYPASS_FIXO = {'/health', '/healthz'}


def _auth_habilitado() -> bool:
    return os.environ.get('AUTH_ENABLED', 'true').lower() not in ('false', '0', 'no')


def _bypass_paths() -> set:
    raw = os.environ.get('AUTH_BYPASS_PATHS', '')
    extras = {p.strip() for p in raw.split(',') if p.strip()}
    return _BYPASS_FIXO | extras


def registrar(app):
    """
    Registra o middleware no app Flask.
    Chamar uma vez no startup (app.py), antes de qualquer request.
    Não altera nenhuma rota existente.
    """
    # Pré-carrega usuários no startup de cada worker
    with app.app_context():
        carregar_usuarios()

    @app.before_request
    def _verificar_auth():
        # Interruptor de emergência: AUTH_ENABLED=false → sem auth (modo observação)
        if not _auth_habilitado():
            # Mesmo desligado, tenta logar quem chegou (sem bloquear)
            _obs_token = (request.headers.get('Cf-Access-Jwt-Assertion') or
                          request.cookies.get('CF_Authorization', ''))
            if _obs_token:
                try:
                    _obs_email = validar_token(_obs_token)
                    try:
                        _obs_id = obter_identidade(_obs_email)
                        logger.info(
                            "[AUTH-OBS] email=%s | grupo=%s | rota=%s",
                            _obs_email, _obs_id['grupo'], request.path
                        )
                    except ValueError:
                        logger.info(
                            "[AUTH-OBS] email=%s | grupo=DESCONHECIDO | rota=%s",
                            _obs_email, request.path
                        )
                except ValueError:
                    logger.debug("[AUTH-OBS] token inválido | rota=%s", request.path)
            g.identidade = None
            return

        # Paths isentos (health check, webhooks máquina-a-máquina)
        if request.path in _bypass_paths():
            g.identidade = None
            return

        # Token do header preferido; cookie como fallback
        token = (request.headers.get('Cf-Access-Jwt-Assertion') or
                 request.cookies.get('CF_Authorization', ''))

        # Validar JWT
        try:
            email = validar_token(token)
        except ValueError as motivo:
            logger.warning(
                "AUTH NEGADO | rota=%s | método=%s | motivo=%s",
                request.path, request.method, motivo
            )
            # Motivo genérico no corpo — não vazar detalhe interno
            return jsonify({"erro": "Acesso não autorizado"}), 403

        # Verificar se email está na lista de usuários autorizados
        try:
            identidade = obter_identidade(email)
        except ValueError:
            logger.warning(
                "AUTH NEGADO | email=%s | rota=%s | motivo=usuário não na lista",
                email, request.path
            )
            return jsonify({"erro": "Acesso não autorizado"}), 403

        # Acesso autorizado — registrar e disponibilizar identidade
        logger.info(
            "AUTH OK | email=%s | grupo=%s | rota=%s | método=%s",
            email, identidade['grupo'], request.path, request.method
        )
        g.identidade = identidade
