"""
alertas_internos.py
Altcom 365 v2 — lógica dos 4 alertas operacionais e versão de referência do agente Milvus.

DATA DE ATUALIZAÇÃO é coluna opcional — quando ausente, alertas de Sem Contato e
Agente Milvus ficam em branco sem interromper o processamento.
"""
import pandas as pd

# ── Helpers de parse ──────────────────────────────────────────────────────────

def _parse_gb(val):
    """'237,53 GB' → 237.53  |  'Não possui' → None"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in ('não possui', 'nan', 'none', ''):
        return None
    s = s.replace(' GB', '').replace('GB', '').replace(',', '.').strip()
    try:
        return float(s)
    except ValueError:
        return None


def _parse_data_at(val):
    """Converte 'dd/mm/aaaa hh:mm' ou objeto datetime para pd.Timestamp."""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if isinstance(val, pd.Timestamp):
        return val
    if hasattr(val, 'date'):
        return pd.Timestamp(val)
    s = str(val).strip()
    if not s or s.lower() in ('nan', 'nat', 'não possui', ''):
        return None
    for fmt in ('%d/%m/%Y %H:%M', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return pd.to_datetime(s, format=fmt)
        except Exception:
            pass
    try:
        return pd.to_datetime(s, dayfirst=True)
    except Exception:
        return None


def _ver_tuple(v):
    """'110.0.0.4' → (110, 0, 0, 4) para comparação semântica."""
    try:
        return tuple(int(x) for x in str(v).strip().split('.'))
    except Exception:
        return (0,)


# ── Versão de referência do agente Milvus ────────────────────────────────────

def calcular_versao_referencia(df):
    """
    Retorna (versao_ref, n_desatualizadas, pct_desatualizadas).
    Usa a versão mais comum entre máquinas com contato nos últimos 7 dias.
    Calculado sobre o parque inteiro pós-filtros (não por cliente).
    Requer DATA DE ATUALIZAÇÃO e VERSÃO DO CLIENT; retorna (None, 0, 0.0) se ausentes.
    """
    col_data   = 'DATA DE ATUALIZAÇÃO'
    col_versao = 'VERSÃO DO CLIENT'

    if col_data not in df.columns or col_versao not in df.columns:
        return None, 0, 0.0

    datas = df[col_data].apply(_parse_data_at)
    hoje  = pd.Timestamp.today()
    dias  = datas.apply(lambda dt: (hoje - dt).days if dt is not None else None)

    recentes_mask = dias.apply(lambda d: d is not None and d <= 7)
    recentes      = df[recentes_mask]
    if len(recentes) == 0:
        recentes = df

    versoes = (
        recentes[col_versao]
        .astype(str).str.strip()
        .replace({'nan': None, 'Não possui': None, '': None})
        .dropna()
    )
    if len(versoes) == 0:
        return None, 0, 0.0

    versao_ref = versoes.mode().iloc[0]

    todas = df[col_versao].astype(str).str.strip()
    n_desatualizadas = int(
        todas.apply(
            lambda v: v not in ('', 'nan', 'Não possui') and _ver_tuple(v) < _ver_tuple(versao_ref)
        ).sum()
    )
    pct = round(n_desatualizadas / len(df) * 100, 1) if len(df) > 0 else 0.0
    return versao_ref, n_desatualizadas, pct


# ── Cálculo dos 4 alertas ─────────────────────────────────────────────────────

def calcular_alertas(df, versao_ref=None):
    """
    Adiciona colunas de alerta ao df (retorna cópia):
      _uso_pct, _alerta_armazenamento, _alerta_windows,
      _alerta_sem_contato, _alerta_milvus, _tem_alerta

    Funciona mesmo quando DATA DE ATUALIZAÇÃO está ausente.
    """
    df   = df.copy()
    hoje = pd.Timestamp.today()

    # ── 1. Uso de armazenamento (> 70%) ──────────────────────────────────────
    def _uso_pct(row):
        total = _parse_gb(row.get('ARMAZENAMENTO INTERNO TOTAL'))
        if not total or total <= 0:
            return None
        util = _parse_gb(row.get('ARMAZENAMENTO INTERNO UTILIZADO'))
        if util is not None:
            return util / total * 100
        disp = _parse_gb(row.get('ARMAZENAMENTO INTERNO DISPONÍVEL'))
        if disp is not None:
            return (total - disp) / total * 100
        return None

    df['_uso_pct'] = df.apply(_uso_pct, axis=1)
    df['_alerta_armazenamento'] = df['_uso_pct'].apply(
        lambda u: f"Preventiva — uso {u:.1f}%" if (u is not None and u > 70) else ""
    )

    # ── 2. Windows desatualizado ──────────────────────────────────────────────
    def _win_old(so):
        s = str(so).lower()
        return any(x in s for x in
                   ['windows 10', 'windows 8', 'windows 7', 'windows xp'])

    df['_alerta_windows'] = df['SISTEMA OPERACIONAL'].apply(
        lambda so: "Upgrade para Win 11" if _win_old(so) else ""
    )

    # ── 3. Sem contato > 20 dias (opcional) ──────────────────────────────────
    if 'DATA DE ATUALIZAÇÃO' in df.columns:
        datas = df['DATA DE ATUALIZAÇÃO'].apply(_parse_data_at)
        dias  = datas.apply(lambda dt: (hoje - dt).days if dt is not None else None)
        df['_alerta_sem_contato'] = dias.apply(
            lambda d: f"{int(d)} dias sem contato — validar com cliente"
            if (d is not None and d > 20) else ""
        )
    else:
        df['_alerta_sem_contato'] = ""

    # ── 4. Agente Milvus desatualizado (opcional) ─────────────────────────────
    if versao_ref and 'VERSÃO DO CLIENT' in df.columns:
        def _milvus_alerta(v):
            s = str(v).strip()
            if s in ('', 'nan', 'Não possui'):
                return ""
            # Só alerta se a versão atual for MAIS ANTIGA que a referência
            return f"Desatualizada ({s}) — atualizar" if _ver_tuple(s) < _ver_tuple(str(versao_ref)) else ""
        df['_alerta_milvus'] = df['VERSÃO DO CLIENT'].apply(_milvus_alerta)
    else:
        df['_alerta_milvus'] = ""

    # ── Flag geral ────────────────────────────────────────────────────────────
    df['_tem_alerta'] = (
        (df['_alerta_armazenamento'].str.len() > 0) |
        (df['_alerta_windows'].str.len()       > 0) |
        (df['_alerta_sem_contato'].str.len()   > 0) |
        (df['_alerta_milvus'].str.len()        > 0)
    )
    return df


# ── Resumo para preview ───────────────────────────────────────────────────────

def resumo_alertas(df_com_alertas, versao_ref=None,
                   n_desatualizadas=0, pct_desatualizadas=0.0):
    """
    Retorna dict com contadores para o preview do frontend.
    df_com_alertas deve ter passado por calcular_alertas() e ter as colunas
    _alerta_* e _uso_pct, além das colunas esperadas pelo engine.
    """
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from engine_altcom365 import classify, BADGE_COLORS

    total = len(df_com_alertas)
    if total == 0:
        return {}

    results        = df_com_alertas.apply(classify, axis=1)
    classif_series = results['Classificação']

    order  = ["EXCELENTE", "ÓTIMO", "BOM", "SATISFATÓRIO", "CRÍTICO"]
    resumo = []
    for cat in order:
        qtd = int((classif_series == cat).sum())
        if qtd == 0:
            continue
        mask_cat = classif_series == cat
        mp = int(results.loc[mask_cat, 'Badge'].str.contains('Man. Prev.').sum())
        up = int(results.loc[mask_cat, 'Badge'].str.contains('Upgrade').sum())
        bg, fg = BADGE_COLORS[cat]
        resumo.append({
            'label': cat, 'qtd': qtd, 'pct': round(qtd / total * 100),
            'man_prev': mp, 'upgrade': up, 'bg': bg, 'fg': fg,
        })

    df_a = df_com_alertas
    mask_nao_critico = classif_series != 'CRÍTICO'

    n_armaz   = int(((df_a['_alerta_armazenamento'].str.len() > 0) & mask_nao_critico).sum())
    n_win     = int(((df_a['_alerta_windows'].str.len()       > 0) & mask_nao_critico).sum())
    n_contato = int(((df_a['_alerta_sem_contato'].str.len()   > 0) & mask_nao_critico).sum())
    n_milvus  = int(((df_a['_alerta_milvus'].str.len()        > 0) & mask_nao_critico).sum())
    n_troca   = int((df_a['_tem_alerta'] & (classif_series == 'CRÍTICO')).sum())

    milvus_badge = "yellow" if pct_desatualizadas > 10 else "blue"
    milvus_label = (
        "Solicitar push à Milvus"
        if pct_desatualizadas > 10
        else "Atualização interna pela equipe Altcom"
    )

    return {
        'total':  total,
        'resumo': resumo,
        'alertas': {
            'armazenamento':  n_armaz,
            'windows':        n_win,
            'sem_contato':    n_contato,
            'milvus':         n_milvus,
            'laudados_troca': n_troca,
            'tem_data_at':    'DATA DE ATUALIZAÇÃO' in df_a.columns,
            'tem_versao':     versao_ref is not None,
        },
        'milvus_info': {
            'versao_ref':       versao_ref,
            'n_desatualizadas': n_desatualizadas,
            'pct':              pct_desatualizadas,
            'badge':            milvus_badge,
            'label':            milvus_label,
        },
    }
