"""
tests/test_clientes_auth.py
Testes HTTP e unitários da autorização do módulo Clientes e Processos
(clientes/auth.py) — Decisão 5 e complemento B do passo 3.

Requer TEST_DATABASE_URL (ver tests/conftest.py) — pulados com motivo
explícito se ausente, não falham.
"""
import uuid

import pytest

from tests.conftest import fazer_token


# ═══════════════════════════════════════════════════════════════════════════
# 1. HTTP — GET /clientes/ (única rota real do passo 3)
# ═══════════════════════════════════════════════════════════════════════════

def test_sem_token_nega(client):
    resp = client.get('/clientes/')
    assert resp.status_code == 403


def test_token_invalido_nega(client):
    resp = client.get('/clientes/', headers={'Cf-Access-Jwt-Assertion': 'nao-e-um-jwt'})
    assert resp.status_code == 403


def test_email_nao_cadastrado_nega_fail_closed(client):
    """
    Token assinado e válido, mas o e-mail não existe na tabela `usuario` —
    o caso prático de "papel sem capacidade" no nível HTTP (ver docstring
    de clientes/auth.py: fail-closed é o default, não uma checagem extra).
    """
    email = f"nao-cadastrado-{uuid.uuid4().hex[:8]}@altcom.com.br"
    token = fazer_token(email=email)
    resp = client.get('/clientes/', headers={'Cf-Access-Jwt-Assertion': token})
    assert resp.status_code == 403


def test_usuario_inativo_nega(client, db_session):
    from portal.models import Usuario
    email = f"inativo-{uuid.uuid4().hex[:8]}@altcom.com.br"
    usuario = Usuario(email=email, nome="Teste Inativo", papel='gestor', ativo=False)
    db_session.session.add(usuario)
    db_session.session.commit()
    try:
        token = fazer_token(email=email)
        resp = client.get('/clientes/', headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 403
    finally:
        db_session.session.delete(usuario)
        db_session.session.commit()


@pytest.mark.parametrize('papel', ['n1n2', 'n3', 'gestor'])
def test_papel_valido_com_capacidade_retorna_200(client, db_session, papel):
    """Os três papéis válidos têm clientes.ler (n1n2 e n3 diretamente, gestor via clientes.*)."""
    from portal.models import Usuario
    email = f"{papel}-{uuid.uuid4().hex[:8]}@altcom.com.br"
    usuario = Usuario(email=email, nome=f"Teste {papel}", papel=papel, ativo=True)
    db_session.session.add(usuario)
    db_session.session.commit()
    try:
        token = fazer_token(email=email)
        resp = client.get('/clientes/', headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 200
        corpo = resp.get_json()
        assert corpo['email'] == email
        assert corpo['papel'] == papel
    finally:
        db_session.session.delete(usuario)
        db_session.session.commit()


# ═══════════════════════════════════════════════════════════════════════════
# 2. Complemento B — AUTH_ENABLED / AUTH_BYPASS_PATHS do middleware
#    compartilhado NÃO abrem exceção para clientes/ (a revalidação
#    independente nega mesmo quando o middleware deixaria passar)
# ═══════════════════════════════════════════════════════════════════════════

def test_auth_enabled_false_nao_da_bypass_em_clientes(client, monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    resp = client.get('/clientes/')
    assert resp.status_code == 403


def test_auth_bypass_paths_nao_da_bypass_em_clientes(client, monkeypatch):
    """
    Path exato da rota testada em AUTH_BYPASS_PATHS — se a checagem de
    clientes/ delegasse pro middleware compartilhado (o que ela
    deliberadamente não faz), isto passaria como 200 sem token.
    """
    monkeypatch.setenv('AUTH_BYPASS_PATHS', '/clientes/')
    resp = client.get('/clientes/')
    assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 3. Unitário — fail-closed para papel fora do mapa
#    (estado impossível de inserir via banco por causa do ck_usuario_papel;
#    só testável na função pura)
# ═══════════════════════════════════════════════════════════════════════════

def test_capacidades_papel_desconhecido_retorna_vazio():
    from clientes.auth import _capacidades_do_papel
    assert _capacidades_do_papel('algum_papel_desconhecido') == frozenset()


def test_capacidades_papel_none_retorna_vazio():
    from clientes.auth import _capacidades_do_papel
    assert _capacidades_do_papel(None) == frozenset()


def test_tem_capacidade_wildcard_gestor():
    from clientes.auth import tem_capacidade, CAPACIDADES_GESTOR
    assert tem_capacidade(CAPACIDADES_GESTOR, 'clientes.qualquer.coisa.nova')


def test_tem_capacidade_wildcard_nao_escapa_do_prefixo_clientes():
    """
    Achado do passo 3, 30/08/2026: clientes.* tinha escopo implícito — só
    inofensivo hoje porque nenhuma capacidade fora de "clientes." existe
    ainda. Prova que o curinga não cobre um nome de módulo futuro
    (Painel de Maturidade, por exemplo) mesmo sem esse módulo existir.
    """
    from clientes.auth import tem_capacidade, CAPACIDADES_GESTOR
    assert not tem_capacidade(CAPACIDADES_GESTOR, 'maturidade.editar')
    assert not tem_capacidade(CAPACIDADES_GESTOR, 'admin.usuarios')


# ═══════════════════════════════════════════════════════════════════════════
# 5. Achado do passo 3 — exceção não-ValueError em resolver_identidade
#    (endpoint de certs do Cloudflare fora do ar) deve virar 403 limpo,
#    nunca 500 com traceback
# ═══════════════════════════════════════════════════════════════════════════

def test_erro_inesperado_na_validacao_vira_403_nao_500(client, monkeypatch):
    """
    validar_token() não levanta só ValueError — _get_certs() ->
    _fetch_certs() usa urllib.request.urlopen, que levanta
    URLError/HTTPError/timeout se o endpoint do Cloudflare estiver
    inacessível com o cache frio/expirado. Simula isso substituindo
    validar_token (só o de clientes/auth.py — deliberadamente reimportado
    lá, não o mesmo objeto de altcom_auth/middleware.py) por algo que
    levanta uma exceção genérica, e confirma que o decorator ainda
    responde 403 limpo em vez de deixar a exceção estourar pro Flask virar
    500.

    Precisa de um JWT que passe pela validação DO MIDDLEWARE COMPARTILHADO
    primeiro (fazer_token(), a mesma revalidada por _mock_jwks) — um token
    malformado já seria negado ali, antes de a requisição sequer chegar na
    rota /clientes/ e no validar_token patchado abaixo, e o teste passaria
    por um motivo errado (confirmado: a primeira versão deste teste usava
    um token malformado e continuava verde mesmo sem a correção).
    """
    import clientes.auth as clientes_auth

    def _explode(token):
        raise OSError("endpoint de certs indisponível (simulado)")

    monkeypatch.setattr(clientes_auth, 'validar_token', _explode)
    token = fazer_token(email="qualquer@altcom.com.br")
    resp = client.get('/clientes/', headers={'Cf-Access-Jwt-Assertion': token})
    assert resp.status_code == 403
    assert resp.get_json() == {"erro": "Acesso não autorizado"}


# ═══════════════════════════════════════════════════════════════════════════
# 4. Item E — casos da matriz D4 que dependem de uma rota de revelação de
#    credencial que ainda não existe (Fase 2). Marcados, não esquecidos.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="rota de revelação de credencial: Fase 2")
def test_revelar_credencial_gera_linha_em_segredo_acesso_log():
    pass


@pytest.mark.skip(reason="rota de revelação de credencial: Fase 2")
def test_revelar_credencial_nega_para_n1n2():
    pass
