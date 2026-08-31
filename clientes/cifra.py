"""
clientes/cifra.py
AES-256-GCM para o campo credencial.segredo_cifrado (briefing 3.1).

Formato da chave (confirmado por Altair, 31/08/2026): APP_ENCRYPTION_KEY no
ambiente é base64 de 32 bytes aleatórios — 44 caracteres, terminando em
'=' — gerada com:
    python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
Decodificar com base64.b64decode(..., validate=True) antes de passar para
o AESGCM (que exige a chave em bytes crus, 32 deles — AES-256).

Nonce: 12 bytes (96 bits, o tamanho recomendado pra GCM — não 32),
gerado com os.urandom() a cada chamada de cifrar(), único por registro,
guardado numa coluna própria (credencial.nonce), NUNCA concatenado ao
ciphertext — decisão do Altair: mais fácil de auditar, não depende de
convenção de offset que alguém esquece.

chave_versao: hoje só existe uma APP_ENCRYPTION_KEY (versão 1, constante
CHAVE_VERSAO_ATUAL abaixo). Rotação de chave não é automática — é um
processo futuro que decifra cada credencial com a chave antiga e recifra
com a nova, atualizando credencial.chave_versao linha a linha. Este módulo
não implementa rotação, só grava a versão usada em cada cifragem (decisão
do Altair, mesmo espírito do campo `ciclo` em cliente_documento: barato
agora, sem ele é problema insolúvel quando a chave precisar girar).
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHAVE_VERSAO_ATUAL = 1
_TAMANHO_CHAVE_BYTES = 32   # AES-256
_TAMANHO_NONCE_BYTES = 12   # recomendado para GCM — não confundir com o
                            # tamanho da chave, os dois são 32/12, não iguais

_chave_cache: bytes | None = None


class ChaveInvalidaError(RuntimeError):
    """
    APP_ENCRYPTION_KEY ausente ou não decodifica em exatamente 32 bytes.
    Levantada cedo e alto de propósito — cifrar ou decifrar com uma chave
    do tamanho errado não é um erro que se quer descobrir depois, com
    dado já gravado.
    """


def _carregar_chave() -> bytes:
    global _chave_cache
    if _chave_cache is not None:
        return _chave_cache

    valor = os.environ.get('APP_ENCRYPTION_KEY', '')
    if not valor:
        raise ChaveInvalidaError(
            "APP_ENCRYPTION_KEY não está definida no ambiente. Confirmada "
            "por Altair como já presente no painel do Render — se este "
            "erro aparecer lá, é configuração perdida, não configuração "
            "nova a fazer."
        )
    try:
        chave = base64.b64decode(valor, validate=True)
    except Exception as e:
        raise ChaveInvalidaError(
            f"APP_ENCRYPTION_KEY não decodifica como base64 válido: {e}"
        ) from e

    if len(chave) != _TAMANHO_CHAVE_BYTES:
        raise ChaveInvalidaError(
            f"APP_ENCRYPTION_KEY decodifica para {len(chave)} bytes, "
            f"esperado {_TAMANHO_CHAVE_BYTES} (AES-256). Não cifra nem "
            f"decifra nada com o tamanho errado — pare e confira o valor "
            f"no painel do Render antes de tentar de novo."
        )

    _chave_cache = chave
    return _chave_cache


def cifrar(texto_claro: str) -> tuple[bytes, bytes, int]:
    """
    Cifra texto_claro. Retorna (ciphertext, nonce, chave_versao) — os três
    valores que credencial.segredo_cifrado, credencial.nonce e
    credencial.chave_versao guardam, cada um na própria coluna.
    """
    chave = _carregar_chave()
    nonce = os.urandom(_TAMANHO_NONCE_BYTES)
    aesgcm = AESGCM(chave)
    ciphertext = aesgcm.encrypt(nonce, texto_claro.encode('utf-8'), None)
    return ciphertext, nonce, CHAVE_VERSAO_ATUAL


def decifrar(ciphertext: bytes, nonce: bytes, chave_versao: int) -> str:
    """
    Decifra um segredo gravado. chave_versao é recebido e validado, não
    ignorado — hoje só a versão 1 existe; uma versão diferente aqui
    significa que a chave girou e este módulo ainda não sabe decifrar
    versões antigas (rotação, quando existir, decide isso explicitamente,
    não por acidente).
    """
    if chave_versao != CHAVE_VERSAO_ATUAL:
        raise ChaveInvalidaError(
            f"credencial cifrada com chave_versao={chave_versao}, mas "
            f"este módulo só decifra a versão atual ({CHAVE_VERSAO_ATUAL}) "
            f"— rotação de chave ainda não está implementada."
        )
    chave = _carregar_chave()
    aesgcm = AESGCM(chave)
    texto_claro = aesgcm.decrypt(nonce, ciphertext, None)
    return texto_claro.decode('utf-8')
