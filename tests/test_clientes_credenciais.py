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

from extensoes import db
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
    """'impressora' era o exemplo de tipo inválido até a Fase 2.1 (item 3)
    estender ck_ativo_tipo/_TIPOS_ATIVO_VALIDOS — passou a ser aceito (ver
    test_criar_ativo_tipo_modem_aceito, que cobre exatamente esse tipo
    novo). 'gerador' continua fora da lista dos dois lados."""
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/ativos', json={
            "cliente_id": str(cliente.id), "tipo": "gerador", "apelido": "X",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_ativo_tipo_modem_aceito(client, db_session, cliente):
    """Fase 2.1, item 3: 'modem' entrou em ck_ativo_tipo e em
    _TIPOS_ATIVO_VALIDOS — é o tipo mais numeroso na planilha real (27
    modems/ONTs), sem ele boa parte dos ativos não entraria na
    importação."""
    from clientes.models import Ativo
    u = criar_usuario(db_session, 'n3')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/ativos', json={
            "cliente_id": str(cliente.id), "tipo": "modem", "apelido": "Modem-01",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 201
        ativo = db.session.get(Ativo, uuid.UUID(resp.get_json()['id']))
        assert ativo is not None
        assert ativo.tipo == 'modem'
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Fase 2.1, item 5 — validação de data na borda da API (400, não 500)
# ═══════════════════════════════════════════════════════════════════════════

def test_criar_ativo_data_malformada_400_nao_500(client, db_session, cliente):
    """instalado_em/garantia_ate iam direto pra coluna Date — data
    malformada virava erro de banco (500) em vez de 400. O script de
    importação vai empurrar exatamente esse tipo de sujeira (datas
    escritas à mão em formatos variados)."""
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/ativos', json={
            "cliente_id": str(cliente.id), "tipo": "roteador", "apelido": "X",
            "instalado_em": "31/09/2026",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
        assert 'instalado_em' in resp.get_json()['erro']

        resp2 = client.post('/clientes/ativos', json={
            "cliente_id": str(cliente.id), "tipo": "roteador", "apelido": "Y",
            "garantia_ate": "não é uma data",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp2.status_code == 400
        assert 'garantia_ate' in resp2.get_json()['erro']
    finally:
        db_session.session.delete(u)
        db_session.session.commit()


def test_criar_credencial_rotacionada_em_malformada_400_nao_500(client, db_session, cliente):
    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/credenciais', json={
            "cliente_id": str(cliente.id), "escopo_tipo": "cliente",
            "sensibilidade": "operacional", "rotulo": "x", "segredo": "y",
            "rotacionada_em": "32/13/2026",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
        assert 'rotacionada_em' in resp.get_json()['erro']
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


def test_criar_credencial_escopo_ativo_de_outro_cliente_nega(client, db_session, cliente):
    """
    Correção pós-entrega da Fase 2, 13/09/2026: escopo_id precisa
    pertencer ao cliente_id do mesmo request — um ativo que existe, mas
    é de outro cliente, tem que ser recusado como se não existisse
    (mesmo 400 de "não corresponde a nenhum ativo").
    """
    from clientes.models import Ativo, Cliente

    outro_cnpj = str(uuid.uuid4().int)[:14].ljust(14, '0')
    outro_cliente = Cliente(razao_social=f"Outro Cliente {outro_cnpj}", cnpj=outro_cnpj)
    db_session.session.add(outro_cliente)
    db_session.session.commit()
    ativo_de_outro = Ativo(cliente_id=outro_cliente.id, tipo='switch', apelido='SW-outro')
    db_session.session.add(ativo_de_outro)
    db_session.session.commit()

    u = criar_usuario(db_session, 'gestor')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/credenciais', json={
            "cliente_id": str(cliente.id), "escopo_tipo": "ativo",
            "escopo_id": str(ativo_de_outro.id),
            "sensibilidade": "operacional", "rotulo": "x", "segredo": "y",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 400
        assert 'nenhum ativo' in resp.get_json()['erro']
    finally:
        db_session.session.delete(u)
        db_session.session.commit()
        Ativo.query.filter_by(id=ativo_de_outro.id).delete(synchronize_session=False)
        Cliente.query.filter_by(id=outro_cliente.id).delete(synchronize_session=False)
        db_session.session.commit()


def test_criar_credencial_para_cliente_em_grupo(client, db_session, cliente):
    """
    Fase 2.1, item 1: cliente pode pertencer a um grupo (caso real: eCar/
    Guia Serviços/Lyon Despachante, uma infraestrutura só sob três CNPJs).
    A credencial continua presa a cliente_id normalmente — grupo_id não
    existe em credencial nem em ativo, de propósito (a leitura "mostrar
    também os ativos/credenciais dos outros clientes do grupo" é Fase 3).
    Este teste só confirma que o schema aditivo funciona ponta a ponta:
    cliente com grupo_id setado continua aceitando credencial normalmente,
    e o vínculo cliente -> grupo é lido de volta corretamente.
    """
    from clientes.models import Credencial, Cliente, Grupo

    grupo = Grupo(nome=f"Grupo Teste {uuid.uuid4().hex[:8]}", observacao="eCar/Guia/Lyon")
    db_session.session.add(grupo)
    db_session.session.commit()
    cliente.grupo_id = grupo.id
    db_session.session.commit()

    u = criar_usuario(db_session, 'n3')
    try:
        token = fazer_token(email=u.email)
        resp = client.post('/clientes/credenciais', json={
            "cliente_id": str(cliente.id), "escopo_tipo": "cliente",
            "sensibilidade": "operacional", "rotulo": "Wi-Fi loja", "segredo": "senha123",
        }, headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 201
        cred = db.session.get(Credencial, uuid.UUID(resp.get_json()['id']))
        assert cred is not None
        assert cred.cliente_id == cliente.id
        cliente_da_credencial = db.session.get(Cliente, cred.cliente_id)
        assert cliente_da_credencial.grupo_id == grupo.id
        assert cliente_da_credencial.grupo.nome == grupo.nome
    finally:
        db_session.session.delete(u)
        cliente.grupo_id = None
        db_session.session.commit()
        Grupo.query.filter_by(id=grupo.id).delete(synchronize_session=False)
        db_session.session.commit()


# ═══════════════════════════════════════════════════════════════════════════
# GET /clientes/<cliente_id>/credenciais — listagem, nunca o segredo
# ═══════════════════════════════════════════════════════════════════════════

def _criar_credencial_direta(db_session, cliente, sensibilidade, rotulo,
                              observacao=None, atributos=None):
    """Gera o id em Python antes de cifrar — mesmo caminho de
    routes.criar_credencial(): o id é o AAD (ver clientes/cifra.py),
    então precisa existir antes da chamada a cifrar(), não só no insert.

    observacao/atributos (Fase 2.1, item 2) são opcionais aqui porque a
    maioria dos testes que usam este helper não se importa com eles —
    só os que testam a listagem/revelação de metadados passam valor."""
    from clientes.cifra import cifrar
    from clientes.models import Credencial
    credencial_id = uuid.uuid4()
    ct, nonce, versao = cifrar('segredo-de-teste', credencial_id.bytes)
    c = Credencial(
        id=credencial_id, cliente_id=cliente.id, escopo_tipo='cliente',
        sensibilidade=sensibilidade, rotulo=rotulo,
        segredo_cifrado=ct, nonce=nonce, chave_versao=versao,
        observacao=observacao, atributos=atributos or {},
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


def test_listar_credenciais_observacao_e_atributos_aparecem(client, db_session, cliente):
    """
    Fase 2.1, item 2: observacao e atributos são metadados de contexto
    (não segredo) — precisam aparecer na listagem, é o que permite o
    analista entender o acesso sem revelar a senha. segredo_cifrado/
    nonce/chave_versao continuam de fora, como já estavam.
    """
    cred = _criar_credencial_direta(
        db_session, cliente, 'operacional', 'Wi-Fi loja',
        observacao='com limite de velocidade em 10Mbps',
        atributos={'ssid': 'MILBOM ADM'},
    )
    u = criar_usuario(db_session, 'n3')
    try:
        token = fazer_token(email=u.email)
        resp = client.get(f'/clientes/{cliente.id}/credenciais',
                           headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 200
        corpo = {c['id']: c for c in resp.get_json()}
        item = corpo[str(cred.id)]
        assert item['observacao'] == 'com limite de velocidade em 10Mbps'
        assert item['atributos'] == {'ssid': 'MILBOM ADM'}
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


def test_revelar_credencial_nao_devolve_observacao_nem_atributos(client, db_session, cliente):
    """
    Fase 2.1, item 2: observacao/atributos aparecem na LISTAGEM (ver
    test_listar_credenciais_observacao_e_atributos_aparecem), mas o
    endpoint de revelação continua devolvendo só {"segredo": ...} — nunca
    ganhou mais campos com a Fase 2.1, mesmo pra uma credencial que tem
    observacao e atributos preenchidos.
    """
    cred = _criar_credencial_direta(
        db_session, cliente, 'operacional', 'Wi-Fi loja',
        observacao='Gerenciador no pc do Rodolfo',
        atributos={'ssid': 'MILBOM ADM'},
    )
    u = criar_usuario(db_session, 'n1n2')
    try:
        token = fazer_token(email=u.email)
        resp = client.post(f'/clientes/credenciais/{cred.id}/revelar',
                            json={"motivo": "conferindo observacao/atributos"},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 200
        corpo = resp.get_json()
        assert corpo == {"segredo": "segredo-de-teste"}
        assert 'observacao' not in corpo
        assert 'atributos' not in corpo
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


def test_revelar_ciphertext_movido_de_outra_credencial_falha_integridade(client, db_session, cliente):
    """
    O cenário que motivou a AAD (correção pós-entrega, 13/09/2026): mover
    segredo_cifrado+nonce de uma credencial administrativa para uma linha
    operacional do MESMO cliente não pode devolver o segredo pelo caminho
    fraco. Sem AAD, a decifra passaria normal (mesma chave, chave_versao
    igual) e um n1n2 revelaria o segredo administrativo via a credencial
    operacional. Com AAD = credencial.id, o id não bate — InvalidTag
    interrompe a decifra: 500 de integridade, não 403 de permissão, e não
    é confundido com negação de acesso no log.
    """
    from portal.models import SegredoAcessoLog

    admin_cred = _criar_credencial_direta(db_session, cliente, 'administrativa', 'Firewall admin')
    op_cred = _criar_credencial_direta(db_session, cliente, 'operacional', 'Wi-Fi loja')

    # Simula o ataque: ciphertext/nonce da credencial administrativa
    # "vazam" para a linha operacional — exatamente o que a AAD tem que
    # impedir de decifrar.
    op_cred.segredo_cifrado = admin_cred.segredo_cifrado
    op_cred.nonce = admin_cred.nonce
    db_session.session.commit()

    u = criar_usuario(db_session, 'n1n2')
    try:
        token = fazer_token(email=u.email)
        resp = client.post(f'/clientes/credenciais/{op_cred.id}/revelar',
                            json={"motivo": "tentando o caminho fraco"},
                            headers={'Cf-Access-Jwt-Assertion': token})
        assert resp.status_code == 500
        assert 'segredo' not in resp.get_json()

        # Erro de integridade não é negação de acesso — não gera linha em
        # segredo_acesso_log (mesmo padrão já usado para ChaveInvalidaError).
        log = SegredoAcessoLog.query.filter_by(credencial_id=op_cred.id).first()
        assert log is None
    finally:
        db_session.session.delete(u)
        db_session.session.commit()
