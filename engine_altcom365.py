"""
Altcom 365 – Engine de Classificação de Eficiência Técnica v5
Fonte: Tabela_de_processadores_para_laudo_de_eficiencia_2026.xlsx (revisada)
Regras de sufixo:
  - Uso armazenamento >70%    → classif + " - Man. Prev." (cor inalterada)
  - SO<Win11 / RAM<8 / SSD<200 → classif + " - Upgrade"   (cor inalterada)
  - Ambos                     → " - Upgrade" tem prioridade
"""
import re, pandas as pd

# ══════════════════════════════════════════════════════════════════════════════
# CORES OFICIAIS (extraídas da planilha)
# ══════════════════════════════════════════════════════════════════════════════
BADGE_COLORS = {
    "CRÍTICO":      ("C00000", "FFFFFF"),   # vermelho escuro / texto branco
    "SATISFATÓRIO": ("FFC000", "000000"),   # amarelo / texto preto
    "BOM":          ("92D050", "000000"),   # verde claro / texto preto
    "ÓTIMO":        ("00B050", "FFFFFF"),   # verde escuro / texto branco
    "EXCELENTE":    ("00B0F0", "000000"),   # azul / texto preto
}

# ══════════════════════════════════════════════════════════════════════════════
# TABELA BASE: (device_type, familia, gen) -> tier (0-4)
# Fonte: tabela revisada maio/2026
# ══════════════════════════════════════════════════════════════════════════════
TIER_TABLE = {
    # ── NOTEBOOK i3 ──────────────────────────────────────────────────────────
    ('notebook','i3', 7): 0, ('notebook','i3', 8): 0, ('notebook','i3', 9): 0,
    ('notebook','i3',10): 1,
    ('notebook','i3',11): 1,   # não listado → conservador = SATISFATÓRIO
    ('notebook','i3',12): 2, ('notebook','i3',13): 2,

    # ── NOTEBOOK i5 ──────────────────────────────────────────────────────────
    ('notebook','i5', 7): 0,
    ('notebook','i5', 8): 1, ('notebook','i5', 9): 1,
    ('notebook','i5',10): 2,
    ('notebook','i5',11): 2,   # REVISADO: era ÓTIMO, agora BOM (tabela mai/26)
    ('notebook','i5',12): 3,   # REVISADO: era BOM, agora ÓTIMO
    ('notebook','i5',13): 4,

    # ── NOTEBOOK i7 ──────────────────────────────────────────────────────────
    ('notebook','i7', 6): 0,
    ('notebook','i7', 7): 1,
    ('notebook','i7', 8): 2,
    ('notebook','i7', 9): 3,   # 9750H = ÓTIMO
    ('notebook','i7',10): 2,   # 10xxx U = BOM
    ('notebook','i7',11): 4,
    ('notebook','i7',12): 4, ('notebook','i7',13): 4,

    # ── DESKTOP i3 ───────────────────────────────────────────────────────────
    ('desktop','i3', 7): 0,
    ('desktop','i3', 8): 1, ('desktop','i3', 9): 1,
    ('desktop','i3',10): 2, ('desktop','i3',11): 2,
    ('desktop','i3',12): 3,
    ('desktop','i3',13): 4,

    # ── DESKTOP i5 ───────────────────────────────────────────────────────────
    ('desktop','i5', 6): 0,
    ('desktop','i5', 7): 1, ('desktop','i5', 8): 1, ('desktop','i5', 9): 1,
    ('desktop','i5',10): 2,
    ('desktop','i5',11): 3,
    ('desktop','i5',12): 4, ('desktop','i5',13): 4,

    # ── DESKTOP i7 ───────────────────────────────────────────────────────────
    ('desktop','i7', 6): 0,
    ('desktop','i7', 7): 1,
    ('desktop','i7', 8): 2,
    ('desktop','i7', 9): 3, ('desktop','i7',10): 3,
    ('desktop','i7',11): 4, ('desktop','i7',12): 4, ('desktop','i7',13): 4,
}

TIER_LABELS  = {0:"CRÍTICO", 1:"SATISFATÓRIO", 2:"BOM", 3:"ÓTIMO", 4:"EXCELENTE"}
DURABILIDADE = {0:"Troca", 1:"2026/2027", 2:"2027/2028", 3:"2028/2029", 4:"2029/2030"}

PRECO_SUBST   = "Usado configuração mínima +/- R$ 1.700,00 - Novo R$ 2.900,00"
PRECO_WIN11   = "R$ 145,00"
PRECO_RAM_8   = "R$ 120,00 (módulo 8 GB)"
PRECO_SSD_240 = "R$ 180,00 (SSD 240 GB)"

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def parse_ram(val) -> int:
    if pd.isna(val): return 0
    m = re.search(r'(\d+)', str(val))
    return int(m.group(1)) if m else 0

def parse_storage(val) -> float:
    if pd.isna(val): return 0.0
    v = str(val).replace(' GB','').replace(',','.').strip()
    try: return float(v)
    except: return 0.0

def parse_uso(val):
    s = str(val)
    if pd.isna(val) or s.lower() in ['nan%','nan','']: return None
    try: return float(s.replace('%','').strip())
    except: return None

def is_win_old(so: str) -> bool:
    return any(x in str(so).lower() for x in
               ['windows 10','windows 7','windows 8','windows xp'])

def device_type(tipo: str) -> str:
    t = str(tipo).lower()
    return 'desktop' if ('terminal' in t or 'desktop' in t) else 'notebook'

def parse_cpu(proc: str) -> tuple:
    """Retorna (familia, gen, suffix)"""
    p = str(proc).lower()

    # AMD Ryzen: "Ryzen 5 5625U"
    rm = re.search(r'ryzen\s+(\d)\s+(\d)\d{3}', p)
    if rm:
        return f'ryzen{rm.group(1)}', int(rm.group(2)), ''

    # Intel N-series (low-power)
    if re.search(r'i3-n\d+|core.*\bn\d{3,4}\b', p):
        return 'n-series', 0, ''

    # Gen tag explícita: "11th Gen ... i5-1135G7"
    gm = re.search(r'(\d+)(?:th|nd|rd|st)\s+gen.*?i([3579])-(\d{3,5})([a-z]*)', p)
    if gm:
        return f'i{gm.group(2)}', int(gm.group(1)), gm.group(4)

    # Sem gen tag: "i5-10210U", "i7-10750H"
    mm = re.search(r'i([3579])-(\d{4,5})([a-z]*)', p)
    if mm:
        model = mm.group(2)
        if len(model) >= 5:
            gen = int(model[:2])
        else:
            first2 = int(model[:2])
            gen = first2 if first2 in range(10, 15) else int(model[0])
        return f'i{mm.group(1)}', gen, mm.group(3)

    return 'unknown', 0, ''

def base_tier(dev: str, proc: str) -> int:
    familia, gen, _ = parse_cpu(proc)

    # AMD Ryzen 5000+ = ÓTIMO; outros = BOM
    if familia.startswith('ryzen'):
        return 3 if gen >= 5 else 2

    # Intel N-series = CRÍTICO
    if familia == 'n-series': return 0

    if familia == 'unknown' or gen == 0: return 0

    key = (dev, familia, gen)
    if key in TIER_TABLE: return TIER_TABLE[key]
    if gen <= 5: return 0
    if gen <= 7: return 1
    return 2

# ══════════════════════════════════════════════════════════════════════════════
# CLASSIFY
# ══════════════════════════════════════════════════════════════════════════════
def classify(row) -> pd.Series:
    proc    = str(row['Processador'])
    ram     = parse_ram(row['Memória RAM total'])
    storage = parse_storage(row['Armazenamento total'])
    uso     = parse_uso(row['Armazenamento utilizado'])
    so      = str(row['Sistema operacional'])
    tipo    = device_type(str(row.get('Tipo de dispositivo', 'notebook')))
    win_old = is_win_old(so)
    familia, gen, suffix = parse_cpu(proc)

    tier_base_val = base_tier(tipo, proc)
    tier = tier_base_val

    # ── Boost por sufixo de alta performance (H em notebook, K em desktop) ──
    # Eleva 1 nível (SATISFATÓRIO->BOM, BOM->ÓTIMO, ÓTIMO->EXCELENTE)
    # EXCELENTE por boost ainda requer RAM >= 16 GB
    _, _, cpu_suffix = parse_cpu(proc)
    is_boost = (
        (tipo == 'notebook' and cpu_suffix.startswith('h')) or
        (tipo == 'desktop'  and cpu_suffix.startswith('k'))
    )
    if is_boost and 1 <= tier <= 3:
        tier += 1  # eleva 1 nível

    tier_after_boost = tier  # salva para lógica de promoção RAM abaixo

    # ── CRÍTICO: retorna imediatamente, sem sufixo ────────────────────────────
    if tier == 0:
        return pd.Series({
            'Classificação':         "CRÍTICO",
            'Badge':                 "CRÍTICO",
            'Descritivo':            "Equipamento incompatível com a carga de trabalho da empresa.",
            'Durabilidade estimada': "Troca",
            'Sugestão':              "",
            'Preços':                PRECO_SUBST,
        })

    # ── Requisito EXCELENTE: 16GB + SSD>=480 → se não atingir, rebaixa tier ─
    if tier == 4 and (ram < 16 or storage < 480):
        tier = 3   # rebaixa para ÓTIMO (permanente — não promove de volta)

    # ── Promoção ÓTIMO→EXCELENTE por RAM: só se tier BASE era 3 ─────────────
    if tier == 3 and tier_base_val == 3 and not is_boost and ram >= 16:
        tier = 4

    # ── Classificação base final (tier não muda mais após aqui) ──────────────
    classif_base = TIER_LABELS[tier]

    # ── Penalidades: detectar situações que geram sufixo ─────────────────────
    # Uso >70%    → "- Man. Prev."
    # SO/RAM/SSD  → "- Upgrade"
    # Upgrade tem prioridade sobre Man. Prev.
    uso_alto = uso is not None and uso > 70

    pen_upgrade = []
    if win_old:       pen_upgrade.append(('so',  "Necessário fazer upgrade para Windows 11."))
    if ram < 8:       pen_upgrade.append(('ram', "Memória RAM abaixo do mínimo recomendado (8 GB)."))
    if storage < 200: pen_upgrade.append(('ssd', "Armazenamento abaixo do mínimo recomendado (220 GB)."))

    if pen_upgrade:
        sufixo = " - Upgrade"
    elif uso_alto:
        sufixo = " - Man. Prev."
    else:
        sufixo = ""

    badge = classif_base + sufixo

    # ── Monta descritivo e sugestões ─────────────────────────────────────────
    desc  = []
    sugs  = []
    precs = []

    if tier == 4:
        desc.append("Configuração de alto desempenho, totalmente alinhada ao padrão corporativo.")
    elif tier == 1:
        desc.append("Configuração funcional dentro dos parâmetros mínimos.")
    else:
        desc.append("Configuração adequada.")

    # Textos de penalidade de upgrade
    for tipo_p, texto_p in pen_upgrade:
        desc.append(texto_p)
        if tipo_p == 'so':
            sugs.append("Upgrade Windows 11 Pro"); precs.append(PRECO_WIN11)
        elif tipo_p == 'ram':
            sugs.append("Upgrade RAM para 8 GB"); precs.append(PRECO_RAM_8)
        elif tipo_p == 'ssd':
            sugs.append("Substituição/adição SSD 240 GB"); precs.append(PRECO_SSD_240)

    # Texto de uso alto
    if uso_alto:
        if uso is not None and uso >= 85:
            desc.append("Armazenamento em nível crítico — limpeza urgente necessária.")
            sugs.append("Limpeza de armazenamento urgente")
        else:
            desc.append("Manutenção preventiva de armazenamento recomendada.")
            sugs.append("Preventiva de armazenamento")

    return pd.Series({
        'Classificação':         classif_base,   # para lookup de cor
        'Badge':                 badge,           # texto exibido na célula
        'Descritivo':            " ".join(desc),
        'Durabilidade estimada': DURABILIDADE[tier],
        'Sugestão':              "\n".join(sugs) if sugs else "NA",
        'Preços':                " | ".join(precs) if precs else "NA",
    })

