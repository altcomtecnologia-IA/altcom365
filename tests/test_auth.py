"""
tests/test_auth.py
Testes unitários do módulo altcom_auth usando tokens sintéticos.
O app Flask NÃO é iniciado nem conectado aqui — módulo totalmente isolado.
"""
import os, sys, time, unittest
from unittest.mock import patch
from pathlib import Path

# Garantir que o pacote altcom_auth é encontrado
sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import jwt as pyjwt

# ── Gerar par de chaves RSA sintético (uma vez por sessão de testes) ─────────
_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_KID = "test-kid-001"
_AUD = "test-aud-abc123"
_ISS = "https://altcom.cloudflareaccess.com"
_TEAM = "altcom.cloudflareaccess.com"

# Env vars para os testes
os.environ['CF_ACCESS_TEAM_DOMAIN'] = _TEAM
os.environ['CF_ACCESS_AUD']         = _AUD
os.environ['AUTH_ENABLED']          = 'true'

from altcom_auth import jwt as auth_jwt
from altcom_auth import identidade as auth_id
from altcom_auth.identidade import GRUPOS, tem_capacidade


def _fazer_token(email="altair@altcom.com.br", aud=None, iss=None,
                 exp_delta=3600, kid=_KID, omitir_email=False):
    """Gera um JWT sintético assinado com a chave privada de teste."""
    now = int(time.time())
    payload = {
        "aud": [aud or _AUD],
        "iss": iss or _ISS,
        "iat": now,
        "exp": now + exp_delta,
    }
    if not omitir_email:
        payload["email"] = email
    return pyjwt.encode(
        payload,
        _PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _mock_certs(kid=_KID, public_key=None):
    """Retorna um dict de certs sintético para usar com patch."""
    return {kid: public_key or _PUBLIC_KEY}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Testes de validação de JWT
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidarToken(unittest.TestCase):

    def test_token_valido_retorna_email(self):
        """Token válido e bem formado → retorna e-mail normalizado."""
        token = _fazer_token(email="Altair@Altcom.com.br")
        with patch.object(auth_jwt, '_get_certs', return_value=_mock_certs()):
            email = auth_jwt.validar_token(token)
        self.assertEqual(email, "altair@altcom.com.br")

    def test_token_ausente_nega(self):
        """Token vazio → ValueError."""
        with self.assertRaises(ValueError) as ctx:
            auth_jwt.validar_token("")
        self.assertIn("ausente", str(ctx.exception).lower())

    def test_token_expirado_nega(self):
        """Token com exp no passado → ValueError 'expirado'."""
        token = _fazer_token(exp_delta=-10)
        with patch.object(auth_jwt, '_get_certs', return_value=_mock_certs()):
            with self.assertRaises(ValueError) as ctx:
                auth_jwt.validar_token(token)
        self.assertIn("expirado", str(ctx.exception).lower())

    def test_aud_errado_nega(self):
        """Token com aud diferente do esperado → ValueError."""
        token = _fazer_token(aud="aud-errado-xyz")
        with patch.object(auth_jwt, '_get_certs', return_value=_mock_certs()):
            with self.assertRaises(ValueError) as ctx:
                auth_jwt.validar_token(token)
        self.assertIn("aud", str(ctx.exception).lower())

    def test_iss_errado_nega(self):
        """Token com iss de outro tenant → ValueError."""
        token = _fazer_token(iss="https://outro-tenant.cloudflareaccess.com")
        with patch.object(auth_jwt, '_get_certs', return_value=_mock_certs()):
            with self.assertRaises(ValueError) as ctx:
                auth_jwt.validar_token(token)
        self.assertIn("iss", str(ctx.exception).lower())

    def test_kid_desconhecido_nega(self):
        """kid não presente nas chaves → ValueError (mesmo após refresh)."""
        token = _fazer_token(kid="kid-inexistente")
        # Cache e refresh retornam chaves sem esse kid
        with patch.object(auth_jwt, '_get_certs', return_value=_mock_certs()):
            with self.assertRaises(ValueError) as ctx:
                auth_jwt.validar_token(token)
        self.assertIn("kid", str(ctx.exception).lower())

    def test_assinatura_invalida_nega(self):
        """Token assinado com chave diferente → ValueError."""
        outra_chave = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        token = pyjwt.encode(
            {"aud": [_AUD], "iss": _ISS, "iat": int(time.time()),
             "exp": int(time.time()) + 3600, "email": "x@x.com"},
            outra_chave, algorithm="RS256", headers={"kid": _KID},
        )
        with patch.object(auth_jwt, '_get_certs', return_value=_mock_certs()):
            with self.assertRaises(ValueError) as ctx:
                auth_jwt.validar_token(token)
        self.assertIn("assinatura", str(ctx.exception).lower())

    def test_email_ausente_no_payload_nega(self):
        """Payload sem claim 'email' → ValueError."""
        token = _fazer_token(omitir_email=True)
        with patch.object(auth_jwt, '_get_certs', return_value=_mock_certs()):
            with self.assertRaises(ValueError) as ctx:
                auth_jwt.validar_token(token)
        self.assertIn("email", str(ctx.exception).lower())

    def test_endpoint_offline_nega(self):
        """Se endpoint de certs está fora e cache vazio → negar (nunca liberar)."""
        auth_jwt._CERT_CACHE = {}
        auth_jwt._CACHE_TS = 0.0
        with patch.object(auth_jwt, '_fetch_certs', side_effect=Exception("timeout")):
            with self.assertRaises(Exception):
                auth_jwt.validar_token(_fazer_token())


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Testes de identidade e capacidades
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdentidade(unittest.TestCase):

    def setUp(self):
        """Injeta usuários de teste diretamente, sem depender do arquivo yaml."""
        auth_id._USUARIOS = {
            "acorrea@altcom.com.br":    {"nome": "Altair",     "grupo": "tecnico_gestao"},
            "analista@altcom.com.br":   {"nome": "Analista",   "grupo": "tecnico_analistas"},
            "admin@altcom.com.br":      {"nome": "Financeiro", "grupo": "administrativo"},
        }
        auth_id._CARREGADO = True

    def test_usuario_conhecido_retorna_identidade(self):
        ident = auth_id.obter_identidade("acorrea@altcom.com.br")
        self.assertEqual(ident['grupo'], "tecnico_gestao")
        self.assertIn("capacidades", ident)

    def test_email_desconhecido_nega(self):
        with self.assertRaises(ValueError):
            auth_id.obter_identidade("desconhecido@altcom.com.br")

    def test_email_case_insensitive(self):
        ident = auth_id.obter_identidade("ACORREA@ALTCOM.COM.BR")
        self.assertEqual(ident['grupo'], "tecnico_gestao")

    # ── tecnico_gestao: acesso total ──────────────────────────────────────────
    def test_gestao_tem_todas_capacidades(self):
        ident = auth_id.obter_identidade("acorrea@altcom.com.br")
        for cap in ["laudo:ler", "laudo:criar", "laudo:excluir",
                    "laudo:exportar", "admin:usuarios", "comercial:ler"]:
            self.assertTrue(tem_capacidade(ident, cap), f"gestão deve ter {cap}")

    # ── tecnico_analistas: bloqueios ─────────────────────────────────────────
    def test_analistas_nao_podem_excluir_laudo(self):
        ident = auth_id.obter_identidade("analista@altcom.com.br")
        self.assertFalse(tem_capacidade(ident, "laudo:excluir"))

    def test_analistas_nao_veem_comercial(self):
        ident = auth_id.obter_identidade("analista@altcom.com.br")
        self.assertFalse(tem_capacidade(ident, "comercial:ler"))

    def test_analistas_podem_criar_laudo(self):
        ident = auth_id.obter_identidade("analista@altcom.com.br")
        self.assertTrue(tem_capacidade(ident, "laudo:criar"))

    # ── administrativo: bloqueios ─────────────────────────────────────────────
    def test_admin_nao_pode_criar_laudo(self):
        ident = auth_id.obter_identidade("admin@altcom.com.br")
        self.assertFalse(tem_capacidade(ident, "laudo:criar"))

    def test_admin_pode_exportar(self):
        ident = auth_id.obter_identidade("admin@altcom.com.br")
        self.assertTrue(tem_capacidade(ident, "laudo:exportar"))

    def test_admin_pode_ver_comercial(self):
        ident = auth_id.obter_identidade("admin@altcom.com.br")
        self.assertTrue(tem_capacidade(ident, "comercial:ler"))

    def test_admin_nao_gerencia_usuarios(self):
        ident = auth_id.obter_identidade("admin@altcom.com.br")
        self.assertFalse(tem_capacidade(ident, "admin:usuarios"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
