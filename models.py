"""
models.py — Altcom 365 V11
Todos os modelos SQLAlchemy para a plataforma operacional.

Tabelas:
  clientes_map       — mapeamento de clientes Milvus (ID + token)
  dispositivos_map   — mapeamento de dispositivos Milvus (IDs para ações)
  perfil_cliente     — ficha de perfil operacional do cliente (blocos 1-5)
  perfil_sistemas    — sistemas por departamento (1:N de perfil_cliente)
  laudos_snapshots   — histórico de processamentos por cliente
  chamados_criados   — anti-duplicação de chamados Milvus
  exclusoes_log      — auditoria de exclusões de dispositivos

REGRA: nenhum dado de classificação fica APENAS no banco.
O Excel continua sendo a fonte verdade de classificação.

ATENÇÃO (diagnóstico de 30/08/2026, ao iniciar o módulo Clientes e
Processos): este arquivo não é importado por app.py. Nenhuma rota faz
`from models import` ou `import models`. As rotas /sync-status e
/sync-clientes referenciam ClientesMap, DispositivosMap e db.session sem
nenhum import que traga esses nomes para o escopo — se qualquer uma for
chamada, quebra com NameError (/sync-clientes também usa a variável `agora`,
nunca definida na função). O comentário "V11: PostgreSQL + SQLAlchemy +
Flask-Migrate" no topo do app.py nunca virou código: nem a normalização de
DATABASE_URL de 'postgres://' para 'postgresql://' que o comentário lá
anuncia foi escrita.
Conclusão: estas tabelas nunca foram criadas em banco nenhum e não têm
escrita real em produção. Ficam de fora do schema do módulo Clientes e
Processos (ver clientes/models.py e portal/models.py, que usam a instância
`db` de extensoes.py, não a deste arquivo). Reativar a sincronização com o
Milvus aqui é decisão própria, separada, a ser tomada explicitamente — não
algo para "descobrir" num diff.
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ── Mapeamento de clientes Milvus ─────────────────────────────────────────────

class ClientesMap(db.Model):
    __tablename__ = 'clientes_map'

    id                = db.Column(db.Integer, primary_key=True)
    nome_fantasia     = db.Column(db.String(200), unique=True, nullable=False, index=True)
    milvus_cliente_id = db.Column(db.Integer)
    milvus_token      = db.Column(db.String(300))  # token por cliente para criar chamados
    ultima_sync       = db.Column(db.DateTime)

    # Relacionamentos
    perfil   = db.relationship('PerfilCliente',  back_populates='cliente', uselist=False, cascade='all, delete-orphan')
    sistemas = db.relationship('PerfilSistemas', back_populates='cliente', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<ClientesMap {self.nome_fantasia}>'

    def to_dict(self):
        return {
            'id':                self.id,
            'nome_fantasia':     self.nome_fantasia,
            'milvus_cliente_id': self.milvus_cliente_id,
            'ultima_sync':       self.ultima_sync.isoformat() if self.ultima_sync else None,
        }


# ── Mapeamento de dispositivos Milvus ─────────────────────────────────────────

class DispositivosMap(db.Model):
    __tablename__ = 'dispositivos_map'
    __table_args__ = (
        db.UniqueConstraint('hostname', 'nome_fantasia', name='uq_dispositivo_cliente'),
    )

    id                    = db.Column(db.Integer, primary_key=True)
    hostname              = db.Column(db.String(200), nullable=False, index=True)
    nome_fantasia         = db.Column(db.String(200), nullable=False, index=True)
    milvus_dispositivo_id = db.Column(db.Integer, index=True)
    is_ativo              = db.Column(db.Boolean, default=True)
    ultima_sync           = db.Column(db.DateTime)

    def __repr__(self):
        return f'<DispositivosMap {self.hostname} / {self.nome_fantasia}>'

    def to_dict(self):
        return {
            'id':                    self.id,
            'hostname':              self.hostname,
            'nome_fantasia':         self.nome_fantasia,
            'milvus_dispositivo_id': self.milvus_dispositivo_id,
            'is_ativo':              self.is_ativo,
            'ultima_sync':           self.ultima_sync.isoformat() if self.ultima_sync else None,
        }


# ── Perfil de Cliente ─────────────────────────────────────────────────────────

class PerfilCliente(db.Model):
    """
    Ficha operacional do cliente — concentra o "como atender este cliente".
    Blocos 1-5 do Plano Mestre V11.
    """
    __tablename__ = 'perfil_cliente'

    id         = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes_map.id'), unique=True, nullable=False)

    # Bloco 1 — Identidade & Acesso
    # Valores: 'AD Local' | 'Entra ID' | 'Híbrido (AD+Entra)' | 'Workgroup'
    diretorio         = db.Column(db.String(50))
    diretorio_detalhes = db.Column(db.Text)
    vpn_possui        = db.Column(db.Boolean, default=False)
    vpn_detalhes      = db.Column(db.Text)   # solução, como conectar, quem libera, obs

    # Bloco 2 — Contrato & Atendimento
    # plano_field: 'Com Field' | 'Sem Field'
    plano_field           = db.Column(db.String(50))
    plano_obs             = db.Column(db.Text)
    manutencao_eletronica = db.Column(db.Text)

    # Bloco 3 — Microsoft / Produtividade
    office_app          = db.Column(db.Boolean, default=False)
    # email_solucao: 'Skymail' | 'Microsoft 365' | 'Google Workspace' | 'Outro'
    email_solucao       = db.Column(db.String(50))
    email_solucao_outro = db.Column(db.String(200))
    # m365_modo: 'Totalmente online' | 'Sincronização local'
    m365_modo           = db.Column(db.String(50))
    # m365_sync_metodo: 'OneDrive' | 'WebDAV' | 'Atalho SharePoint' | 'Outro'
    m365_sync_metodo    = db.Column(db.String(100))
    m365_obs            = db.Column(db.Text)

    # Bloco 5 — Informações adicionais
    info_adicional = db.Column(db.Text)           # texto livre (markdown simples)
    campos_extra   = db.Column(db.JSON)           # pares chave-valor dinâmicos

    # Bloco 6 — Auditoria (automático)
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)
    criado_por = db.Column(db.String(100))
    editado_em = db.Column(db.DateTime)
    editado_por = db.Column(db.String(100))
    historico  = db.Column(db.JSON)               # lista das últimas 10 edições

    # Relacionamento
    cliente = db.relationship('ClientesMap', back_populates='perfil')

    def __repr__(self):
        return f'<PerfilCliente cliente_id={self.cliente_id}>'


# ── Sistemas por Departamento (1:N de PerfilCliente) ─────────────────────────

class PerfilSistemas(db.Model):
    """Bloco 4 do Plano Mestre — sistemas por departamento, relação 1:N."""
    __tablename__ = 'perfil_sistemas'

    id                  = db.Column(db.Integer, primary_key=True)
    perfil_id           = db.Column(db.Integer, db.ForeignKey('perfil_cliente.id'), nullable=False)
    cliente_id          = db.Column(db.Integer, db.ForeignKey('clientes_map.id'), nullable=False, index=True)

    departamento        = db.Column(db.String(200))
    sistema             = db.Column(db.String(200))
    fornecedor          = db.Column(db.String(200))
    fornecedor_contato  = db.Column(db.Text)   # nome/tel/email do fornecedor
    atendimento_conjunto = db.Column(db.Text)  # como agir em atendimento conjunto
    obs                 = db.Column(db.Text)

    # Relacionamento
    cliente = db.relationship('ClientesMap', back_populates='sistemas')

    def __repr__(self):
        return f'<PerfilSistemas {self.departamento} / {self.sistema}>'

    def to_dict(self):
        return {
            'id':                   self.id,
            'departamento':         self.departamento,
            'sistema':              self.sistema,
            'fornecedor':           self.fornecedor,
            'fornecedor_contato':   self.fornecedor_contato,
            'atendimento_conjunto': self.atendimento_conjunto,
            'obs':                  self.obs,
        }


# ── Snapshots de laudos ───────────────────────────────────────────────────────

class LaudosSnapshots(db.Model):
    """
    Histórico de processamentos por cliente.
    Permite comparativo "parque melhorou desde o último laudo?" sem reprocessar.
    """
    __tablename__ = 'laudos_snapshots'

    id                 = db.Column(db.Integer, primary_key=True)
    nome_fantasia      = db.Column(db.String(200), nullable=False, index=True)
    data_processamento = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # JSONs completos (opcional — pode ser nulo para snapshots resumidos)
    laudo_json         = db.Column(db.JSON)
    interno_json       = db.Column(db.JSON)

    # Contadores (denormalizados para comparativo rápido)
    total_dispositivos = db.Column(db.Integer, default=0)
    qtd_critico        = db.Column(db.Integer, default=0)
    qtd_satisfatorio   = db.Column(db.Integer, default=0)
    qtd_bom            = db.Column(db.Integer, default=0)
    qtd_otimo          = db.Column(db.Integer, default=0)
    qtd_excelente      = db.Column(db.Integer, default=0)
    qtd_alertas        = db.Column(db.JSON)   # {armazenamento, windows, sem_contato, milvus}

    def __repr__(self):
        return f'<LaudosSnapshots {self.nome_fantasia} @ {self.data_processamento}>'

    def to_dict(self):
        return {
            'id':                 self.id,
            'nome_fantasia':      self.nome_fantasia,
            'data_processamento': self.data_processamento.isoformat() if self.data_processamento else None,
            'total_dispositivos': self.total_dispositivos,
            'qtd_critico':        self.qtd_critico,
            'qtd_satisfatorio':   self.qtd_satisfatorio,
            'qtd_bom':            self.qtd_bom,
            'qtd_otimo':          self.qtd_otimo,
            'qtd_excelente':      self.qtd_excelente,
            'qtd_alertas':        self.qtd_alertas,
        }


# ── Chamados criados no Milvus (anti-duplicação) ──────────────────────────────

class ChamadosCriados(db.Model):
    """
    Registro de chamados abertos via app.
    Regra: não criar chamado do mesmo (dispositivo_id, tipo_alerta) nos últimos 30 dias.
    """
    __tablename__ = 'chamados_criados'

    id                    = db.Column(db.Integer, primary_key=True)
    milvus_dispositivo_id = db.Column(db.Integer, nullable=False, index=True)
    hostname              = db.Column(db.String(200))
    nome_fantasia         = db.Column(db.String(200))
    # tipo_alerta: 'critico' | 'armazenamento' | 'windows' | 'sem_contato' | 'milvus'
    tipo_alerta           = db.Column(db.String(100), nullable=False)
    chamado_codigo        = db.Column(db.String(100))  # código retornado pelo Milvus
    data_criacao          = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    criado_por            = db.Column(db.String(100))

    def __repr__(self):
        return f'<ChamadosCriados #{self.chamado_codigo} {self.hostname}>'

    def to_dict(self):
        return {
            'id':                    self.id,
            'milvus_dispositivo_id': self.milvus_dispositivo_id,
            'hostname':              self.hostname,
            'nome_fantasia':         self.nome_fantasia,
            'tipo_alerta':           self.tipo_alerta,
            'chamado_codigo':        self.chamado_codigo,
            'data_criacao':          self.data_criacao.isoformat() if self.data_criacao else None,
            'criado_por':            self.criado_por,
        }


# ── Log de exclusões de dispositivos ─────────────────────────────────────────

class ExclusoesLog(db.Model):
    """
    Auditoria de exclusões via DELETE /api/dispositivos/{id}.
    Ação irreversível — registrada para rastreabilidade.
    """
    __tablename__ = 'exclusoes_log'

    id                    = db.Column(db.Integer, primary_key=True)
    milvus_dispositivo_id = db.Column(db.Integer)
    hostname              = db.Column(db.String(200))
    nome_fantasia         = db.Column(db.String(200))
    dias_sem_contato      = db.Column(db.Integer)
    data_exclusao         = db.Column(db.DateTime, default=datetime.utcnow)
    executado_por         = db.Column(db.String(100))

    def __repr__(self):
        return f'<ExclusoesLog {self.hostname} excluído em {self.data_exclusao}>'

    def to_dict(self):
        return {
            'id':                    self.id,
            'milvus_dispositivo_id': self.milvus_dispositivo_id,
            'hostname':              self.hostname,
            'nome_fantasia':         self.nome_fantasia,
            'dias_sem_contato':      self.dias_sem_contato,
            'data_exclusao':         self.data_exclusao.isoformat() if self.data_exclusao else None,
            'executado_por':         self.executado_por,
        }
