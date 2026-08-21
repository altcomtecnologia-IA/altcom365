"""
altcom_auth/jwt.py
Busca, cache e validação dos tokens JWT emitidos pelo Cloudflare Access.

Regras de segurança:
  - Chaves públicas buscadas de https://<team>.cloudflareaccess.com/cdn-cgi/access/certs
  - Cache em memória por worker com TTL de 1 hora
  - kid desconhecido → forçar refresh antes de negar
  - Endpoint offline → negar acesso (nunca liberar por fallback)
  - Nenhuma chave hardcoded; nenhum JWT completo nos logs
"""
import os, time, logging, urllib.request, json
import jwt as pyjwt
from cryptography.x509 import load_pem_x509_certificate

logger = logging.getLogger(__name__)

# Cache por worker: {kid: public_key_object}
_CERT_CACHE: dict = {}
_CACHE_TS: float = 0.0
_CACHE_TTL: int = 3600  # 1 hora


def _team_domain() -> str:
    return os.environ.get('CF_ACCESS_TEAM_DOMAIN', '').rstrip('/')


def _valid_auds() -> list:
    raw = os.environ.get('CF_ACCESS_AUD', '')
    return [a.strip() for a in raw.split(',') if a.strip()]


def _fetch_certs() -> dict:
    """
    Baixa as chaves públicas do Cloudflare Access.
    Lança exceção se o endpoint estiver indisponível — nunca retorna dict vazio.
    """
    domain = _team_domain()
    if not domain:
        raise ValueError("CF_ACCESS_TEAM_DOMAIN não configurado")
    url = f"https://{domain}/cdn-cgi/access/certs"
    req = urllib.request.Request(url, headers={'User-Agent': 'altcom_auth/1.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())

    certs = {}
    for entry in data.get('public_certs', []):
        kid = entry['kid']
        cert_pem = entry['cert'].encode()
        cert_obj = load_pem_x509_certificate(cert_pem)
        certs[kid] = cert_obj.public_key()

    if not certs:
        raise ValueError("Endpoint de certs retornou lista vazia")
    return certs


def _get_certs(force_refresh: bool = False) -> dict:
    """Retorna cache de chaves, recarregando se TTL expirou ou kid desconhecido."""
    global _CERT_CACHE, _CACHE_TS
    now = time.time()
    if force_refresh or not _CERT_CACHE or (now - _CACHE_TS) > _CACHE_TTL:
        # Se endpoint falhar e não há cache → exceção sobe e nega o acesso
        _CERT_CACHE = _fetch_certs()
        _CACHE_TS = now
        logger.info("Chaves CF Access recarregadas: %s", list(_CERT_CACHE.keys()))
    return _CERT_CACHE


def validar_token(token: str) -> str:
    """
    Valida o JWT do Cloudflare Access.
    Retorna o e-mail do usuário em caso de sucesso.
    Lança ValueError com motivo legível em qualquer falha.
    Nunca libera acesso se houver erro nos certs (endpoint offline = negar).
    """
    if not token:
        raise ValueError("Token ausente")

    auds = _valid_auds()
    if not auds:
        raise ValueError("CF_ACCESS_AUD não configurado")

    iss = f"https://{_team_domain()}"

    # Extrair kid do header sem validar assinatura
    try:
        header = pyjwt.get_unverified_header(token)
    except Exception as e:
        raise ValueError(f"Header JWT inválido: {e}")

    kid = header.get('kid')
    if not kid:
        raise ValueError("kid ausente no header JWT")

    # Tentar cache; se kid desconhecido, forçar refresh uma vez
    certs = _get_certs()
    if kid not in certs:
        certs = _get_certs(force_refresh=True)
    if kid not in certs:
        raise ValueError(f"kid '{kid}' não reconhecido")

    pub_key = certs[kid]

    # Validar assinatura, exp, iss e aud via PyJWT
    try:
        payload = pyjwt.decode(
            token,
            pub_key,
            algorithms=["RS256"],
            audience=auds,
            issuer=iss,
        )
    except pyjwt.ExpiredSignatureError:
        raise ValueError("Token expirado")
    except pyjwt.InvalidAudienceError:
        raise ValueError("aud inválido")
    except pyjwt.InvalidIssuerError:
        raise ValueError("iss inválido")
    except pyjwt.InvalidSignatureError:
        raise ValueError("Assinatura inválida")
    except Exception as e:
        raise ValueError(f"Erro na validação JWT: {e}")

    email = payload.get('email', '').strip().lower()
    if not email:
        raise ValueError("Claim 'email' ausente no token")

    return email
