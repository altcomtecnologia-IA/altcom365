"""
tests/conftest.py
Fixtures compartilhadas para os testes HTTP do módulo Clientes e Processos
(tests/test_clientes_auth.py, tests/test_health_bypass.py).

Não importa app.py — app.py carrega o Laudos inteiro (pandas, engine de
classificação, upload de planilha, etc.), peso desnecessário pra testar só
o blueprint clientes/ e a checagem de /health. Monta aqui um Flask app
mínimo com só o que os testes precisam: altcom_auth (o before_request
compartilhado — precisa estar presente pra provar que ele NÃO basta
sozinho, item B/D3), a rota /health, extensoes.db e clientes_bp.

Requer TEST_DATABASE_URL apontando pra um Postgres com a migration da Fase
1 aplicada (alembic upgrade head) e o mesmo dono de tabela que produção usa
(altcom365_app, não superusuário — C6 da Fase 1: comportamento do
REVOKE/trigger de append-only muda dependendo de quem é dono). Sem essa
env var, os testes que dependem de banco são pulados com motivo explícito
em vez de falhar — não há uma verdade universal sobre onde rodar Postgres
em CI.
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import jwt as pyjwt

# ── Chave RSA sintética — mesmo padrão de tests/test_auth.py ────────────────
_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537, key_size=2048, backend=default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_KID = "test-kid-clientes-001"
_AUD = "test-aud-clientes-abc123"
_ISS = "https://altcom.cloudflareaccess.com"
_TEAM = "altcom.cloudflareaccess.com"



def fazer_token(email="teste@altcom.com.br", aud=None, iss=None,
                 exp_delta=3600, kid=_KID, omitir_email=False):
    """Gera um JWT sintético assinado com a chave privada de teste."""
    now = int(time.time())
    payload = {
        "aud": [aud or _AUD],
        "iss": iss or _ISS,
        "iat": now,
        "exp": now + exp_delta,
    }
    if not omitir_email:
        payload["email"] = email
    return pyjwt.encode(
        payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": kid},
    )


@pytest.fixture()
def _mock_jwks(monkeypatch):
    """
    Substitui a busca de certs do Cloudflare (rede) pela chave pública
    sintética, e fixa CF_ACCESS_TEAM_DOMAIN/CF_ACCESS_AUD nos valores que
    fazer_token() assina, via monkeypatch (restaura sozinho por teste).

    NÃO é autouse — é dependência explícita do fixture `client` (abaixo),
    não do conftest inteiro. tests/test_auth.py também seta
    CF_ACCESS_TEAM_DOMAIN/CF_ACCESS_AUD, com valores DIFERENTES, a nível de
    módulo (no import, sem reset) — se este fixture fosse autouse, um
    conftest.py em tests/ se aplica a TODO teste da pasta, unittest
    incluído, e sobrescreveria a configuração de test_auth.py em toda
    execução, quebrando aqueles testes por contaminação cruzada entre
    arquivos, não por bug de verdade. Escopo explícito evita isso nos dois
    sentidos.
    """
    from altcom_auth import jwt as auth_jwt
    monkeypatch.setenv('CF_ACCESS_TEAM_DOMAIN', _TEAM)
    monkeypatch.setenv('CF_ACCESS_AUD', _AUD)
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    monkeypatch.delenv('AUTH_BYPASS_PATHS', raising=False)
    monkeypatch.setattr(auth_jwt, '_get_certs', lambda force_refresh=False: {_KID: _PUBLIC_KEY})
    yield


def _test_database_url():
    return os.environ.get('TEST_DATABASE_URL', '')


@pytest.fixture(scope='session')
def database_url():
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL não definida — defina apontando para um "
            "Postgres com `alembic upgrade head` aplicado (dono das "
            "tabelas = mesmo usuário do DATABASE_URL de produção, não "
            "superusuário) para rodar os testes HTTP de clientes/."
        )
    return url


@pytest.fixture()
def app(database_url):
    """
    App Flask mínimo — altcom_auth + /health + extensoes.db + clientes_bp.
    Deliberadamente NÃO importa app.py (ver docstring do módulo).
    """
    from flask import Flask
    from altcom_auth import registrar as _registrar_auth
    from extensoes import db, normalizar_database_url
    from clientes import clientes_bp

    flask_app = Flask(__name__)
    flask_app.secret_key = 'test-secret-key'
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = normalizar_database_url(database_url)
    flask_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    flask_app.config['TESTING'] = True

    _registrar_auth(flask_app)

    @flask_app.route('/health')
    def health():
        return 'ok', 200

    db.init_app(flask_app)
    flask_app.register_blueprint(clientes_bp)

    yield flask_app


@pytest.fixture()
def client(app, _mock_jwks):
    return app.test_client()


@pytest.fixture()
def db_session(app):
    """
    Dá acesso a extensoes.db dentro do contexto do app, para os testes
    inserirem/limparem linhas de `usuario` diretamente. Cada teste que usar
    isso é responsável por limpar o que inseriu (DELETE explícito) — não
    dá pra rollback por transação porque test_client() faz sua própria
    requisição/commit.
    """
    from extensoes import db
    with app.app_context():
        yield db
