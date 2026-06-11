"""
milvus_api.py — Camada de acesso à API Milvus
Responsabilidades:
  - listar_dispositivos() → DataFrame compatível com o formato Excel atual
  - Paginação automática (parque cabe em 1 chamada, mas preparado para mais)
  - Cache de 60 s para respeitar o rate limit de 1 req/min
  - Tratamento de todos os erros com mensagens amigáveis
  - Log de uso para auditoria interna
"""

import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────
API_BASE   = "https://apiintegracao.milvus.com.br"
TIMEOUT_S  = 30          # segundos por requisição
PAGE_SIZE  = 1000        # máximo suportado pela API
CACHE_TTL  = 60          # segundos — respeita o rate limit de 1 req/min

# Cache simples em memória
_cache: dict = {"ts": 0.0, "df": None}


def _token() -> str | None:
    """Lê o token da variável de ambiente. Nunca retorna segredos no código."""
    return os.environ.get("MILVUS_API_TOKEN") or None


def token_configurado() -> bool:
    """Retorna True se o token está disponível."""
    return bool(_token())


# ── Mapeamento de campos API → formato Excel ──────────────────────────────────
# Lado esquerdo: campo retornado pela API Milvus
# Lado direito : coluna que o engine_altcom365 espera
FIELD_MAP = {
    "hostname":                    "NOME DO DISPOSITIVO",
    "tipo_dispositivo_text":       "TIPO DO DISPOSITIVO",
    "sistema_operacional":         "SISTEMA OPERACIONAL",
    "processador":                 "PROCESSADOR",
    "total_processadores":         "NÚCLEOS DO PROCESSADOR",
    "ram_total":                   "MEMÓRIA RAM TOTAL",
    "armazenamento_total":         "ARMAZENAMENTO INTERNO TOTAL",
    "armazenamento_utilizado":     "ARMAZENAMENTO INTERNO UTILIZADO",
    "armazenamento_disponivel":    "ARMAZENAMENTO INTERNO DISPONÍVEL",
    "data_ultima_atualizacao":     "DATA DE ATUALIZAÇÃO",
    "versao_client":               "VERSÃO DO CLIENT",
    "apelido":                     "APELIDO",
    "usuario_logado":              "USUÁRIO LOGADO",
    "nome_fantasia":               "NOME FANTASIA DO CLIENTE",
    "is_servidor":                 "SERVIDOR",
    # is_ativo → EXCLUÍDO (lógica invertida: is_ativo=false → EXCLUÍDO=SIM)
}


def _mapear_dispositivo(raw: dict) -> dict:
    """Converte um objeto da API para o formato esperado pelo engine."""
    row = {}
    for api_field, excel_col in FIELD_MAP.items():
        row[excel_col] = raw.get(api_field, "")

    # is_ativo invertido para EXCLUÍDO
    is_ativo = raw.get("is_ativo", True)
    row["EXCLUÍDO"] = "NÃO" if is_ativo else "SIM"

    # SERVIDOR: converte boolean para SIM/NÃO
    is_srv = raw.get("is_servidor", False)
    row["SERVIDOR"] = "SIM" if is_srv else "NÃO"

    return row


def _buscar_pagina(token: str, pagina: int = 1) -> dict:
    """Faz uma chamada paginada ao endpoint de listagem."""
    url = f"{API_BASE}/api/dispositivos/listagem"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    body = {
        "is_paginate": True,
        "total_registros": PAGE_SIZE,
        "pagina": pagina,
    }
    resp = requests.post(url, json=body, headers=headers, timeout=TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def listar_dispositivos() -> tuple[pd.DataFrame | None, str | None]:
    """
    Retorna (DataFrame, None) em caso de sucesso
    ou      (None, mensagem_de_erro) em caso de falha.

    O DataFrame usa as colunas que o app.py e o engine_altcom365 já conhecem,
    idêntico ao que seria obtido via upload do Excel.
    """
    token = _token()
    if not token:
        return None, (
            "Credencial Milvus não configurada. "
            "Configure MILVUS_API_TOKEN no servidor."
        )

    # Cache válido?
    agora = time.time()
    if _cache["df"] is not None and (agora - _cache["ts"]) < CACHE_TTL:
        logger.info("milvus_api: usando cache (%.0f s restantes)",
                    CACHE_TTL - (agora - _cache["ts"]))
        return _cache["df"], None

    try:
        logger.info("milvus_api: chamando %s/api/dispositivos/listagem", API_BASE)
        inicio = time.time()

        # Primeira página
        data    = _buscar_pagina(token, pagina=1)
        meta    = data.get("meta", {}).get("paginate", {})
        total   = int(meta.get("total", 0))
        last_pg = int(meta.get("last_page", 1))
        lista   = data.get("lista", [])

        # Páginas adicionais (caso o parque cresça além de 1000)
        for pg in range(2, last_pg + 1):
            time.sleep(61)          # respeita o rate limit de 1 req/min
            extra = _buscar_pagina(token, pagina=pg)
            lista.extend(extra.get("lista", []))

        elapsed = time.time() - inicio
        logger.info(
            "milvus_api: %d dispositivos recebidos em %.1f s (esperado: %d)",
            len(lista), elapsed, total,
        )

        # Converte para DataFrame no formato Excel
        rows = [_mapear_dispositivo(d) for d in lista]
        df   = pd.DataFrame(rows)

        # Garante colunas mínimas (evita KeyError no engine)
        colunas_minimas = list(FIELD_MAP.values()) + ["EXCLUÍDO"]
        for col in colunas_minimas:
            if col not in df.columns:
                df[col] = ""

        # Atualiza cache
        _cache["ts"] = time.time()
        _cache["df"] = df

        # Log de auditoria
        logger.info(
            "milvus_api: sincronização concluída em %s — %d dispositivos",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(df),
        )

        return df, None

    except requests.exceptions.Timeout:
        msg = "Milvus não respondeu. Use o upload manual como alternativa."
        logger.error("milvus_api: timeout após %d s", TIMEOUT_S)
        return None, msg

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else 0
        if status == 401:
            msg = "Token Milvus rejeitado. Verifique se ainda é válido em portal.milvus.com.br"
        elif status == 429:
            msg = "Limite de chamadas Milvus atingido. Aguarde 1 minuto e tente novamente."
        elif status >= 500:
            msg = "Erro temporário no Milvus. Tente novamente em alguns minutos."
        else:
            msg = f"Erro HTTP {status} ao chamar a API Milvus."
        logger.error("milvus_api: HTTP %d — %s", status, msg)
        return None, msg

    except Exception as exc:  # noqa: BLE001
        msg = f"Erro inesperado ao sincronizar com Milvus: {exc}"
        logger.exception("milvus_api: erro inesperado")
        return None, msg
