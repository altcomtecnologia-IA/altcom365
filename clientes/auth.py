"""
clientes/auth.py
Autorização do módulo Clientes e Processos.

REGRA EXECUTÁVEL, NÃO CONVENÇÃO — este pacote (clientes/) nunca lê
g.identidade. tests/test_clientes_isolamento.py varre a AST do pacote
inteiro e falha se encontrar. Convenção escrita em comentário sobrevive uns
três meses; um teste que quebra o CI sobrevive.

Por quê: g.identidade é populado por altcom_auth/middleware.py, o hook
compartilhado com o Laudos, e hoje carrega um bug conhecido e deliberado —
_GRUPO_PADRAO='tecnico_gestao' para QUALQUER JWT válido, correção adiada de
propósito porque corrigi-la no middleware compartilhado tiraria acesso ao
Laudos de quem usa hoje (ver ISSUE_GRUPO_PADRAO.md, na raiz do repo). Ler
g.identidade aqui herdaria esse bug em silêncio — qualquer e-mail
autenticado ganharia acesso total ao módulo que guarda senha de firewall de
cliente. Por isso este arquivo:
  - nunca importa nada de altcom_auth além de `validar_token` (a
    verificação de assinatura RS256 em si — D1b: "pule o portão, não a
    validação");
  - extrai o token do request por conta própria, sem depender do que o
    middleware já fez;
  - resolve papel só pela tabela `usuario` (portal/models.py), com default
    fail-closed em QUALQUER caso que não seja "e-mail ativo com papel
    mapeado": e-mail ausente da tabela, ativo=false, ou papel presente no
    banco mas fora do mapa de capacidades abaixo (dado corrompido, papel
    novo criado direto no banco antes do código saber o que ele significa,
    migração futura). Nunca exceção não tratada, nunca fallback generoso.
  - ignora AUTH_ENABLED e AUTH_BYPASS_PATHS por completo — nem olha pra
    essas variáveis. O middleware compartilhado ainda roda antes desta
    checagem (é um before_request global), mas se ele deixar passar sem
    JWT (AUTH_ENABLED=false) ou por bypass de path, esta camada revalida
    do zero e nega mesmo assim.
"""
from functools import wraps

from flask import request, g, jsonify

from altcom_auth.jwt import validar_token
from portal.models import Usuario


# Capacidades por papel — briefing seção 2. N3 = N1/N2 + duas extras
# (herança de conjunto, não duas listas paralelas — seção 7). Só as
# capacidades já nomeadas no briefing; o resto da matriz da seção 7
# (ativos, sistemas, perfil modelo, log de acessos) entra quando a rota
# que precisar dela for construída, Fase 2+ — não antes, pra não fixar
# nome de capacidade pra funcionalidade que ainda pode mudar de forma.
CAPACIDADES_N1N2 = frozenset({
    'clientes.ler',
    'clientes.checklist.fechar',
})
CAPACIDADES_N3 = CAPACIDADES_N1N2 | frozenset({
    'clientes.credencial.admin.revelar',
    'clientes.editar',
})
CAPACIDADES_GESTOR = frozenset({'clientes.*'})

_CAPACIDADES_POR_PAPEL = {
    'n1n2': CAPACIDADES_N1N2,
    'n3': CAPACIDADES_N3,
    'gestor': CAPACIDADES_GESTOR,
}


def _capacidades_do_papel(papel):
    """
    Fail-closed. papel=None (sem linha em usuario, ou ativo=false — ver
    resolver_identidade) OU papel fora de _CAPACIDADES_POR_PAPEL (valor
    inesperado: dado corrompido, papel novo que o CHECK do banco já aceite
    mas este mapa ainda não conheça) → conjunto vazio. Nunca levanta,
    nunca cai num grupo "razoável" por engano.
    """
    if not papel:
        return frozenset()
    return _CAPACIDADES_POR_PAPEL.get(papel, frozenset())


def tem_capacidade(capacidades, capacidade):
    return 'clientes.*' in capacidades or capacidade in capacidades


def _extrair_token():
    # Mesma extração que altcom_auth/middleware.py faz — deliberadamente
    # reimplementada aqui, não importada de lá. Importar a função de
    # extração acoplaria este módulo ao middleware compartilhado por um
    # caminho que não é o JWT em si (D1b só pede reaproveitar a validação
    # de assinatura, não o antes-e-depois do middleware inteiro).
    return (request.headers.get('Cf-Access-Jwt-Assertion') or
            request.cookies.get('CF_Authorization', ''))


def resolver_identidade():
    """
    Valida o JWT e resolve papel/capacidades, do zero, sempre — ignora
    g.identidade, AUTH_ENABLED e AUTH_BYPASS_PATHS.

    Levanta ValueError (motivo legível, não exposto ao cliente) se o token
    for ausente/inválido. Não levanta por e-mail sem papel — isso é
    fail-closed (capacidades vazias), não erro: a checagem de capacidade
    subsequente cuida de negar o acesso.

    Retorna {"email", "papel", "capacidades"}.
    """
    token = _extrair_token()
    email = validar_token(token)  # ValueError: token ausente/expirado/inválido

    usuario = Usuario.query.filter_by(email=email, ativo=True).first()
    papel = usuario.papel if usuario is not None else None

    return {
        "email": email,
        "papel": papel,
        "capacidades": _capacidades_do_papel(papel),
    }


def requer_clientes(capacidade):
    """
    Decorator único de autorização do módulo Clientes — toda rota do
    blueprint usa este, sensível ou não (Decisão 3, 01/09/2026). Não existe
    uma variante "fraca" pra rotas comuns e uma "forte" só pra credencial:
    eliminar essa distinção elimina o risco de uma rota nova de Fase 2
    (revelação de credencial, por exemplo) acabar protegida pela variante
    errada por descuido.

    Em sucesso, deixa a identidade validada em g.identidade_clientes — não
    g.identidade — para a rota usar em audit_log/segredo_acesso_log (D1a:
    o log tem que nascer da identidade que ESTE decorator validou, nunca
    da que o middleware compartilhado possa ter deixado, ou não, em
    g.identidade).
    """
    def decorador(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                identidade = resolver_identidade()
            except ValueError:
                return jsonify({"erro": "Acesso não autorizado"}), 403

            if not tem_capacidade(identidade["capacidades"], capacidade):
                return jsonify({
                    "erro": "Permissão insuficiente",
                    "capacidade_requerida": capacidade,
                }), 403

            g.identidade_clientes = identidade
            return fn(*args, **kwargs)
        return wrapper
    return decorador
