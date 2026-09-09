"""
quarentena_ops.py
Operacoes CRUD para a tabela quarentena (acompanhamento comercial).
Usa db de extensoes.py — mesmo padrao de clientes/models.py.
"""
from datetime import date, timedelta
from flask import g
from extensoes import db
from clientes.models import Quarentena


def _expirar_vencidas() -> None:
    """Marca como expiradas quarentenas cujo prazo passou. Chamada lazily."""
    hoje = date.today()
    Quarentena.query.filter(
        Quarentena.status == 'ativa',
        Quarentena.expira_em < hoje,
    ).update({'status': 'expirada'}, synchronize_session=False)
    db.session.commit()


def quarentenar(dispositivo: str, cliente: str,
                motivo: str, acao_tomada: str) -> Quarentena:
    """
    Cria novo registro de quarentena ativa (30 dias).
    Se ja existir um registro ativo para o mesmo dispositivo+cliente,
    encerra o anterior e abre um novo (renovacao com nova acao).
    """
    hoje = date.today()
    analista = getattr(getattr(g, 'identidade', {}), 'get', lambda k, d=None: d)('email', '')
    if not analista and hasattr(g, 'identidade') and isinstance(g.identidade, dict):
        analista = g.identidade.get('email', '')

    # Encerra registro ativo anterior (se houver)
    Quarentena.query.filter_by(
        dispositivo=dispositivo, cliente=cliente, status='ativa'
    ).update({'status': 'liberada'}, synchronize_session=False)

    q = Quarentena(
        dispositivo=dispositivo,
        cliente=cliente,
        motivo=motivo,
        acao_tomada=acao_tomada,
        adicionado_por=analista,
        adicionado_em=hoje,
        expira_em=hoje + timedelta(days=30),
        status='ativa',
    )
    db.session.add(q)
    db.session.commit()
    return q


def liberar(qid: str) -> bool:
    """Libera antecipadamente uma quarentena pelo ID (UUID string)."""
    q = db.session.get(Quarentena, qid)
    if q and q.status == 'ativa':
        q.status = 'liberada'
        db.session.commit()
        return True
    return False


def get_ativas_set() -> set:
    """
    Retorna set de (dispositivo, cliente) com quarentena ativa.
    Expira vencidas antes de consultar.
    """
    _expirar_vencidas()
    rows = Quarentena.query.filter_by(status='ativa').all()
    return {(r.dispositivo, r.cliente) for r in rows}


def get_ativas_lista() -> list:
    """
    Retorna lista de dicts das quarentenas ativas (para a aba 'Em acompanhamento').
    """
    _expirar_vencidas()
    rows = (Quarentena.query
            .filter_by(status='ativa')
            .order_by(Quarentena.cliente, Quarentena.dispositivo)
            .all())
    hoje = date.today()
    return [
        {
            'id':            str(r.id),
            'dispositivo':   r.dispositivo,
            'cliente':       r.cliente,
            'motivo':        r.motivo or '',
            'acao_tomada':   r.acao_tomada or '',
            'adicionado_por': r.adicionado_por or '',
            'adicionado_em': r.adicionado_em.isoformat() if r.adicionado_em else '',
            'expira_em':     r.expira_em.isoformat() if r.expira_em else '',
            'dias_restantes': (r.expira_em - hoje).days if r.expira_em else 0,
        }
        for r in rows
    ]


def tem_historico_set() -> set:
    """
    Retorna set de (dispositivo, cliente) que ja tiveram quarentena
    encerrada (expirada ou liberada) — usada para badge 'Ja tratado'.
    """
    rows = Quarentena.query.filter(
        Quarentena.status.in_(['expirada', 'liberada'])
    ).all()
    return {(r.dispositivo, r.cliente) for r in rows}


def get_historico(dispositivo: str, cliente: str) -> list:
    """Retorna historico completo de quarentenas para um dispositivo."""
    rows = (Quarentena.query
            .filter_by(dispositivo=dispositivo, cliente=cliente)
            .order_by(Quarentena.adicionado_em.desc())
            .all())
    return [
        {
            'id':            str(r.id),
            'motivo':        r.motivo or '',
            'acao_tomada':   r.acao_tomada or '',
            'adicionado_por': r.adicionado_por or '',
            'adicionado_em': r.adicionado_em.isoformat() if r.adicionado_em else '',
            'expira_em':     r.expira_em.isoformat() if r.expira_em else '',
            'status':        r.status,
        }
        for r in rows
    ]
