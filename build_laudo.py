import os, sys, re
from datetime import datetime
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from engine_altcom365 import (classify, device_type, parse_uso, parse_storage,
                               parse_ram, BADGE_COLORS)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

NAVY, WHITE, ZEBRA, BORDER_C = "0D1B2A", "FFFFFF", "F5F7FA", "D0D5DD"
CYAN = "00C8D4"

# ── Frases que NAO aparecem no laudo do cliente ───────────────────────────────
_DESC_REMOVE = [
    "Necessário fazer upgrade para Windows 11.",
    "Manutenção preventiva de armazenamento recomendada.",
    "Armazenamento em nível crítico — limpeza urgente necessária.",
]
_SUG_REMOVE = {"Upgrade Windows 11 Pro", "Preventiva de armazenamento",
               "Limpeza de armazenamento urgente"}
_WIN_PRICE  = "R$ 145,00"

# ── Estilos ───────────────────────────────────────────────────────────────────
def brd():
    s = Side(style='thin', color=BORDER_C)
    return Border(left=s, right=s, top=s, bottom=s)

def badge_style(classif_base, badge_text):
    bg, fg = BADGE_COLORS.get(classif_base, ("FFFFFF", "000000"))
    return PatternFill("solid", fgColor=bg), Font(name='Arial', bold=True, size=8, color=fg)

def hdr(ws, r, c, v):
    cell = ws.cell(row=r, column=c, value=v)
    cell.fill = PatternFill("solid", fgColor=CYAN)
    cell.font = Font(name='Arial', bold=True, size=9, color=NAVY)
    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    cell.border = brd()

def dat(ws, r, c, v, z=False, ha='left', wrap=True, size=8):
    cell = ws.cell(row=r, column=c, value=v)
    cell.fill = PatternFill("solid", fgColor=ZEBRA if z else WHITE)
    cell.font = Font(name='Arial', size=size)
    cell.alignment = Alignment(horizontal=ha, vertical='center', wrap_text=wrap)
    cell.border = brd()

def title_strip(ws, row, text, span, h=34):
    ws.row_dimensions[row].height = h
    for col in range(1, span + 1):
        ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=NAVY)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    c = ws.cell(row=row, column=1, value=text)
    c.font = Font(name='Arial', bold=True, size=13, color=WHITE)
    c.alignment = Alignment(horizontal='left', vertical='center', indent=1)

# ── Limpeza para laudo do cliente ─────────────────────────────────────────────
def _clean_descritivo(desc):
    result = desc
    for phrase in _DESC_REMOVE:
        result = result.replace(phrase, '')
    result = re.sub(r'\s+', ' ', result).strip()
    return result or "Configuração adequada."

def _clean_sugestao(sug):
    if not sug or sug == "NA":
        return "NA"
    lines = [l.strip() for l in sug.split('\n') if l.strip() not in _SUG_REMOVE]
    return '\n'.join(lines) if lines else "NA"

def _clean_precos(prec):
    if not prec or prec == "NA":
        return "NA"
    parts = [p.strip() for p in prec.split(' | ') if p.strip() != _WIN_PRICE]
    return ' | '.join(parts) if parts else "NA"

def _badge_cliente(classif, orig_row):
    if classif == "CRÍTICO":
        return "CRÍTICO"
    ram     = parse_ram(orig_row.get('Memória RAM total', 0))
    storage = parse_storage(orig_row.get('Armazenamento total', 0))
    if ram < 8 or storage < 200:
        return classif + " - Upgrade"
    return classif

# ── Parser de Data de atualizacao ─────────────────────────────────────────────
def _parse_data_at(val):
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if isinstance(val, pd.Timestamp):
        return val
    if hasattr(val, 'date'):
        return pd.Timestamp(val)
    s = str(val).strip()
    if not s or s.lower() in ('nan', 'nat', ''):
        return None
    try:
        return pd.to_datetime(s, format='%d/%m/%Y %H:%M')
    except Exception:
        pass
    try:
        return pd.to_datetime(s, dayfirst=True)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════════════
# LAUDO DO CLIENTE (limpo)
# ════════════════════════════════════════════════════════════════════════════════
def build_laudo_cliente(input_path, output_path):
    df      = pd.read_excel(input_path)
    results = df.apply(classify, axis=1)
    df_out  = pd.concat([df, results], axis=1)

    cliente = str(df_out['Cliente'].iloc[0]) if 'Cliente' in df_out.columns else "Cliente"
    hoje    = pd.Timestamp.today().strftime('%d/%m/%y')
    total   = len(df_out)

    wb = Workbook()

    # ABA 1: LAUDO
    ws = wb.active; ws.title = "Laudo"
    NCOLS = 12
    title_strip(ws, 1, f"LAUDO DE EFICIENCIA TECNICA  |  {cliente.upper()}", NCOLS)
    ws.row_dimensions[2].height = 18
    ws.merge_cells(f'A2:{get_column_letter(NCOLS)}2')
    ws['A2'] = f"Metodologia Altcom 365 - Avaliacao do Parque de Estacoes de Trabalho - Data {hoje}"
    ws['A2'].font      = Font(name='Arial', size=9, color="888888")
    ws['A2'].alignment = Alignment(horizontal='left', vertical='center', indent=1)
    ws.row_dimensions[3].height = 5

    cols_hdr = ['Tipo','Dispositivo','S.O.','Processador','Nucleos','RAM',
                'Armazenamento','Uso %','Classificacao','Descritivo','Durabilidade','Sugestao']
    ws.row_dimensions[4].height = 22
    for ci, h in enumerate(cols_hdr, 1):
        hdr(ws, 4, ci, h)

    for ridx, row in df_out.iterrows():
        r  = ridx + 5
        ws.row_dimensions[r].height = 44
        z  = ridx % 2 == 0

        uso   = parse_uso(row['Armazenamento utilizado'])
        uso_s = f"{uso:.1f}%" if uso is not None else "N/D"
        st    = parse_storage(row['Armazenamento total'])
        st_s  = f"{st:.0f} GB SSD" if st > 0 else "N/D"
        tipo_label   = "Desktop" if device_type(str(row.get('Tipo de dispositivo', ''))) == 'desktop' else "Notebook"
        classif_base = row['Classificacao'] if 'Classificacao' in row.index else row.get('Classificação','')
        # Try both column names
        classif_base = str(row.get('Classificação', row.get('Classificacao','')))
        desc_raw  = str(row.get('Descritivo',''))
        sug_raw   = str(row.get('Sugestao', row.get('Sugestão','')))
        dur_raw   = str(row.get('Durabilidade estimada',''))

        desc_c  = _clean_descritivo(desc_raw)
        sug_c   = _clean_sugestao(sug_raw)
        badge_c = _badge_cliente(classif_base, row)

        vals_align = [
            (tipo_label,                    'center'),
            (row.get('Nome do dispositivo',''), 'left'),
            (row.get('Sistema operacional',''), 'left'),
            (row.get('Processador',''),         'left'),
            (row.get('Nucleos do processador', row.get('Núcleos do processador','')), 'center'),
            (row.get('Memoria RAM total', row.get('Memória RAM total','')), 'center'),
            (st_s,   'center'),
            (uso_s,  'center'),
            (badge_c,'center'),
            (desc_c, 'left'),
            (dur_raw,'center'),
            (sug_c,  'left'),
        ]
        for ci, (v, ha) in enumerate(vals_align, 1):
            if ci == 9:
                c = ws.cell(row=r, column=ci, value=v)
                fill, font = badge_style(classif_base, badge_c)
                c.fill = fill; c.font = font
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                c.border = brd()
            else:
                dat(ws, r, ci, v, z=z, ha=ha)

    for i, w in enumerate([10,20,26,38,8,8,14,7,20,50,12,30], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A5'

    # ABA 2: RESUMO
    ws2 = wb.create_sheet("Resumo Executivo")
    title_strip(ws2, 1, "RESUMO EXECUTIVO", 5)
    ws2.row_dimensions[2].height = 18
    ws2.merge_cells('A2:E2')
    ws2['A2'] = f"Total de dispositivos avaliados: {total} | Cliente: {cliente} | Data: {hoje}"
    ws2['A2'].font      = Font(name='Arial', size=10, color="444444")
    ws2['A2'].alignment = Alignment(indent=1)
    ws2.row_dimensions[3].height = 5
    for ci, h in enumerate(['Classificacao','Qtd','%','Durabilidade ref.','Status'], 1):
        hdr(ws2, 4, ci, h)
    ws2.row_dimensions[4].height = 22

    order   = ["EXCELENTE","OTIMO","BOM","SATISFATORIO","CRITICO"]
    dur_ref = {"EXCELENTE":"2029/2030","OTIMO":"2028/2029","BOM":"2027/2028",
               "SATISFATORIO":"2026/2027","CRITICO":"Troca"}
    status  = {"EXCELENTE":"Hardware ideal, alta longevidade",
               "OTIMO":"Hardware adequado, operacao estavel",
               "BOM":"Funcional, ajustes pontuais necessarios",
               "SATISFATORIO":"Limite operacional, planejar renovacao",
               "CRITICO":"Substituicao imediata recomendada"}

    # Normaliza nomes de classificacao para lookup
    classif_col = 'Classificação' if 'Classificação' in df_out.columns else 'Classificacao'
    order_real = ["EXCELENTE","ÓTIMO","BOM","SATISFATÓRIO","CRÍTICO"]
    dur_ref_r = {"EXCELENTE":"2029/2030","ÓTIMO":"2028/2029","BOM":"2027/2028",
                 "SATISFATÓRIO":"2026/2027","CRÍTICO":"Troca"}
    status_r = {"EXCELENTE":"Hardware ideal, alta longevidade",
                "ÓTIMO":"Hardware adequado, operação estável",
                "BOM":"Funcional, ajustes pontuais necessários",
                "SATISFATÓRIO":"Limite operacional, planejar renovação",
                "CRÍTICO":"Substituição imediata recomendada"}
    for ri, cat in enumerate(order_real, 5):
        qtd = (df_out[classif_col] == cat).sum()
        ws2.row_dimensions[ri].height = 24
        bg, fg = BADGE_COLORS[cat]
        c = ws2.cell(row=ri, column=1, value=cat)
        c.fill = PatternFill("solid", fgColor=bg)
        c.font = Font(name='Arial', bold=True, size=9, color=fg)
        c.alignment = Alignment(horizontal='center', vertical='center'); c.border = brd()
        for ci, v in enumerate([qtd, f"{qtd/total*100:.0f}%", dur_ref_r[cat], status_r[cat]], 2):
            cc = ws2.cell(row=ri, column=ci, value=v)
            cc.font      = Font(name='Arial', size=9)
            cc.alignment = Alignment(horizontal='center' if ci < 5 else 'left', vertical='center')
            cc.border    = brd()
    for ci, w in enumerate([16,8,8,16,38], 1):
        ws2.column_dimensions[get_column_letter(ci)].width = w

    # ABA 3: LEGENDA
    ws3 = wb.create_sheet("Legenda")
    title_strip(ws3, 1, "LEGENDA - METODOLOGIA ALTCOM 365", 3)
    legend_rows = [
        ("Classificacao","Criterio base","Sufixo / Acao"),
        ("EXCELENTE","CPU topo de linha + 16 GB RAM + SSD >= 480 GB + Win 11","—"),
        ("OTIMO","CPU moderna + requisitos minimos atendidos","—"),
        ("BOM","CPU intermediaria superior + requisitos minimos","—"),
        ("SATISFATORIO","CPU intermediaria inferior ou requisitos parciais","—"),
        ("CRITICO","CPU obsoleta — substituicao necessaria","—"),
        ("","",""),
        ("Sufixo","Condicao","Acao recomendada"),
        ("- Upgrade","RAM < 8 GB ou SSD < 200 GB","Upgrade de componente"),
    ]
    ws3.row_dimensions[2].height = 5
    classif_display = {
        "EXCELENTE":"EXCELENTE","OTIMO":"ÓTIMO","BOM":"BOM",
        "SATISFATORIO":"SATISFATÓRIO","CRITICO":"CRÍTICO"
    }
    for ri, (a, b, c_val) in enumerate(legend_rows, 3):
        ws3.row_dimensions[ri].height = 22
        display_a = classif_display.get(a, a)
        if ri in (3, 11):
            for ci, v in enumerate([display_a, b, c_val], 1): hdr(ws3, ri, ci, v)
        elif display_a in BADGE_COLORS:
            bg, fg = BADGE_COLORS[display_a]
            cell = ws3.cell(row=ri, column=1, value=display_a)
            cell.fill = PatternFill("solid", fgColor=bg)
            cell.font = Font(name='Arial', bold=True, size=9, color=fg)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = brd()
            for ci, v in enumerate([b, c_val], 2):
                dat(ws3, ri, ci, v, ha='left')
        else:
            for ci, v in enumerate([display_a, b, c_val], 1):
                dat(ws3, ri, ci, v, ha='left' if ci > 1 else 'center')
    for ci, w in enumerate([18, 58, 28], 1):
        ws3.column_dimensions[get_column_letter(ci)].width = w

    wb.save(output_path)


# ════════════════════════════════════════════════════════════════════════════════
# RELATORIO INTERNO ALTCOM
# ════════════════════════════════════════════════════════════════════════════════
def build_relatorio_interno(input_path, output_path):
    df   = pd.read_excel(input_path)
    hoje = pd.Timestamp.today()
    hoje_str = hoje.strftime('%d/%m/%y %H:%M')

    cliente    = str(df['Cliente'].iloc[0]) if 'Cliente' in df.columns else "Cliente"
    HAS_DATA_AT = 'Data de atualização' in df.columns

    results = df.apply(classify, axis=1)

    # Computa alertas por linha
    alert_data = []
    for idx, row in df.iterrows():
        res = results.loc[idx]

        uso = parse_uso(row.get('Armazenamento utilizado', None))
        armazena_alert = f"Ocupacao de {uso:.1f}%" if (uso is not None and uso > 70) else ""

        so = str(row.get('Sistema operacional', ''))
        so_alert = "Windows 10 - Upgrade" if 'windows 10' in so.lower() else ""

        milvus_alert = ""
        if HAS_DATA_AT:
            dt = _parse_data_at(row.get('Data de atualização'))
            if dt is not None:
                delta = (hoje - dt).days
                if delta > 40:
                    milvus_alert = f"Desatualizado ha {delta} dias"

        alert_data.append({
            'idx':            idx,
            'so_alert':       so_alert,
            'armazena_alert': armazena_alert,
            'milvus_alert':   milvus_alert,
            'tem_alerta':     bool(so_alert or armazena_alert or milvus_alert),
        })

    alert_df  = [a for a in alert_data if a['tem_alerta']]
    alert_map = {a['idx']: a for a in alert_df}

    wb = Workbook()
    ws = wb.active
    ws.title = "Alertas Internos"

    # Define colunas
    cols_hdr = ['Dispositivo','Tipo','S.O.','Processador','RAM',
                'Armazenamento','Uso %']
    if HAS_DATA_AT:
        cols_hdr.append('Data atualizacao')
    cols_hdr += ['Classificacao', 'Sistema Operacional', 'Armazenamento ', 'Agente Milvus']
    NCOLS = len(cols_hdr)

    title_strip(ws, 1,
                f"RELATORIO INTERNO — ALERTAS  |  {cliente.upper()}",
                NCOLS)
    ws.row_dimensions[2].height = 18
    ws.merge_cells(f'A2:{get_column_letter(NCOLS)}2')
    ws['A2'] = f"Dispositivos com alertas ativos — gerado em {hoje_str}"
    ws['A2'].font      = Font(name='Arial', size=9, color="888888")
    ws['A2'].alignment = Alignment(horizontal='left', vertical='center', indent=1)
    ws.row_dimensions[3].height = 5

    ws.row_dimensions[4].height = 22
    for ci, h in enumerate(cols_hdr, 1):
        hdr(ws, 4, ci, h)

    if not alert_df:
        ws.merge_cells(f'A5:{get_column_letter(NCOLS)}5')
        c = ws.cell(row=5, column=1, value="Nenhum alerta identificado no parque.")
        c.font      = Font(name='Arial', size=10, italic=True, color="888888")
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[5].height = 28
        wb.save(output_path)
        return

    r = 5
    for a in alert_df:
        idx  = a['idx']
        row  = df.loc[idx]
        res  = results.loc[idx]

        ws.row_dimensions[r].height = 38
        z = (r % 2 == 0)

        uso   = parse_uso(row.get('Armazenamento utilizado', None))
        uso_s = f"{uso:.1f}%" if uso is not None else "N/D"
        st    = parse_storage(row.get('Armazenamento total', 0))
        st_s  = f"{st:.0f} GB SSD" if st > 0 else "N/D"
        tipo_label   = "Desktop" if device_type(str(row.get('Tipo de dispositivo', ''))) == 'desktop' else "Notebook"
        classif_base = str(res.get('Classificação', res.get('Classificacao','')))
        badge_text   = str(res.get('Badge',''))

        base_vals = [
            (str(row.get('Nome do dispositivo','')), 'left'),
            (tipo_label,                              'center'),
            (str(row.get('Sistema operacional','')),  'left'),
            (str(row.get('Processador','')),           'left'),
            (str(row.get('Memória RAM total', row.get('Memoria RAM total',''))), 'center'),
            (st_s,  'center'),
            (uso_s, 'center'),
        ]
        if HAS_DATA_AT:
            base_vals.append((str(row.get('Data de atualização','')), 'center'))

        ci = 1
        for v, ha in base_vals:
            dat(ws, r, ci, v, z=z, ha=ha)
            ci += 1

        # Classificacao badge
        c = ws.cell(row=r, column=ci, value=badge_text)
        fill, font = badge_style(classif_base, badge_text)
        c.fill = fill; c.font = font
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = brd()
        ci += 1

        # 3 colunas de alerta
        # Dispositivos CRÍTICO: informar que foram laudados para troca, sem procedimento específico
        if classif_base == "CRÍTICO":
            for col_ci in range(ci, ci + 3):
                c = ws.cell(row=r, column=col_ci,
                            value="Laudado para Troca" if col_ci == ci else "—")
                c.fill = PatternFill("solid", fgColor="F5E6D3")
                c.font = Font(name='Arial', size=8,
                              bold=(col_ci == ci), color="7C3D0C",
                              italic=(col_ci != ci))
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                c.border = brd()
            ci += 3
        else:
            alert_styles = [
                (a['so_alert'],       "FFF2CC", "7F6000"),
                (a['armazena_alert'], "FFE7E7", "9C0006"),
                (a['milvus_alert'],   "D6E4F0", "1F4E79"),
            ]
            for v, bg_fill, fc in alert_styles:
                c = ws.cell(row=r, column=ci, value=v if v else "—")
                if v:
                    c.fill = PatternFill("solid", fgColor=bg_fill)
                    c.font = Font(name='Arial', size=8, bold=True, color=fc)
                else:
                    c.fill = PatternFill("solid", fgColor=ZEBRA if z else WHITE)
                    c.font = Font(name='Arial', size=8, color="BBBBBB")
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                c.border = brd()
                ci += 1

        r += 1

    # Larguras
    widths = [22, 9, 24, 36, 8, 14, 7]
    if HAS_DATA_AT:
        widths.append(18)
    widths += [18, 22, 18, 22]
    for ci, w in enumerate(widths[:NCOLS], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    ws.freeze_panes = 'A5'

    # Rodape
    r_foot = r
    ws.row_dimensions[r_foot].height = 20
    ws.merge_cells(f'A{r_foot}:{get_column_letter(NCOLS - 3)}{r_foot}')
    c = ws.cell(row=r_foot, column=1,
                value=f"Total com alertas: {len(alert_df)}  |  Total no parque: {len(df)}")
    c.font      = Font(name='Arial', bold=True, size=9, color=NAVY)
    c.fill      = PatternFill("solid", fgColor="E8EDF2")
    c.alignment = Alignment(horizontal='left', vertical='center', indent=1)
    c.border    = brd()

    wb.save(output_path)


# Alias
build_laudo = build_laudo_cliente
