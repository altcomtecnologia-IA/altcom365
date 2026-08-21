"""
altcom_auth/identidade.py
Mapeamento email → usuário → grupo → capacidades.

O Cloudflare autentica *quem*; este módulo decide *o quê*.
Grupos e capacidades definidos aqui, independente do IdP.
"""
import logging
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

# Matriz de capacidades por grupo.
# tecnico_gestao usa '*' = todas as capacidades, incluindo futuras.
GRUPOS: dict = {
    "administrativo": {
        "laudo:ler", "laudo:exportar",
        "cliente:ler", "cliente:gerenciar", "comercial:ler",
    },
    "tecnico_gestao": {"*"},
    "tecnico_analistas": {
        "laudo:ler", "laudo:criar", "laudo:editar",
        "laudo:exportar", "cliente:ler",
    },
}

# Cache em memória: {email: {"nome": str, "grupo": str}}
_USUARIOS: dict = {}
_CARREGADO: bool = False


def _caminho_yaml() -> Path:
    return Path(__file__).parent.parent / 'config' / 'usuarios.yaml'


def carregar_usuarios(caminho=None):
    """Carrega config/usuarios.yaml. Chamado uma vez por worker no startup."""
    global _USUARIOS, _CARREGADO
    p = Path(caminho) if caminho else _caminho_yaml()
    if not p.exists():
        logger.warning("config/usuarios.yaml não encontrado em %s", p)
        _USUARIOS = {}
        _CARREGADO = True
        return
    with open(p, 'r', encoding='utf-8') as f:
        dados = yaml.safe_load(f) or {}
    _USUARIOS = {
        u['email'].lower().strip(): {
            "nome":  u.get('nome', ''),
            "grupo": u.get('grupo', ''),
        }
        for u in dados.get('usuarios', [])
        if u.get('email')
    }
    _CARREGADO = True
    logger.info("Usuários carregados: %d", len(_USUARIOS))


def obter_identidade(email: str) -> dict:
    """
    Retorna {"email", "nome", "grupo", "capacidades"} para o e-mail autenticado.
    Lança ValueError se o e-mail não estiver no usuarios.yaml.
    E-mail autenticado no Cloudflare mas ausente aqui → 403.
    """
    if not _CARREGADO:
        carregar_usuarios()

    usuario = _USUARIOS.get(email.lower().strip())
    if usuario is None:
        raise ValueError(f"Usuário não autorizado")

    grupo = usuario['grupo']
    caps = GRUPOS.get(grupo, set())
    return {
        "email":       email.lower().strip(),
        "nome":        usuario['nome'],
        "grupo":       grupo,
        "capacidades": caps,
    }


def tem_capacidade(identidade: dict, capacidade: str) -> bool:
    """True se o usuário tem a capacidade requerida (ou tem '*')."""
    caps = identidade.get('capacidades', set())
    return '*' in caps or capacidade in caps
