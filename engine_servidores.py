"""
Altcom 365 – Engine de Classificação de Eficiência de Servidores v1
Fonte: Tabela de Dispositivos (Milvus) — entidade Servidor

Dois eixos de avaliação:
  Eixo 1 (tier base): Geração do processador → determina classificação e cor
  Eixo 2 (sufixo):    Estado operacional em tempo real → determina urgência

Sufixos (não alteram a cor — indicam ação necessária):
  SO EOL (2008/2012)       → " - EOL"         (prioridade máxima)
  CPU > 80% ou RAM > 85%  → " - Sobrecarga"
  Armazenamento > 70%     → " - Man. Prev."

Regra absoluta: o token NUNCA aparece no código fonte.
Nada desta engine é commitado direto na main — tudo passa pela v2-api.
"""
import re
import pandas as pd

# ══════════════════════════════════════════════════════════════════════════════
# CORES E LABELS
# ══════════════════════════════════════════════════════════════════════════════

BADGE_COLORS = {
    "CRÍTICO":      ("C00000", "FFFFFF"),
    "SATISFATÓRIO": ("FFC000", "000000"),
    "BOM":          ("92D050", "000000"),
    "ÓTIMO":        ("00B050", "FFFFFF"),
    "EXCELENTE":    ("00B0F0", "000000"),
}

TIER_LABELS = {
    0: "CRÍTICO",
    1: "SATISFATÓRIO",
    2: "BOM",
    3: "ÓTIMO",
    4: "EXCELENTE",
}

DURABILIDADE = {
    0: "Substituição imediata",
    1: "2026/2027",
    2: "2027/2028",
    3: "2029/2030",
    4: "2030+",
}

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_ram(val) -> int:
    if pd.isna(val):
        return 0
    m = re.search(r'(\d+)', str(val))
    return int(m.group(1)) if m else 0


def _parse_storage(val) -> float:
    if pd.isna(val):
        return 0.0
    v = str(val).replace(' GB', '').replace(',', '.').strip()
    try:
        return float(v)
    except ValueError:
        return 0.0


def _parse_uso(val):
    s = str(val)
    if pd.isna(val) or s.lower() in ('nan%', 'nan', ''):
        return None
    try:
        return float(s.replace('%', '').replace(',', '.').strip())
    except ValueError:
        return None


def _parse_ram_uso_pct(ram_util_val, ram_total_val) -> float:
    """
    Retorna uso de RAM em % (0-100).
    Suporta dois formatos do Milvus:
      - Percentual string (ex: '20,04%' ou '84.87%') → usa direto
      - MB bruto (ex: 3849)                           → calcula sobre RAM total em GB→MB
    """
    s = str(ram_util_val).strip()
    if not s or s.lower() in ('nan', 'não possui', ''):
        return 0.0
    try:
        if '%' in s:
            return float(s.replace('%', '').replace(',', '.').strip())
        ram_util_mb = float(s.replace(',', '.'))
        m = re.search(r'([\d,\.]+)', str(ram_total_val or ''))
        if not m:
            return 0.0
        ram_total_gb = float(m.group(1).replace(',', '.'))
        if ram_total_gb <= 0:
            return 0.0
        return min((ram_util_mb / (ram_total_gb * 1024)) * 100, 100.0)
    except Exception:
        return 0.0


def _is_vm(row) -> bool:
    val = str(row.get('Máquina virtual', '')).lower().strip()
    return val in ('sim', 'yes', 'true', '1', 'verdadeiro')


def _so_eol(so: str) -> bool:
    """SO fora de suporte estendido da Microsoft."""
    s = so.lower()
    return any(x in s for x in [
        'windows server 2000',
        'windows server 2003',
        'windows server 2008',
        'windows server 2012',
    ])


def _so_legado(so: str) -> bool:
    """SO ainda suportado mas em fase de envelhecimento."""
    return 'windows server 2016' in so.lower()


def _sem_antivirus(row) -> bool:
    av = str(row.get('Antivírus', '')).strip().lower()
    return av in ('', 'nan', 'nenhum', 'none', 'não instalado')


# ══════════════════════════════════════════════════════════════════════════════
# CPU PARSE — servidores
# ══════════════════════════════════════════════════════════════════════════════

def parse_cpu_servidor(proc: str) -> tuple:
    """
    Retorna (familia: str, tier_base: int)

    Mapeamento de geração → tier:
      0 = CRÍTICO       Xeon E5/E3 v1/v2 (≤2013), Xeon X série, desconhecido
      1 = SATISFATÓRIO  Xeon E5/E3 v3/v4 (2014-2016), consumer grade em servidor
      2 = BOM           Xeon Scalable 1ª gen Skylake (2017-2019), EPYC Naples
      3 = ÓTIMO         Xeon Scalable 2ª gen Cascade Lake (2020-2021), EPYC Rome
      4 = EXCELENTE     Xeon Scalable 3ª gen+ Ice Lake/Sapphire Rapids (2021+), EPYC Milan+
    """
    p = str(proc).lower()

    # ── AMD EPYC ──────────────────────────────────────────────────────────────
    # Naples (7001 series, 2017) = BOM
    if re.search(r'epyc\s+7[01]\d{2}', p):
        return 'epyc_naples', 2

    # Rome (7002 series, 2019) = ÓTIMO
    if re.search(r'epyc\s+7[23]\d{2}', p):
        return 'epyc_rome', 3

    # Milan (7003 series, 2021+) e Genoa (9004+) = EXCELENTE
    if re.search(r'epyc\s+7[4-9]\d{2}|epyc\s+9\d{3}', p):
        return 'epyc_milan_plus', 4

    # ── Intel Xeon Scalable (2017+) ───────────────────────────────────────────
    # Identificação pelo número de série: 4 dígitos após Silver/Gold/Platinum
    # 1ª gen Skylake-SP: 41xx, 51xx, 61xx, 81xx
    if re.search(r'xeon.+(?:silver|gold|platinum)\s+[4568]1\d{2}', p):
        return 'xeon_scalable_1g', 2

    # 2ª gen Cascade Lake: 42xx, 52xx, 62xx, 82xx
    if re.search(r'xeon.+(?:silver|gold|platinum)\s+[4568]2\d{2}', p):
        return 'xeon_scalable_2g', 3

    # 3ª gen Ice Lake e posteriores (Sapphire Rapids): 43xx+, 53xx+, 63xx+, 83xx+
    if re.search(r'xeon.+(?:silver|gold|platinum)\s+[4568][3-9]\d{2}', p):
        return 'xeon_scalable_3g', 4

    # ── Intel Xeon E-series com versão explícita ──────────────────────────────
    # v3/v4 (Haswell/Broadwell 2014-2016) = SATISFATÓRIO
    if re.search(r'xeon.+e[3-7]-\d{4}\s*v[34]', p):
        return 'xeon_e_v3v4', 1

    # v1/v2 (Sandy/Ivy Bridge ≤2013) ou sem versão = CRÍTICO
    if re.search(r'xeon.+e[3-7]-\d{4}', p):
        return 'xeon_e_v1v2', 0

    # ── Xeon X / W / antigo sem E-number (Nehalem, Westmere, etc.) ───────────
    if re.search(r'xeon\s+[xw]\d{4}', p):
        return 'xeon_old', 0

    # ── CPU consumer grade usada em servidor (i5/i7/i9) ──────────────────────
    # Equipamento workstation funcionando como servidor — SATISFATÓRIO conservador
    if re.search(r'i[3579]-\d{4,5}', p):
        return 'consumer_grade', 1

    # ── Xeon detectado mas modelo não mapeado ─────────────────────────────────
    if 'xeon' in p:
        return 'xeon_unknown', 0

    return 'unknown', 0


# ══════════════════════════════════════════════════════════════════════════════
# CLASSIFY SERVIDOR
# ══════════════════════════════════════════════════════════════════════════════

def classify_servidor(row) -> pd.Series:
    """
    Classifica um servidor físico ou VM.
    Espera um pd.Series (linha do DataFrame Milvus) com os campos:
      Processador, Memória RAM total, Memória RAM utilizada,
      CPU utilizada, Armazenamento interno total, Armazenamento interno utilizado,
      Sistema operacional, Máquina virtual, Antivírus, Temperatura da CPU,
      Núcleos do processador, Localização, Data de compra, Data de garantia.
    """
    proc      = str(row.get('Processador', ''))
    cpu_uso   = _parse_uso(row.get('CPU utilizada', None))
    st_total  = _parse_storage(row.get('Armazenamento interno total', 0))
    st_usado  = _parse_storage(row.get('Armazenamento interno utilizado', 0))
    so        = str(row.get('Sistema operacional', ''))
    vm        = _is_vm(row)

    familia, tier = parse_cpu_servidor(proc)

    # Percentuais de utilização
    ram_pct = _parse_ram_uso_pct(
        row.get('Memória RAM utilizada', 0),
        row.get('Memória RAM total', 0)
    )
    st_pct  = (st_usado  / st_total  * 100) if st_total  > 0 else 0

    # Flags de SO
    eol    = _so_eol(so)
    legado = _so_legado(so)

    classif_base = TIER_LABELS[tier]

    # ── CRÍTICO: retorna imediatamente ────────────────────────────────────────
    if tier == 0:
        desc_critico = "Hardware fora do ciclo de suporte do fabricante."
        if eol:
            desc_critico += f" SO fora de suporte estendido ({so}) — risco crítico de segurança."
        return pd.Series({
            'Classificação':         'CRÍTICO',
            'Badge':                 'CRÍTICO - EOL' if eol else 'CRÍTICO',
            'Descritivo':            desc_critico,
            'Durabilidade estimada': DURABILIDADE[0],
            'É VM':                  'Sim' if vm else 'Não',
            'Sugestão':              'Substituição de hardware e migração de SO urgentes',
        })

    # ── Determinação do sufixo (prioridade: EOL > Sobrecarga > Man. Prev.) ───
    sobrecarga = (
        (cpu_uso is not None and cpu_uso > 80) or
        ram_pct > 85
    )
    manut_prev = st_pct > 70

    if eol:
        sufixo = ' - EOL'
    elif sobrecarga:
        sufixo = ' - Sobrecarga'
    elif manut_prev:
        sufixo = ' - Man. Prev.'
    else:
        sufixo = ''

    badge = classif_base + sufixo

    # ── Descritivos e sugestões ───────────────────────────────────────────────
    desc = []
    sugs = []

    tipo_str = "Máquina virtual (VM)" if vm else "Servidor físico"
    desc.append(f"{tipo_str}.")

    if eol:
        desc.append(
            f"Sistema operacional fora de suporte estendido ({so}) — "
            "risco de segurança crítico e ausência de patches."
        )
        sugs.append("Migração urgente para Windows Server 2022")

    elif legado:
        desc.append(f"SO em fase de envelhecimento ({so}) — planejar migração.")
        sugs.append("Planejar migração para Windows Server 2022")

    if cpu_uso is not None and cpu_uso > 80:
        desc.append(
            f"CPU com utilização elevada ({cpu_uso:.0f}%) — "
            "avaliar distribuição de carga ou upgrade de hardware."
        )
        sugs.append("Redistribuição de workloads ou upgrade de processador")
    elif cpu_uso is not None and cpu_uso > 60:
        desc.append(f"CPU com utilização moderada-alta ({cpu_uso:.0f}%) — monitorar.")

    if ram_pct > 85:
        desc.append(
            f"Memória RAM com utilização elevada ({ram_pct:.0f}%) — "
            "risco de instabilidade e degradação de performance."
        )
        sugs.append("Expansão de memória RAM")
    elif ram_pct > 70:
        desc.append(f"Memória RAM com utilização moderada-alta ({ram_pct:.0f}%) — monitorar.")

    if manut_prev and not sobrecarga:
        desc.append(
            f"Armazenamento com {st_pct:.0f}% de uso — "
            "manutenção preventiva recomendada."
        )
        sugs.append("Limpeza ou expansão de armazenamento")

    if _sem_antivirus(row):
        desc.append("Antivírus não detectado — verificar proteção endpoint.")
        sugs.append("Verificar/instalar solução de proteção endpoint")

    # Descrição padrão se não houver penalidades
    if len(desc) == 1:  # apenas o tipo_str
        desc.append("Servidor operando dentro dos parâmetros esperados.")

    return pd.Series({
        'Classificação':         classif_base,
        'Badge':                 badge,
        'Descritivo':            ' '.join(desc),
        'Durabilidade estimada': DURABILIDADE[tier],
        'É VM':                  'Sim' if vm else 'Não',
        'Sugestão':              '\n'.join(sugs) if sugs else 'NA',
    })
