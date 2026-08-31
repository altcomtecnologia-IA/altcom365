"""
clientes/routes.py
Rotas do módulo Clientes e Processos.

Fase 1, passo 3: blueprint + decorator (GET /clientes/).
Fase 2 (briefing 4.2, 3.1-3.5): ativos, credenciais cifradas, listagem de
credenciais (metadados, nunca segredo) e o endpoint de revelação.

Sem rota DELETE em nenhum lugar deste arquivo — nem para ativo, nem para
credencial. Ver docstrings de Ativo e Credencial em clientes/models.py:
apagar qualquer um dos dois deixa segredo_acesso_log e/ou credenciais
órfãos, sem dono conhecido. `ativo.status='baixado'` é o "apagar" da
aplicação; credencial não tem equivalente ainda porque rotação (Fase 2)
é UPDATE, e desativar uma credencial fica para quando essa necessidade
aparecer de verdade — não inventada agora.
"""
import uuid as uuid_lib

from flask import Blueprint, jsonify, g, request

from extensoes import db
from .auth import requer_clientes, tem_capacidade
from .auditoria import registrar_auditoria, registrar_acesso_segredo
from .cifra import cifrar, decifrar, ChaveInvalidaError
from .models import Ativo, AtivoInterface, Credencial, Cliente

clientes_bp = Blueprint('clientes', __name__, url_prefix='/clientes')

_TIPOS_ATIVO_VALIDOS = {
    'roteador', 'firewall', 'switch', 'ap', 'link', 'servidor', 'nvr', 'nobreak',
}
_ESCOPO_TIPOS_VALIDOS = {'ativo', 'sistema', 'cliente'}
_SENSIBILIDADES_VALIDAS = {'operacional', 'administrativa'}


def _uuid_ou_none(valor):
    """Converte string -> uuid.UUID; None se valor for None/vazio; levanta
    ValueError se vier algo que não parseia como UUID."""
    if not valor:
        return None
    return uuid_lib.UUID(str(valor))


def _erro(mensagem, codigo, **extra):
    corpo = {"erro": mensagem}
    corpo.update(extra)
    return jsonify(corpo), codigo


@clientes_bp.route('/')
@requer_clientes('clientes.ler')
def index():
    identidade = g.identidade_clientes
    return jsonify({
        "modulo": "Clientes e Processos",
        "email": identidade["email"],
        "papel": identidade["papel"],
        "status": "Fase 1 — telas de cliente e plano chegam no passo 4",
    })


# ═══════════════════════════════════════════════════════════════════════════
# Ativos (briefing 4.2) — escrita mínima: sem tela, sem DELETE.
# ═══════════════════════════════════════════════════════════════════════════

@clientes_bp.route('/ativos', methods=['POST'])
@requer_clientes('clientes.editar')
def criar_ativo():
    """
    Decisão 10 (31/08/2026): rota mínima, não tela — existe pra não
    precisar de outro script ad-hoc no dia em que um ativo novo entrar
    depois da importação inicial (script de migração das planilhas usa
    esta mesma função, não HTTP, mas a validação é a mesma).
    """
    dados = request.get_json(silent=True) or {}

    cliente_id_bruto = dados.get('cliente_id')
    tipo = (dados.get('tipo') or '').strip()
    apelido = (dados.get('apelido') or '').strip()

    if not cliente_id_bruto or not tipo or not apelido:
        return _erro("cliente_id, tipo e apelido são obrigatórios", 400)
    try:
        cliente_id = uuid_lib.UUID(str(cliente_id_bruto))
    except ValueError:
        return _erro("cliente_id não é um UUID válido", 400)
    if tipo not in _TIPOS_ATIVO_VALIDOS:
        return _erro(f"tipo inválido — precisa ser um de {sorted(_TIPOS_ATIVO_VALIDOS)}", 400)
    if Cliente.query.get(cliente_id) is None:
        return _erro("cliente_id não corresponde a nenhum cliente", 400)

    status = dados.get('status', 'ativo')
    if status not in ('ativo', 'reserva', 'baixado'):
        return _erro("status inválido — precisa ser um de ['ativo', 'reserva', 'baixado']", 400)

    ativo = Ativo(
        cliente_id=cliente_id,
        tipo=tipo,
        apelido=apelido,
        marca=dados.get('marca'),
        modelo=dados.get('modelo'),
        numero_serie=dados.get('numero_serie'),
        unidade=dados.get('unidade'),
        localizacao=dados.get('localizacao'),
        status=status,
        instalado_em=dados.get('instalado_em'),
        garantia_ate=dados.get('garantia_ate'),
        atributos=dados.get('atributos') or {},
    )
    db.session.add(ativo)
    db.session.flush()   # popula ativo.id sem fechar a transação

    identidade = g.identidade_clientes
    registrar_auditoria(
        identidade, acao='criar', entidade='ativo', entidade_id=ativo.id,
        depois={
            'cliente_id': str(cliente_id), 'tipo': tipo, 'apelido': apelido,
            'status': status,
        },
    )
    db.session.commit()

    return jsonify({"id": str(ativo.id)}), 201


@clientes_bp.route('/ativos/<uuid:ativo_id>/interfaces', methods=['POST'])
@requer_clientes('clientes.editar')
def criar_ativo_interface(ativo_id):
    ativo = Ativo.query.get(ativo_id)
    if ativo is None:
        return _erro("ativo_id não corresponde a nenhum ativo", 404)

    dados = request.get_json(silent=True) or {}
    atribuicao = dados.get('atribuicao')
    if atribuicao is not None and atribuicao not in ('fixo', 'dhcp', 'pppoe'):
        return _erro("atribuicao inválida — precisa ser um de ['fixo', 'dhcp', 'pppoe'] ou omitida", 400)

    interface = AtivoInterface(
        ativo_id=ativo.id,
        nome=dados.get('nome'),
        ip=dados.get('ip'),
        mascara=dados.get('mascara'),
        gateway=dados.get('gateway'),
        atribuicao=atribuicao,
        vlan=dados.get('vlan'),
        mac=dados.get('mac'),
    )
    db.session.add(interface)
    db.session.flush()

    identidade = g.identidade_clientes
    registrar_auditoria(
        identidade, acao='criar', entidade='ativo_interface', entidade_id=interface.id,
        depois={'ativo_id': str(ativo.id), 'nome': dados.get('nome')},
    )
    db.session.commit()

    return jsonify({"id": str(interface.id)}), 201


# ═══════════════════════════════════════════════════════════════════════════
# Credenciais (briefing 4.2, 3.1-3.5)
# ═══════════════════════════════════════════════════════════════════════════

@clientes_bp.route('/credenciais', methods=['POST'])
@requer_clientes('clientes.editar')
def criar_credencial():
    """
    Cifra o segredo com clientes.cifra.cifrar() antes de gravar — nunca
    chega a existir uma variável com o texto puro fora desta função (e da
    própria cifra.py). O corpo da requisição em si passa pela rede em
    HTTPS; o que fica em log de aplicação é só o resultado deste endpoint
    (id), nunca o `segredo` recebido.
    """
    dados = request.get_json(silent=True) or {}

    cliente_id_bruto = dados.get('cliente_id')
    escopo_tipo = dados.get('escopo_tipo')
    sensibilidade = dados.get('sensibilidade')
    rotulo = (dados.get('rotulo') or '').strip()
    segredo = dados.get('segredo')

    faltando = [
        nome for nome, valor in [
            ('cliente_id', cliente_id_bruto), ('escopo_tipo', escopo_tipo),
            ('sensibilidade', sensibilidade), ('rotulo', rotulo), ('segredo', segredo),
        ] if not valor
    ]
    if faltando:
        return _erro(f"campos obrigatórios ausentes: {', '.join(faltando)}", 400)

    try:
        cliente_id = uuid_lib.UUID(str(cliente_id_bruto))
    except ValueError:
        return _erro("cliente_id não é um UUID válido", 400)
    if Cliente.query.get(cliente_id) is None:
        return _erro("cliente_id não corresponde a nenhum cliente", 400)

    if escopo_tipo not in _ESCOPO_TIPOS_VALIDOS:
        return _erro(f"escopo_tipo inválido — precisa ser um de {sorted(_ESCOPO_TIPOS_VALIDOS)}", 400)
    if sensibilidade not in _SENSIBILIDADES_VALIDAS:
        return _erro(f"sensibilidade inválida — precisa ser um de {sorted(_SENSIBILIDADES_VALIDAS)}", 400)

    try:
        escopo_id = _uuid_ou_none(dados.get('escopo_id'))
    except ValueError:
        return _erro("escopo_id não é um UUID válido", 400)

    if escopo_tipo == 'ativo':
        if escopo_id is None:
            return _erro("escopo_id é obrigatório quando escopo_tipo='ativo'", 400)
        if Ativo.query.get(escopo_id) is None:
            return _erro("escopo_id não corresponde a nenhum ativo", 400)
    elif escopo_tipo == 'sistema' and escopo_id is None:
        # cliente_sistema (briefing 4.3) ainda não existe — sem como
        # validar a referência. Aceito sem checagem, lacuna documentada
        # em clientes/models.py (docstring de Credencial).
        return _erro("escopo_id é obrigatório quando escopo_tipo='sistema'", 400)

    try:
        segredo_cifrado, nonce, chave_versao = cifrar(str(segredo))
    except ChaveInvalidaError as e:
        # Erro de configuração (APP_ENCRYPTION_KEY ausente/errada), não do
        # cliente da API — 500 é o status certo aqui, não 400.
        return _erro(f"não foi possível cifrar: {e}", 500)

    credencial = Credencial(
        cliente_id=cliente_id,
        escopo_tipo=escopo_tipo,
        escopo_id=escopo_id,
        sensibilidade=sensibilidade,
        rotulo=rotulo,
        usuario=dados.get('usuario'),
        url=dados.get('url'),
        segredo_cifrado=segredo_cifrado,
        nonce=nonce,
        chave_versao=chave_versao,
        mfa_observacao=dados.get('mfa_observacao'),
        rotacionada_em=dados.get('rotacionada_em'),
    )
    db.session.add(credencial)
    db.session.flush()

    identidade = g.identidade_clientes
    registrar_auditoria(
        identidade, acao='criar', entidade='credencial', entidade_id=credencial.id,
        # segredo_cifrado nunca é o valor real aqui — só um marcador que
        # registrar_auditoria()/redigir() troca por '[redigido]' (briefing
        # 3.4). O valor do marcador não importa, só a chave.
        depois={
            'cliente_id': str(cliente_id), 'escopo_tipo': escopo_tipo,
            'sensibilidade': sensibilidade, 'rotulo': rotulo,
            'segredo_cifrado': True,
        },
    )
    db.session.commit()

    return jsonify({"id": str(credencial.id)}), 201


@clientes_bp.route('/<uuid:cliente_id>/credenciais', methods=['GET'])
@requer_clientes('clientes.credencial.operacional.revelar')
def listar_credenciais(cliente_id):
    """
    Metadados só — nunca segredo_cifrado, nunca nonce, nunca chave_versao
    (briefing 3.2, decisão 9). `bloqueada` é calculada para o papel de
    quem pediu: True quando a credencial é sensibilidade='administrativa'
    e a identidade não tem clientes.credencial.admin.revelar — §6.6:
    aparece como existente e bloqueada, não some. Exige a capacidade
    "fraca" (operacional) pra sequer listar; quem não tem nem isso não
    lista nada, nem os rótulos.
    """
    identidade = g.identidade_clientes
    tem_admin = tem_capacidade(identidade['capacidades'], 'clientes.credencial.admin.revelar')

    credenciais = Credencial.query.filter_by(cliente_id=cliente_id).all()
    resultado = []
    for c in credenciais:
        bloqueada = c.sensibilidade == 'administrativa' and not tem_admin
        resultado.append({
            "id": str(c.id),
            "rotulo": c.rotulo,
            "usuario": c.usuario,
            "url": c.url,
            "sensibilidade": c.sensibilidade,
            "rotacionada_em": c.rotacionada_em.isoformat() if c.rotacionada_em else None,
            "bloqueada": bloqueada,
        })
    return jsonify(resultado)


@clientes_bp.route('/credenciais/<uuid:credencial_id>/revelar', methods=['POST'])
@requer_clientes('clientes.credencial.operacional.revelar')
def revelar_credencial(credencial_id):
    """
    Endpoint dedicado de revelação (briefing 3.2) — um segredo por vez,
    nunca em listagem. POST, não GET: motivo no corpo de um GET é
    estranho, e a URL de um GET pode acabar em log de acesso, histórico
    do navegador e proxy — um POST evita os três.

    ATENÇÃO — checagem em duas etapas, e a segunda não é opcional
    (decisão 8, 31/08/2026): o decorator acima só exige a capacidade
    "fraca" (clientes.credencial.operacional.revelar, que os três papéis
    têm) porque a capacidade de verdade depende da sensibilidade da
    credencial ENCONTRADA — um dado de runtime que o decorator, estático,
    não pode conhecer. A checagem de clientes.credencial.admin.revelar
    logo abaixo é o portão de verdade para credencial sensibilidade=
    'administrativa'; sem ela, esta rota deixaria qualquer papel revelar
    qualquer coisa. QUALQUER rota futura que leia segredo_cifrado precisa
    repetir este padrão de duas checagens — a garantia estrutural da
    Decisão 3 (decorator único) não cobre sozinha uma capacidade que só
    se decide depois de ler o dado.

    Enumeração: "não existe" e "existe mas você não pode" devolvem
    exatamente o mesmo 403 aqui, de propósito — não é descuido, e não é
    404 para o primeiro caso. É o oposto do §6.6 (que quer o N1 sabendo
    que a credencial administrativa existe, via a LISTAGEM, não via este
    endpoint) — aqui, ID que não existe e ID que existe sem permissão não
    podem ser diferenciáveis por quem pediu.
    """
    identidade = g.identidade_clientes

    dados = request.get_json(silent=True) or {}
    motivo = (dados.get('motivo') or '').strip()
    if not motivo:
        # Validação pura, antes de qualquer consulta — não revela nada
        # sobre a credencial (nem tentamos buscá-la ainda), então não é
        # um evento de segredo_acesso_log.
        return _erro("motivo é obrigatório", 400)

    credencial = Credencial.query.get(credencial_id)
    if credencial is None:
        registrar_acesso_segredo(identidade, credencial_id, motivo, 'negado')
        db.session.commit()
        return _erro("Acesso não autorizado", 403)

    if credencial.sensibilidade == 'administrativa' and not tem_capacidade(
        identidade['capacidades'], 'clientes.credencial.admin.revelar'
    ):
        registrar_acesso_segredo(identidade, credencial.id, motivo, 'negado')
        db.session.commit()
        return _erro("Acesso não autorizado", 403)

    try:
        texto_claro = decifrar(credencial.segredo_cifrado, credencial.nonce, credencial.chave_versao)
    except ChaveInvalidaError as e:
        # Erro de configuração — não é negação de acesso, não vai para
        # segredo_acesso_log como 'negado' (a pessoa TINHA permissão; o
        # sistema é que não conseguiu decifrar). 500, log de aplicação.
        return _erro(f"não foi possível decifrar: {e}", 500)

    # Concedido: grava ANTES de devolver o valor (briefing 3.2), na mesma
    # transação — se o commit falhar, a resposta também falha, nunca
    # devolve segredo sem log.
    registrar_acesso_segredo(identidade, credencial.id, motivo, 'concedido')
    db.session.commit()

    return jsonify({"segredo": texto_claro})
