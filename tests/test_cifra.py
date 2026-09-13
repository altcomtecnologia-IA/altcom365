"""
tests/test_cifra.py
Testes unitários de clientes/cifra.py — AES-256-GCM (briefing 3.1). Sem
Flask, sem banco: só a primitiva de cifra/decifra, isolada.

APP_ENCRYPTION_KEY vem de tests/conftest.py (setdefault, uma vez por
sessão de testes) — os testes de formato de chave abaixo usam
importlib.reload() pra forçar clientes.cifra a reler o ambiente a cada
caso, já que o módulo cacheia a chave decodificada em memória
(_chave_cache) depois do primeiro uso — sem reload, o segundo teste veria
a chave já cacheada do primeiro, não o valor que acabou de ser trocado.
"""
import base64
import importlib
import os
import secrets
import uuid

import pytest
from cryptography.exceptions import InvalidTag

import clientes.cifra as cifra


@pytest.fixture(autouse=True)
def _reset_cache():
    """Cada teste começa com o cache de chave zerado, não herdado do
    teste anterior — ver docstring do módulo."""
    importlib.reload(cifra)
    yield
    importlib.reload(cifra)   # deixa limpo pro próximo arquivo de teste também


def _aad():
    """AAD de teste — em produção é sempre credencial.id.bytes (ver
    docstring de clientes/cifra.py); aqui só precisa ser 16 bytes
    estáveis por chamada, um uuid4().bytes serve."""
    return uuid.uuid4().bytes


class TestRoundTrip:

    def test_cifra_decifra_volta_ao_texto_original(self):
        aad = _aad()
        ct, nonce, versao = cifra.cifrar("senha-super-secreta-123", aad)
        assert cifra.decifrar(ct, nonce, versao, aad) == "senha-super-secreta-123"

    def test_versao_retornada_e_a_atual(self):
        _, _, versao = cifra.cifrar("x", _aad())
        assert versao == cifra.CHAVE_VERSAO_ATUAL

    def test_nonce_tem_12_bytes(self):
        _, nonce, _ = cifra.cifrar("x", _aad())
        assert len(nonce) == 12

    def test_nonce_unico_a_cada_chamada(self):
        """GCM com nonce repetido sob a mesma chave é catastrófico — nunca
        pode repetir, nem para o mesmo texto."""
        _, nonce1, _ = cifra.cifrar("mesmo texto", _aad())
        _, nonce2, _ = cifra.cifrar("mesmo texto", _aad())
        assert nonce1 != nonce2

    def test_ciphertext_muda_mesmo_com_texto_igual(self):
        ct1, _, _ = cifra.cifrar("mesmo texto", _aad())
        ct2, _, _ = cifra.cifrar("mesmo texto", _aad())
        assert ct1 != ct2   # nonce diferente garante isso

    def test_nonce_trocado_nao_decifra(self):
        """Prova que GCM detecta nonce errado (autenticação, não só
        confidencialidade) — não silenciosamente devolve lixo."""
        aad = _aad()
        ct, _, versao = cifra.cifrar("x", aad)
        _, outro_nonce, _ = cifra.cifrar("y", aad)
        with pytest.raises(Exception):
            cifra.decifrar(ct, outro_nonce, versao, aad)

    def test_chave_versao_diferente_de_1_recusa(self):
        aad = _aad()
        ct, nonce, _ = cifra.cifrar("x", aad)
        with pytest.raises(cifra.ChaveInvalidaError):
            cifra.decifrar(ct, nonce, 2, aad)

    def test_texto_com_unicode(self):
        original = "sênh@ cöm acentuação e emoji 🔒"
        aad = _aad()
        ct, nonce, versao = cifra.cifrar(original, aad)
        assert cifra.decifrar(ct, nonce, versao, aad) == original


class TestAAD:
    """
    Correção pós-entrega da Fase 2, 13/09/2026 — ver docstring do módulo:
    aad amarra o ciphertext à linha exata (credencial.id em produção), não
    só à chave. Sem isso, um ciphertext+nonce movidos de uma credencial
    para outra decifrariam normalmente, devolvendo o segredo errado no
    contexto errado.
    """

    def test_aad_trocado_nao_decifra(self):
        ct, nonce, versao = cifra.cifrar("segredo", _aad())
        with pytest.raises(InvalidTag):
            cifra.decifrar(ct, nonce, versao, _aad())   # aad diferente do usado em cifrar()

    def test_aad_correto_decifra(self):
        """Prova que o teste acima falha pelo aad errado, não por algo
        mais — o mesmo aad de volta funciona normalmente."""
        aad = _aad()
        ct, nonce, versao = cifra.cifrar("segredo", aad)
        assert cifra.decifrar(ct, nonce, versao, aad) == "segredo"


class TestFormatoDaChave:

    def test_chave_ausente_recusa(self, monkeypatch):
        monkeypatch.delenv('APP_ENCRYPTION_KEY', raising=False)
        importlib.reload(cifra)
        with pytest.raises(cifra.ChaveInvalidaError, match="não está definida"):
            cifra.cifrar("x", _aad())

    def test_chave_tamanho_errado_recusa(self, monkeypatch):
        chave_16_bytes = base64.b64encode(secrets.token_bytes(16)).decode()
        monkeypatch.setenv('APP_ENCRYPTION_KEY', chave_16_bytes)
        importlib.reload(cifra)
        with pytest.raises(cifra.ChaveInvalidaError, match="16 bytes"):
            cifra.cifrar("x", _aad())

    def test_chave_base64_invalido_recusa(self, monkeypatch):
        monkeypatch.setenv('APP_ENCRYPTION_KEY', 'isto nao e base64 valido!!!')
        importlib.reload(cifra)
        with pytest.raises(cifra.ChaveInvalidaError, match="base64"):
            cifra.cifrar("x", _aad())

    def test_chave_no_formato_documentado_funciona(self, monkeypatch):
        """
        Exatamente o comando que Altair confirmou ter usado para gerar a
        chave real: base64 de 32 bytes aleatórios, 44 caracteres,
        terminando em '='.
        """
        chave = base64.b64encode(secrets.token_bytes(32)).decode()
        assert len(chave) == 44
        assert chave.endswith('=')
        monkeypatch.setenv('APP_ENCRYPTION_KEY', chave)
        importlib.reload(cifra)
        aad = _aad()
        ct, nonce, versao = cifra.cifrar("funciona", aad)
        assert cifra.decifrar(ct, nonce, versao, aad) == "funciona"
