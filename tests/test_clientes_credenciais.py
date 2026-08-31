"""
tests/test_clientes_credenciais.py
Testes HTTP das rotas de Fase 2 (clientes/routes.py): ativos, interfaces,
credenciais e a listagem/revelação (briefing 3.1-3.5, 4.2, decisões 8-10
do passo Fase 2 — 31/08/2026).

Requer TEST_DATABASE_URL com a migration ec4549622c1d aplicada (ver
tests/conftest.py) — pulados com motivo se ausente.
"""
import uuid

import pytest

from tests.conftest import fazer_token, criar_usuario


# ═══════════════════════════════════════════════════════════════════════════
# POST /clientes/ativos
# ═══════════════════════════════════════════════════════════════════════════

def test_criar_ativo_sem_token_nega(client, cliente):
    resp = client.post('/clientes/ativos', json={
        "cliente_id": str(cliente.id), "tipo": "firewall", "apelido": "FW-01",
    })
    assert resp.status_code == 403


def test_criar_ativo_n1n2_sem_capacidade_nega(client, db_session, cliente):
    u = criar_usuario(db_session, 'n1n2')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/ativos', json={
            "cliente_id": str(cliente.id), "tipo": "firewall", "apelido": "FW-01",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 403
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_ativo_n3_sucesso(client, db_session, cliente):
    from clientes.models import Ativo
    u = criar_usuario(db_session, 'n3')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/ativos', json={
            "cliente_id": str(cliente.id), "tipo": "firewall", "apelido": "FW-01",
            "marca": "WatchGuard",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 201
        ativo_id = resp.get_json()['id']
        ativo = Ativo.query.get(uuid.UUID(ativo_id))
        assert ativo is not None
        assert ativo.apelido == 'FW-01'
        assert ativo.status == 'ativo'   # default, não informado
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_ativo_campo_obrigatorio_faltando(client, db_session, cliente):
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/ativos', json={"cliente_id": str(cliente.id)},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_ativo_tipo_invalido(client, db_session, cliente):
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/ativos', json={
            "cliente_id": str(cliente.id), "tipo": "impressora", "apelido": "X",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_ativo_cliente_inexistente(client, db_session):
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/ativos', json={
            "cliente_id": str(uuid.uuid4()), "tipo": "firewall", "apelido": "X",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


# ═══════════════════════════════════════════════════════════════════════════
# POST /clientes/ativos/<id>/interfaces
# ═══════════════════════════════════════════════════════════════════════════

def test_criar_interface_ativo_inexistente(client, db_session):
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post(f'/clientes/ativos/{uuid.uuid4()}/interfaces', json={"nome": "WAN1"},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 404
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_interface_sucesso(client, db_session, cliente):
    from clientes.models import Ativo, AtivoInterface
    u = criar_usuario(db_session, 'n3')
    try:
        token = fazer_token(email=u.email)
        ativo = Ativo(cliente_id=cliente.id, tipo='roteador', apelido='RT-01')
        db_session.session.add(ativo)
        db_session.session.commit()

        resp = client.post(f'/clientes/ativos/{ativo.id}/interfaces', json={
            "nome": "WAN1", "ip": "203.0.113.5", "atribuicao": "fixo",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 201
        interface_id = resp.get_json()['id']
        assert AtivoInterface.query.get(uuid.UUID(interface_id)) is not None
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


# ═══════════════════════════════════════════════════════════════════════════
# POST /clientes/credenciais
# ═══════════════════════════════════════════════════════════════════════════

def test_criar_credencial_n1n2_sem_capacidade_nega(client, db_session, cliente):
    u = criar_usuario(db_session, 'n1n2')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/credenciais', json={
            "cliente_id": str(cliente.id), "escopo_tipo": "cliente",
            "sensibilidade": "operacional", "rotulo": "teste", "segredo": "abc123",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 403
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_credencial_n3_sucesso_e_segredo_nunca_em_texto_puro(client, db_session, cliente):
    from clientes.models import Credencial
    from portal.models import AuditLog
    u = criar_usuario(db_session, 'n3')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/credenciais', json={
            "cliente_id": str(cliente.id), "escopo_tipo": "cliente",
            "sensibilidade": "administrativa", "rotulo": "Firewall admin",
            "usuario": "admin", "segredo": "senha-em-texto-puro-123",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 201
        cred_id = uuid.UUID(resp.get_json()['id'])

        credencial = Credencial.query.get(cred_id)
        assert credencial is not None
        assert credencial.segredo_cifrado != b'senha-em-texto-puro-123'
        assert b'senha-em-texto-puro-123' not in credencial.segredo_cifrado
        assert len(credencial.nonce) == 12
        assert credencial.chave_versao == 1

        # audit_log com o segredo redigido, nunca o valor real
        log = AuditLog.query.filter_by(entidade='credencial', entidade_id=cred_id).first()
        assert log is not None
        assert log.depois['segredo_cifrado'] == '[redigido]'
        assert 'senha-em-texto-puro-123' not in str(log.depois)
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_credencial_escopo_ativo_exige_escopo_id(client, db_session, cliente):
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/credenciais', json={
            "cliente_id": str(cliente.id), "escopo_tipo": "ativo",
            "sensibilidade": "operacional", "rotulo": "x", "segredo": "y",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_credencial_escopo_ativo_id_inexistente(client, db_session, cliente):
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/credenciais', json={
            "cliente_id": str(cliente.id), "escopo_tipo": "ativo", "escopo_id": str(uuid.uuid4()),
            "sensibilidade": "operacional", "rotulo": "x", "segredo": "y",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


# ═══════════════════════════════════════════════════════════════════════════
# GET /clientes/<cliente_id>/credenciais — listagem, nunca o segredo
# ═══════════════════════════════════════════════════════════════════════════

def _criar_credencial_direta(db_session, cliente, sensibilidade, rotulo):
    from clientes.cifra import cifrar
    from clientes.models import Credencial
    ct, nonce, versao = cifrar('segredo-de-teste')
    c = Credencial(
        cliente_id=cliente.id, escopo_tipo='cliente', sensibilidade=sensibilidade,
        rotulo=rotulo, segredo_cifrado=ct, nonce=nonce, chave_versao=versao,
    )
    db_session.session.add(c)
    db_session.session.commit()
    return c


def test_listar_credenciais_sem_token_nega(client, cliente):
    resp = client.get(f'/clientes/{cliente.id}/credenciais')
    assert resp.status_code == 403


def test_listar_credenciais_mascara_administrativa_para_n1n2(client, db_session, cliente):
    op = _criar_credencial_direta(db_session, cliente, 'operacional', 'Wi-Fi loja')
    adm = _criar_credencial_direta(db_session, cliente, 'administrativa', 'Firewall admin')
    u = criar_usuario(db_session, 'n1n2')
    try:
        token = fazer_token(email=u.email)
        resp = client.get(f'/clientes/{cliente.id}/credenciais',
                           headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 200
        corpo = {c['id']: c for c in resp.get_json()}

        assert corpo[str(op.id)]['bloqueada'] is False
        assert corpo[str(adm.id)]['bloqueada'] is True

        # nunca o segredo, cifrado ou não, em nenhuma das duas
        for item in corpo.values():
            assert 'segredo_cifrado' not in item
            assert 'nonce' not in item
            assert 'chave_versao' not in item
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_listar_credenciais_n3_ve_administrativa_desbloqueada(client, db_session, cliente):
    adm = _criar_credencial_direta(db_session, cliente, 'administrativa', 'Firewall admin')
    u = criar_usuario(db_session, 'n3')
    try:
        token = fazer_token(email=u.email)
        resp = client.get(f'/clientes/{cliente.id}/credenciais',
                           headers={'Cf-Access-Jwt-Assertion': token})
        corpo = {c['id']: c for c in resp.get_json()}
        assert corpo[str(adm.id)]['bloqueada'] is False
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


# ═══════════════════════════════════════════════════════════════════════════
# POST /clientes/credenciais/<id>/revelar
# ═══════════════════════════════════════════════════════════════════════════

def test_revelar_sem_motivo_nega_400(client, db_session, cliente):
    cred = _criar_credencial_direta(db_session, cliente, 'operacional', 'x')
    u = criar_usuario(db_session, 'n1n2')
    try:
        token = fazer_token(email=u.email)
        resp = client.post(f'/clientes/credenciais/{cred.id}/revelar', json={},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_revelar_credencial_inexistente_nega_403_e_loga_negado(client, db_session):
    from portal.models import SegredoAcessoLog
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        id_inexistente = uuid.uuid4()
        resp = client.post(f'/clientes/credenciais/{id_inexistente}/revelar',
                            json={"motivo": "conferindo"},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 403

        log = SegredoAcessoLog.query.filter_by(credencial_id=id_inexistente).first()
        assert log is not None
        assert log.resultado == 'negado'
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_revelar_operacional_n1n2_concede(client, db_session, cliente):
    from portal.models import SegredoAcessoLog
    cred = _criar_credencial_direta(db_session, cliente, 'operacional', 'Wi-Fi loja')
    u = criar_usuario(db_session, 'n1n2')
    try:
        token = fazer_token(email=u.email)
        resp = client.post(f'/clientes/credenciais/{cred.id}/revelar',
                            json={"motivo": "cliente esqueceu a senha do wifi"},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 200
        assert resp.get_json()['segredo'] == 'segredo-de-teste'

        log = SegredoAcessoLog.query.filter_by(credencial_id=cred.id).first()
        assert log is not None
        assert log.resultado == 'concedido'
        assert log.motivo == 'cliente esqueceu a senha do wifi'
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_revelar_administrativa_n1n2_nega_e_loga(client, db_session, cliente):
    """
    O caso central da Decisão 8: o decorator sozinho deixaria passar
    (n1n2 tem a capacidade operacional), a checagem de segunda etapa
    dentro da rota é quem nega de verdade.
    """
    from portal.models import SegredoAcessoLog
    cred = _criar_credencial_direta(db_session, cliente, 'administrativa', 'Firewall admin')
    u = criar_usuario(db_session, 'n1n2')
    try:
        token = fazer_token(email=u.email)
        resp = client.post(f'/clientes/credenciais/{cred.id}/revelar',
                            json={"motivo": "só pra ver"},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 403
        assert 'segredo' not in resp.get_json()

        log = SegredoAcessoLog.query.filter_by(credencial_id=cred.id).first()
        assert log is not None
        assert log.resultado == 'negado'
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_revelar_administrativa_n3_concede(client, db_session, cliente):
    cred = _criar_credencial_direta(db_session, cliente, 'administrativa', 'Firewall admin')
    u = criar_usuario(db_session, 'n3')
    try:
        token = fazer_token(email=u.email)
        resp = client.post(f'/clientes/credenciais/{cred.id}/revelar',
                            json={"motivo": "troca de senha programada"},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 200
        assert resp.get_json()['segredo'] == 'segredo-de-teste'
    finally:
        db_session.session.delete(u)
        db_session.session.commit()
