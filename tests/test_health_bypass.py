"""
tests/test_health_bypass.py
Item D (passo 3): /health não pode ser movido/renomeado nem passar a
exigir autenticação — é o path que o health check do Render consulta e o
único que altcom_auth/middleware.py isenta por padrão (_BYPASS_FIXO), e
está confirmado em produção (AUTH_BYPASS_PATHS=/health no painel do
Render, evidência de log trazida por Altair em 30/08/2026: GET /health →
200 sem header de auth).

Trava dois comportamentos com o app mínimo de tests/conftest.py (que já
registra altcom_auth normalmente, sem nenhum bypass especial de teste):
  1. /health responde 200 sem qualquer token.
  2. A reorganização em blueprint (clientes_bp registrado no mesmo app)
     não interferiu no bypass — a rota continua exatamente onde estava.
"""


def test_health_sem_token_retorna_200(client):
    resp = client.get('/health')
    assert resp.status_code == 200


def test_health_com_token_invalido_ainda_retorna_200(client):
    """
    Bypass por path é incondicional (comparação exata de string em
    _bypass_paths(), confirmado em altcom_auth/middleware.py) — nem um
    header Authorization malformado deveria mudar o resultado.
    """
    resp = client.get('/health', headers={'Cf-Access-Jwt-Assertion': 'lixo-nao-e-jwt'})
    assert resp.status_code == 200
