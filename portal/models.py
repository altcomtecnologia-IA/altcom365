"""
portal/models.py
Identidade e trilhas de auditoria — nível PORTAL, não do módulo Clientes.

usuario: mapa email -> papel. Fonte da verdade para autorização de QUALQUER
módulo do Altcom 365 (o Cloudflare Access/Entra autentica quem é; esta
tabela decide o que cada um pode). Hoje só o módulo Clientes e Processos lê
daqui; o Painel de Maturidade, quando virar módulo, lê da mesma tabela —
não duplicar.

audit_log e sessao_log: apenas o fato, nunca o segredo. Quando o campo
alterado for segredo_cifrado (tabela credencial, Fase 2), quem grava em
audit_log é responsável por passar '[redigido]' em antes/depois — esta
tabela não sabe o que é um segredo, só registra o que a aplicação mandar.

Ambas as tabelas de log são append-only por TRÊS mecanismos independentes,
todos na migration (não aqui):
  1. REVOKE UPDATE/DELETE do role de runtime (altcom365_app) — pega
     qualquer role futuro que não seja dono, mas não vale contra o dono.
  2. Trigger BEFORE UPDATE OR DELETE FOR EACH ROW — aborta mesmo para o
     dono da tabela. Testado ao vivo nos dois papéis.
  3. Trigger BEFORE TRUNCATE FOR EACH STATEMENT — o trigger do item 2 não
     dispara em TRUNCATE (FOR EACH ROW nunca dispara nesse comando), e sem
     este terceiro mecanismo o dono apaga a trilha inteira sem erro nenhum.

usuario_id em AuditLog e SessaoLog é PROPOSITALMENTE sem ForeignKey para
usuario. Isso é decisão, não descuido — não "consertar" adicionando FK
depois. O log tem que sobreviver à remoção da linha em usuario (que pode
acontecer: usuário desligado, papel corrigido por engano e recriado, etc.).
Com FK, ON DELETE CASCADE apagaria a trilha do usuário removido — o oposto
de append-only — e ON DELETE SET NULL/RESTRICT criaria um acoplamento que
trava a manutenção de `usuario` pela existência de log antigo. Sem FK, o
log guarda o UUID como esteve no momento do evento, para sempre, e resolve
o nome por join best-effort quando a linha ainda existir.
"""
from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET
from sqlalchemy import text

from extensoes import db


def _uuid_pk():
    return db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class Usuario(db.Model):
    __tablename__ = 'usuario'
    __table_args__ = (
        db.CheckConstraint("papel IN ('n1n2', 'n3', 'gestor')", name='ck_usuario_papel'),
    )

    id            = _uuid_pk()
    email         = db.Column(db.Text, nullable=False, unique=True)
    nome          = db.Column(db.Text)
    papel         = db.Column(db.Text, nullable=False)   # n1n2|n3|gestor
    ativo         = db.Column(db.Boolean, nullable=False, server_default=text('true'))
    ultimo_acesso = db.Column(db.DateTime(timezone=True))

    def __repr__(self):
        return f'<Usuario {self.email} papel={self.papel}>'


class AuditLog(db.Model):
    """
    A tabela que mais cresce e a mais consultada ("o que aconteceu com este
    cliente", "o que este usuário fez"). Índice (entidade, entidade_id)
    abaixo, via ORM. O índice (usuario_id, em DESC) — ordem descendente não
    tem forma portável direta no Column/Index do SQLAlchemy declarative
    nesta versão — vive só na migration.
    """
    __tablename__ = 'audit_log'
    __table_args__ = (
        db.Index('ix_audit_log_entidade', 'entidade', 'entidade_id'),
    )

    id          = db.Column(db.BigInteger, primary_key=True)
    usuario_id  = db.Column(UUID(as_uuid=True))
    em          = db.Column(db.DateTime(timezone=True), nullable=False,
                             server_default=text('now()'))
    acao        = db.Column(db.Text, nullable=False)
    entidade    = db.Column(db.Text, nullable=False)
    entidade_id = db.Column(UUID(as_uuid=True))
    antes       = db.Column(JSONB)   # '[redigido]' quando o campo for segredo_cifrado
    depois      = db.Column(JSONB)
    ip          = db.Column(INET)
    user_agent  = db.Column(db.Text)


class SessaoLog(db.Model):
    __tablename__ = 'sessao_log'

    id         = db.Column(db.BigInteger, primary_key=True)
    usuario_id = db.Column(UUID(as_uuid=True))
    em         = db.Column(db.DateTime(timezone=True), nullable=False,
                            server_default=text('now()'))
    ip         = db.Column(INET)
    user_agent = db.Column(db.Text)
