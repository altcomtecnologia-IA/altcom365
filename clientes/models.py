"""
clientes/models.py
Núcleo de dados do módulo Clientes e Processos.
Fase 1 (briefing 4.1): Cliente, ClienteContato, Plano, FaixaRollout.
Fase 2 (briefing 4.2): Ativo, AtivoInterface, Credencial.
Sistemas, perfis e checklist (4.3, 4.4) ainda não entraram.

Usa a instância db de extensoes.py, NÃO a de models.py (que é do Laudos e
não está conectada ao app hoje — ver diagnóstico enviado ao Altair em
29-30/08/2026: nenhuma rota de app.py importa models.py, e as duas rotas
que referenciam ClientesMap/DispositivosMap ali quebrariam com NameError se
fossem chamadas. Essas tabelas não nascem neste banco.
"""
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET, MACADDR
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


class Ativo(db.Model):
    """
    Fase 2 (briefing 4.2). `atributos` é JSONB livre nesta fase — o
    "schema por tipo, validado, cadastrável" que o briefing descreve
    pressupõe uma tabela de configuração que ainda não existe; validar as
    chaves por tipo (roteador tem dns_ativo/faixa_dhcp/..., firewall tem
    licenca_vence_em/vpn_..., etc. — ver briefing 4.2) fica em Python nas
    rotas de escrita, não em CHECK constraint. Lacuna registrada, não
    resolvida agora.

    ATIVO NUNCA É APAGADO — decisão do Altair (passo Fase 2, 31/08/2026):
    apagar um ativo deixaria credenciais órfãs, apontando (via
    credencial.escopo_id) para um equipamento que não existe mais — um
    segredo sem dono conhecido, e sem jeito de saber de qual máquina era.
    `status` já prevê exatamente esse caso: um ativo desligado vira
    status='baixado', nunca DELETE FROM ativo. Por isso não existe (e não
    deve existir) rota DELETE /clientes/ativos/<id> — só a mudança de
    status via UPDATE.
    """
    __tablename__ = 'ativo'
    __table_args__ = (
        db.CheckConstraint(
            "tipo IN ('roteador', 'firewall', 'switch', 'ap', 'link', "
            "'servidor', 'nvr', 'nobreak')",
            name='ck_ativo_tipo',
        ),
        db.CheckConstraint(
            "status IN ('ativo', 'reserva', 'baixado')",
            name='ck_ativo_status',
        ),
    )

    id            = _uuid_pk()
    cliente_id    = db.Column(UUID(as_uuid=True), db.ForeignKey('cliente.id'), nullable=False)
    tipo          = db.Column(db.Text, nullable=False)
    apelido       = db.Column(db.Text, nullable=False)
    marca         = db.Column(db.Text)
    modelo        = db.Column(db.Text)
    numero_serie  = db.Column(db.Text)
    unidade       = db.Column(db.Text)
    localizacao   = db.Column(db.Text)
    status        = db.Column(db.Text, nullable=False, server_default='ativo')
    instalado_em  = db.Column(db.Date)
    garantia_ate  = db.Column(db.Date)
    atributos     = db.Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    interfaces = db.relationship('AtivoInterface', backref='ativo', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Ativo {self.apelido} tipo={self.tipo} status={self.status}>'


class AtivoInterface(db.Model):
    """Fase 2 (briefing 4.2). ON DELETE CASCADE existe por completude do
    schema (é o SQL do briefing), mas na prática nunca dispara: ativo não
    é apagado (ver Ativo.__doc__)."""
    __tablename__ = 'ativo_interface'
    __table_args__ = (
        db.CheckConstraint(
            "atribuicao IS NULL OR atribuicao IN ('fixo', 'dhcp', 'pppoe')",
            name='ck_ativo_interface_atribuicao',
        ),
    )

    id          = _uuid_pk()
    ativo_id    = db.Column(UUID(as_uuid=True), db.ForeignKey('ativo.id', ondelete='CASCADE'), nullable=False)
    nome        = db.Column(db.Text)          # WAN1, LAN, VLAN20
    ip          = db.Column(INET)
    mascara     = db.Column(db.Text)
    gateway     = db.Column(INET)
    atribuicao  = db.Column(db.Text)          # fixo|dhcp|pppoe
    vlan        = db.Column(db.Integer)
    mac         = db.Column(MACADDR)


class Credencial(db.Model):
    """
    Fase 2 (briefing 4.2, 3.1-3.5). `segredo_cifrado` e `nonce` são
    escritos e lidos só por clientes/cifra.py — nenhuma rota monta ou
    interpreta o ciphertext diretamente. `nonce` mora em coluna própria,
    não concatenado ao ciphertext (decisão do Altair: auditável sem
    depender de convenção de offset).

    `chave_versao` (decisão do Altair, mesmo espírito do `ciclo` em
    cliente_documento): hoje sempre 1 — clientes/cifra.CHAVE_VERSAO_ATUAL
    — mas existe desde já para o dia em que a chave precisar rotacionar;
    sem esta coluna, rotação vira "adivinha com qual chave cada linha foi
    cifrada", que não tem resposta.

    `escopo_tipo`/`escopo_id` é polimórfico (ativo|sistema|cliente) —
    sem FK possível (não há uma tabela só para apontar), e `sistema`
    (cliente_sistema, briefing 4.3) nem existe ainda. Validar que
    escopo_id aponta pra uma linha de verdade é responsabilidade da rota
    de escrita, não do banco, nesta fase.

    Sem rota DELETE, mesmo raciocínio do Ativo: revelação e trilha de
    acesso (segredo_acesso_log.credencial_id) precisam sobreviver à
    credencial que descrevem. Rotação de senha é UPDATE (segredo_cifrado,
    nonce, chave_versao, rotacionada_em), nunca um novo registro nem um
    apagar-e-recriar.
    """
    __tablename__ = 'credencial'
    __table_args__ = (
        db.CheckConstraint(
            "escopo_tipo IN ('ativo', 'sistema', 'cliente')",
            name='ck_credencial_escopo_tipo',
        ),
        db.CheckConstraint(
            "sensibilidade IN ('operacional', 'administrativa')",
            name='ck_credencial_sensibilidade',
        ),
    )

    id                = _uuid_pk()
    cliente_id        = db.Column(UUID(as_uuid=True), db.ForeignKey('cliente.id'), nullable=False)
    escopo_tipo       = db.Column(db.Text, nullable=False)
    escopo_id         = db.Column(UUID(as_uuid=True))
    sensibilidade     = db.Column(db.Text, nullable=False)
    rotulo            = db.Column(db.Text, nullable=False)
    usuario           = db.Column(db.Text)
    url               = db.Column(db.Text)
    segredo_cifrado   = db.Column(db.LargeBinary, nullable=False)
    nonce             = db.Column(db.LargeBinary, nullable=False)
    chave_versao      = db.Column(db.Integer, nullable=False, server_default=text('1'))
    mfa_observacao    = db.Column(db.Text)
    rotacionada_em    = db.Column(db.Date)
    criado_em         = db.Column(db.DateTime(timezone=True), nullable=False,
                                   server_default=text('now()'))

    def __repr__(self):
        return f'<Credencial {self.rotulo} sensibilidade={self.sensibilidade}>'


class Quarentena(db.Model):
    """
    Histórico de dispositivos em quarentena (acompanhamento comercial).
    Registros nunca são deletados — status muda de 'ativa' -> 'expirada'/'liberada'.
    Permite ao analista saber se o dispositivo já foi tratado antes.

    status:
      'ativa'    -> em quarentena, oculto do laudo principal
      'expirada' -> 30 dias passaram, voltou ao laudo com badge de histórico
      'liberada' -> analista encerrou antecipadamente (ação concluída)
    """
    __tablename__ = 'quarentena'
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('ativa', 'expirada', 'liberada')",
            name='ck_quarentena_status',
        ),
    )

    id             = _uuid_pk()
    dispositivo    = db.Column(db.Text, nullable=False)
    cliente        = db.Column(db.Text, nullable=False)
    motivo         = db.Column(db.Text)          # 'SSD' / 'RAM' / 'Win11' / 'Outro'
    acao_tomada    = db.Column(db.Text)          # descricao tecnica da acao
    adicionado_por = db.Column(db.Text)          # email do analista
    adicionado_em  = db.Column(db.Date, nullable=False, server_default=text('CURRENT_DATE'))
    expira_em      = db.Column(db.Date, nullable=False)
    status         = db.Column(db.Text, nullable=False, server_default='ativa')
