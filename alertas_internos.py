"""
alertas_internos.py
Altcom 365 v2 -- logica dos 4 alertas operacionais e versao de referencia do agente Milvus.

DATA DE ATUALIZACAO e coluna opcional -- quando ausente, alertas de Sem Contato e
Agente Milvus ficam em branco sem interromper o processamento.

V11: Nova regra de versao de referencia (semver):
  1. Filtrar máquinas com contato <= 7 dias
  2. Ordenar versões distintas por semver (tupla de inteiros)
  3. Referência = maior versão com >= 3 ocorrências
  4. Fallback: moda (regra V10)
"""
import pandas as pd

# -- Helpers de parse --

def _parse_gb(val):
    """'237,53 GB' -> 237.53  |  'Nao possui' -> None"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in ('nao possui', 'nan', 'none', ''):
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
    if not s or s.lower() in ('nan', 'nat', 'nao possui', ''):
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
    """
    '110.0.0.4' -> (110, 0, 0, 4) para comparacao semantica correta.
    NUNCA comparar versoes como string: '9.0.0.0' > '110.0.0.0' em string -- bug classico.
    """
    try:
        return tuple(int(x) for x in str(v).strip().split('.'))
    except Exception:
        return (0,)


# -- Versao de referencia do agente Milvus (V11: nova regra semver) -----------

def calcular_versao_referencia(df):
    """
    Retorna (versao_ref, n_desatualizadas, pct_desatualizadas).

    Nova regra V11 (semver):
      1. Filtrar máquinas com contato <= 7 dias (pós-filtros do app)
         Se nenhuma tiver data recente, usa todo o parque como base.
      2. Coletar versões válidas e contar ocorrências.
      3. Ordenar versões por SEMVER DESC (tupla de inteiros — evita bug de string).
      4. Referência = maior versão com >= 3 ocorrências (piso protege contra beta/teste).
      5. Fallback: se nenhuma versão atinge o piso, usa a moda (regra V10).

    Casos de teste esperados:
      - 111.0.0.0 em 200 máquinas recentes → ref = 111.0.0.0
      - 112.0.0.0 em 1, 111.0.0.0 em 200  → ref = 111.0.0.0 (112 não atinge piso)
      - 112.0.0.0 em 3, 111.0.0.0 em 200  → ref = 112.0.0.0
      - 9.0.0.0 vs 110.0.0.1              → 110.0.0.1 maior (anti-bug string)

    Calcula desatualizadas sobre o parque inteiro (não só recentes):
      n_desatualizadas = máquinas com versão DIFERENTE da referência.
    """
    col_data   = 'DATA DE ATUALIZAÇÃO'
    col_versao = 'VERSÃO DO CLIENT'

    if col_versao not in df.columns:
        return None, 0, 0.0

    def _versoes_validas(subset):
        return (
            subset[col_versao]
            .astype(str).str.strip()
            .replace({'nan': None, 'Não possui': None, 'nao possui': None, '': None})
            .dropna()
        )

    # --- Passo 1: base de máquinas recentes ─────────────────────────────────
    if col_data in df.columns:
        datas = df[col_data].apply(_parse_data_at)
        hoje  = pd.Timestamp.today()
        dias  = datas.apply(lambda dt: (hoje - dt).days if dt is not None else None)
        recentes_mask = dias.apply(lambda d: d is not None and d <= 7)
        recentes = df[recentes_mask]
        if len(recentes) == 0:
            recentes = df   # sem recentes -> usa tudo como fallback
        versoes_base = _versoes_validas(recentes)
    else:
        # Sem coluna de data -- usa todas as máquinas
        versoes_base = _versoes_validas(df)

    if len(versoes_base) == 0:
        return None, 0, 0.0

    # --- Passo 2: contagem de ocorrências ───────────────────────────────────
    contagem = versoes_base.value_counts()   # Series: versão -> count

    # --- Passo 3: versões com piso >= 3, ordenadas por semver DESC ──────────
    candidatas = contagem[contagem >= 3]

    if len(candidatas) > 0:
        # Ordenar por semver (maior primeiro)
        versao_ref = max(candidatas.index, key=_ver_tuple)
    else:
        # Fallback V10: moda simples
        versao_ref = contagem.index[0]   # já está ordenada por frequência DESC

    # --- Passo 4: contar desatualizadas no parque inteiro ───────────────────
    todas = df[col_versao].astype(str).str.strip()
    n_desatualizadas = int(
        todas.apply(
            lambda v: v not in ('', 'nan', 'Não possui', 'nao possui') and v != versao_ref
        ).sum()
    )
    pct = round(n_desatualizadas / len(df) * 100, 1) if len(df) > 0 else 0.0
    return versao_ref, n_desatualizadas, pct


# -- Calculo dos 4 alertas --

def calcular_alertas(df, versao_ref=None):
    """
    Adiciona colunas de alerta ao df (retorna copia):
      _uso_pct, _alerta_armazenamento, _alerta_windows,
      _alerta_sem_contato, _alerta_milvus, _tem_alerta

    Funciona mesmo quando DATA DE ATUALIZACAO esta ausente.
    """
    df   = df.copy()
    hoje = pd.Timestamp.today()

    # 1. Uso de armazenamento (> 70%)
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
        lambda u: f"Preventiva -- uso {u:.1f}%" if (u is not None and u > 70) else ""
    )

    # 2. Windows desatualizado — só máquinas com CPU compatível com Win11
    #    Critério Altcom: Intel i5 8ª gen+, i7 7ª gen+, i9 qualquer, AMD Ryzen qualquer
    def _win_old(so):
        s = str(so).lower()
        return any(x in s for x in
                   ['windows 10', 'windows 8', 'windows 7', 'windows xp'])

    def _cpu_qualifica_win11(proc_str):
        """True se o CPU suporta Win11 pelos critérios Altcom: i5 8ª+, i7 7ª+, i9, Ryzen,
        e novo naming Intel Core 5/7/3/9 e Core Ultra (12ª gen em diante)."""
        try:
            from engine_altcom365 import parse_cpu
            familia, gen, _ = parse_cpu(str(proc_str) if proc_str else '')
        except Exception:
            return False
        if familia.startswith('ryzen'):
            return True
        if familia == 'i9':
            return True
        if familia == 'i7' and gen >= 7:
            return True
        if familia == 'i5' and gen >= 8:
            return True
        # Novo naming Intel (sem "i"): Core 5/7/3/9 e Core Ultra — sempre modernos
        if familia.startswith('core'):
            return True
        return False

    def _alerta_win(row):
        so  = str(row.get('SISTEMA OPERACIONAL', ''))
        proc = str(row.get('PROCESSADOR', ''))
        if _win_old(so) and _cpu_qualifica_win11(proc):
            return "Upgrade para Win 11"
        return ""

    df['_alerta_windows'] = df.apply(_alerta_win, axis=1)

    # 3. Sem contato > 20 dias (opcional)
    if 'DATA DE ATUALIZAÇÃO' in df.columns:
        datas = df['DATA DE ATUALIZAÇÃO'].apply(_parse_data_at)
        dias  = datas.apply(lambda dt: (hoje - dt).days if dt is not None else None)
        df['_alerta_sem_contato'] = dias.apply(
            lambda d: f"{int(d)} dias sem contato -- validar com cliente"
            if (d is not None and d > 20) else ""
        )
    else:
        df['_alerta_sem_contato'] = ""

    # 4. Agente Milvus desatualizado (opcional)
    if versao_ref and 'VERSÃO DO CLIENT' in df.columns:
        def _milvus_alerta(v):
            s = str(v).strip()
            if s in ('', 'nan', 'Não possui'):
                return ""
            # Alerta para qualquer versao diferente da referencia (mais antiga OU mais nova)
            return f"Desatualizada ({s}) -- atualizar" if s != str(versao_ref) else ""
        df['_alerta_milvus'] = df['VERSÃO DO CLIENT'].apply(_milvus_alerta)
    else:
        df['_alerta_milvus'] = ""

    # Flag geral
    df['_tem_alerta'] = (
        (df['_alerta_armazenamento'].str.len() > 0) |
        (df['_alerta_windows'].str.len()       > 0) |
        (df['_alerta_sem_contato'].str.len()   > 0) |
        (df['_alerta_milvus'].str.len()        > 0)
    )
    return df


# -- Resumo para preview --

def resumo_alertas(df_com_alertas, versao_ref=None,
                   n_desatualizadas=0, pct_desatualizadas=0.0):
    """
    Retorna dict com contadores para o preview do frontend.
    df_com_alertas deve ter passado por calcular_alertas() e ter as colunas
    _alerta_* e _uso_pct, alem das colunas esperadas pelo engine.
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
