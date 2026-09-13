"""
clientes/auditoria.py
Escrita em audit_log e segredo_acesso_log (portal/models.py), a partir do
módulo Clientes. Mesma regra de isolamento de clientes/auth.py — nunca lê
g.identidade; toda função aqui recebe a identidade já resolvida (o dict
que g.identidade_clientes guarda) como parâmetro explícito.

audit_log e segredo_acesso_log são append-only só no banco (REVOKE +
triggers, na migration) — este módulo não impõe isso, só escreve; a
proteção real está na migration, não aqui.
"""
from flask import request

from extensoes import db
from portal.models import AuditLog, SegredoAcessoLog

# Briefing 3.4: "quando o campo alterado for segredo_cifrado, gravar
# '[redigido]' em antes e depois". `nonce` e `chave_versao` não são o
# segredo em si (GCM não exige sigilo do nonce, e a versão é só um
# inteiro) — só segredo_cifrado é redigido.
CAMPOS_REDIGIDOS = ('segredo_cifrado',)


def redigir(dados):
    """
    dados: dict ou None. Substitui toda chave em CAMPOS_REDIGIDOS pelo
    literal '[redigido]', preserva o resto. Não modifica dados in-place.
    """
    if not dados:
        return dados
    return {
        chave: ('[redigido]' if chave in CAMPOS_REDIGIDOS else valor)
        for chave, valor in dados.items()
    }


def registrar_auditoria(identidade, acao, entidade, entidade_id, antes=None, depois=None):
    """
    Grava uma linha em audit_log. `antes`/`depois` já devem ter passado
    por redigir() quando envolverem credencial — chamado aqui de novo por
    segurança (redigir() é idempotente: '[redigido]' não é uma chave em
    CAMPOS_REDIGIDOS, uma segunda passada não muda nada), nunca confie só
    no chamador ter lembrado.
    """
    log = AuditLog(
        usuario_id=identidade.get('usuario_id'),
        acao=acao,
        entidade=entidade,
        entidade_id=entidade_id,
        antes=redigir(antes),
        depois=redigir(depois),
        ip=request.remote_addr,
        user_agent=request.headers.get('User-Agent'),
    )
    db.session.add(log)


def registrar_acesso_segredo(identidade, credencial_id, motivo, resultado):
    """
    Grava uma linha em segredo_acesso_log. `resultado` ∈ {'concedido',
    'negado'} (CHECK no banco força isso também — aqui é defesa em
    profundidade, não a única barreira).

    `credencial_id` pode apontar para um id que não existe — ver
    docstring de SegredoAcessoLog em portal/models.py: é deliberado,
    não FK, porque uma tentativa contra um id inexistente é justamente
    um dos sinais que este log precisa capturar.

    Chamado ANTES de devolver o segredo no caminho de sucesso (briefing
    3.2: "Registro em segredo_acesso_log antes de devolver o valor") —
    responsabilidade de quem chama, não desta função, já que "antes de
    devolver" é uma questão de ORDEM na rota, não algo que esta função
    consiga garantir sozinha.
    """
    if resultado not in ('concedido', 'negado'):
        raise ValueError(f"resultado inválido: {resultado!r}")

    log = SegredoAcessoLog(
        credencial_id=credencial_id,
        usuario_id=identidade['usuario_id'],
        motivo=motivo,
        resultado=resultado,
        ip=request.remote_addr,
    )
    db.session.add(log)
