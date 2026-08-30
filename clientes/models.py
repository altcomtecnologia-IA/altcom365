"""
clientes/models.py
Núcleo de dados do módulo Clientes e Processos — Fase 1 (briefing seção 4.1).
Credenciais, ativos, sistemas e checklist entram nas migrations da Fase 2.

Usa a instância db de extensoes.py, NÃO a de models.py (que é do Laudos e
não está conectada ao app hoje — ver diagnóstico enviado ao Altair em
29-30/08/2026: nenhuma rota de app.py importa models.py, e as duas rotas
que referenciam ClientesMap/DispositivosMap ali quebrariam com NameError se
fossem chamadas. Essas tabelas não nascem neste banco.
"""
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import text

from extensoes import db


def _uuid_pk():
    return db.Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class Cliente(db.Model):
    __tablename__ = 'cliente'
    __table_args__ = (
        db.CheckConstraint("status IN ('ativo', 'suspenso', 'encerrado')", name='ck_cliente_status'),
        # CNPJ é a única chave comum a Conta Azul, Milvus e o portal — sem
        # normalização, "12.345.678/0001-90" e "12345678000190" viram dois
        # clientes e o UNIQUE não percebe. Decisão registrada: normalizar
        # agora, na Fase 1, enquanto a tabela está vazia (grátis) e antes do
        # cadastro dos 27 planos (passo 5) — que é justamente o momento de
        # maior risco de entrada inconsistente, sem UI ainda para forçar o
        # formato. Cadastro/edição (passo 4) só precisa normalizar para
        # dígitos antes do INSERT/UPDATE; a constraint é a rede de segurança.
        db.CheckConstraint("cnpj ~ '^[0-9]{14}$'", name='ck_cliente_cnpj_normalizado'),
    )

    id              = _uuid_pk()
    razao_social    = db.Column(db.Text, nullable=False)
    nome_fantasia   = db.Column(db.Text)
    cnpj            = db.Column(db.Text, nullable=False, unique=True)
    cidade          = db.Column(db.Text)
    uf              = db.Column(db.CHAR(2))
    status          = db.Column(db.Text, nullable=False, server_default='ativo')
    data_assinatura = db.Column(db.Date)
    criado_em       = db.Column(db.DateTime(timezone=True), nullable=False,
                                 server_default=text('now()'))

    contatos = db.relationship('ClienteContato', backref='cliente', cascade='all, delete-orphan')
    planos   = db.relationship('Plano', backref='cliente', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Cliente {self.razao_social}>'


class ClienteContato(db.Model):
    __tablename__ = 'cliente_contato'

    id                = _uuid_pk()
    cliente_id        = db.Column(UUID(as_uuid=True), db.ForeignKey('cliente.id'), nullable=False)
    nome              = db.Column(db.Text, nullable=False)
    cargo             = db.Column(db.Text)
    email             = db.Column(db.Text)
    telefone          = db.Column(db.Text)
    departamento      = db.Column(db.Text)
    eh_responsavel_ti = db.Column(db.Boolean, nullable=False, server_default=text('false'))


class Plano(db.Model):
    """
    Versionado: vigencia_fim NULL = vigente. A VIEW plano_vigente (criada na
    migration, não modelada aqui como classe — é consulta, não tabela) lista
    as colunas explicitamente, NÃO usa SELECT * — o Postgres congela a lista
    de colunas no CREATE VIEW, e SELECT * faria uma alteração futura de
    `plano` (Fase 2+) devolver, em silêncio, o conjunto antigo de colunas
    pela view. Qualquer migration que altere `plano` precisa DROP + CREATE
    desta view (CREATE OR REPLACE VIEW não muda a lista de colunas).

    O índice único parcial ux_plano_vigente_por_cliente (também só na
    migration, é filtrado e o SQLAlchemy declarative não expressa isso de
    forma portável) garante que nunca existam duas linhas vigentes para o
    mesmo cliente — sem ele a view pode devolver mais de uma linha por
    cliente e ninguém percebe até o código pegar a primeira arbitrariamente.

    Só o plano VIGENTE (vigencia_fim NULL) é protegido contra sobreposição.
    Dois planos FECHADOS (ambos com vigencia_fim preenchido) podem se
    sobrepor no tempo sem que nada acuse — lacuna conhecida e aceita, não
    bug: o alvo do índice parcial é sempre existir no máximo um vigente por
    cliente, não impedir qualquer sobreposição histórica.
    """
    __tablename__ = 'plano'
    __table_args__ = (
        db.CheckConstraint("tipo_plano IN ('completo', 'remoto')", name='ck_plano_tipo_plano'),
        db.CheckConstraint(
            "antivirus IN ('acronis_incluso', 'watchguard_incluso', 'watchguard_direto')",
            name='ck_plano_antivirus',
        ),
        db.CheckConstraint(
            "backup_m365 IN ('nenhum', 'sharepoint', 'completo')",
            name='ck_plano_backup_m365',
        ),
        # Briefing 5.4: plano remoto não tem frequência de visita; completo
        # tem 30 ou 45 dias, nunca outro valor e nunca NULL.
        db.CheckConstraint(
            "(tipo_plano = 'remoto' AND frequencia_visita_dias IS NULL) OR "
            "(tipo_plano = 'completo' AND frequencia_visita_dias IN (30, 45))",
            name='ck_plano_frequencia_por_tipo',
        ),
        # Sem isto, vigencia_fim anterior a vigencia_inicio corrompe a linha
        # do tempo em silêncio — só aparece meses depois, ao reconstruir o
        # histórico de um cliente.
        db.CheckConstraint(
            "vigencia_fim IS NULL OR vigencia_fim >= vigencia_inicio",
            name='ck_plano_vigencia_coerente',
        ),
    )

    id                          = _uuid_pk()
    cliente_id                  = db.Column(UUID(as_uuid=True), db.ForeignKey('cliente.id'), nullable=False)
    vigencia_inicio             = db.Column(db.Date, nullable=False)
    vigencia_fim                = db.Column(db.Date)
    tipo_plano                  = db.Column(db.Text, nullable=False)
    frequencia_visita_dias      = db.Column(db.Integer)
    antivirus                   = db.Column(db.Text, nullable=False)
    backup_m365                 = db.Column(db.Text, nullable=False)
    suporte_dominio_atualizacao = db.Column(db.Boolean, nullable=False, server_default=text('false'))
    suporte_estendido           = db.Column(db.Boolean, nullable=False, server_default=text('false'))
    escopo_fixado               = db.Column(db.Boolean, nullable=False, server_default=text('false'))
    qtd_estacoes                = db.Column(db.Integer)
    estacoes_verificado_em      = db.Column(db.Date)
    observacoes                 = db.Column(db.Text)


class FaixaRollout(db.Model):
    """
    Faixas fixas e cadastradas (briefing 5.1): (20, 2), (30, 3), (NULL, 4).
    O SQL da seção 4.1 não define chave primária — adicionada aqui pelo
    padrão UUID do resto do schema; sinalizado ao Altair, não decidido
    unilateralmente em silêncio.

    As três linhas são semeadas na própria migration (sem seed a tabela
    existe e a regra de negócio não). O índice único em maquinas_ate que
    torna a faixa não ambígua usa UNIQUE NULLS NOT DISTINCT — sintaxe só de
    DDL, sem equivalente direto e portável no Column/Index do SQLAlchemy
    declarative nesta versão — por isso também vive só na migration.
    """
    __tablename__ = 'faixa_rollout'

    id             = _uuid_pk()
    maquinas_ate   = db.Column(db.Integer)          # 20, 30, NULL (acima)
    rollouts_mes   = db.Column(db.Integer, nullable=False)   # 2, 3, 4
