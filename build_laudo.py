import os, sys, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from engine_altcom365 import classify, device_type, parse_uso, parse_storage, BADGE_COLORS
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

NAVY, WHITE, ZEBRA, BORDER_C = "0D1B2A", "FFFFFF", "F5F7FA", "D0D5DD"
CYAN = "00C8D4"

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
    for col in range(1, span+1):
        ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=NAVY)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
    c = ws.cell(row=row, column=1, value=text)
    c.font = Font(name='Arial', bold=True, size=13, color=WHITE)
    c.alignment = Alignment(horizontal='left', vertical='center', indent=1)

def build_laudo(input_path, output_path):
    df = pd.read_excel(input_path)
    results = df.apply(classify, axis=1)
    df_out = pd.concat([df, results], axis=1)

    cliente = str(df_out['Cliente'].iloc[0]) if 'Cliente' in df_out.columns else "Cliente"
    hoje    = pd.Timestamp.today().strftime('%d/%m/%y')
    total   = len(df_out)

    wb = Workbook()

    # ── ABA 1: LAUDO ─────────────────────────────────────────────────────────
    ws = wb.active; ws.title = "Laudo"
    NCOLS = 12
    title_strip(ws, 1, f"LAUDO DE EFICIÊNCIA TÉCNICA  |  {cliente.upper()}", NCOLS)
    ws.row_dimensions[2].height = 18
    ws.merge_cells(f'A2:{get_column_letter(NCOLS)}2')
    ws['A2'] = f"Metodologia Altcom 365 – Avaliação do Parque de Estações de Trabalho - Data {hoje}"
    ws['A2'].font = Font(name='Arial', size=9, color="888888")
    ws['A2'].alignment = Alignment(horizontal='left', vertical='center', indent=1)
    ws.row_dimensions[3].height = 5

    cols_hdr = ['Tipo','Dispositivo','S.O.','Processador','Núcleos','RAM',
                'Armazenamento','Uso %','Classificação','Descritivo','Durabilidade','Sugestão']
    ws.row_dimensions[4].height = 22
    for ci, h in enumerate(cols_hdr, 1): hdr(ws, 4, ci, h)

    for ridx, row in df_out.iterrows():
        r = ridx + 5; ws.row_dimensions[r].height = 44; z = ridx % 2 == 0
        uso = parse_uso(row['Armazenamento utilizado'])
        uso_s = f"{uso:.1f}%" if uso is not None else "N/D"
        st = parse_storage(row['Armazenamento total'])
        st_s = f"{st:.0f} GB SSD" if st > 0 else "N/D"
        tipo_label = "Desktop" if device_type(str(row.get('Tipo de dispositivo',''))) == 'desktop' else "Notebook"
        classif_base = row['Classificação']
        badge_text   = row['Badge']

        vals_align = [
            (tipo_label,'center'),(row['Nome do dispositivo'],'left'),
            (row['Sistema operacional'],'left'),(row['Processador'],'left'),
            (row['Núcleos do processador'],'center'),(row['Memória RAM total'],'center'),
            (st_s,'center'),(uso_s,'center'),(badge_text,'center'),
            (row['Descritivo'],'left'),(row['Durabilidade estimada'],'center'),(row['Sugestão'],'left'),
        ]
        for ci, (v, ha) in enumerate(vals_align, 1):
            if ci == 9:
                c = ws.cell(row=r, column=ci, value=v)
                fill, font = badge_style(classif_base, badge_text)
                c.fill = fill; c.font = font
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                c.border = brd()
            else:
                dat(ws, r, ci, v, z=z, ha=ha)

    for i, w in enumerate([10,20,26,38,8,8,14,7,20,50,12,30], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A5'

    # ── ABA 2: RESUMO ─────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Resumo Executivo")
    title_strip(ws2, 1, "RESUMO EXECUTIVO", 5)
    ws2.row_dimensions[2].height = 18
    ws2.merge_cells('A2:E2')
    ws2['A2'] = f"Total de dispositivos avaliados: {total} | Cliente: {cliente} | Data: {hoje}"
    ws2['A2'].font = Font(name='Arial', size=10, color="444444")
    ws2['A2'].alignment = Alignment(indent=1)
    ws2.row_dimensions[3].height = 5
    for ci, h in enumerate(['Classificação','Qtd','%','Durabilidade ref.','Status'], 1):
        hdr(ws2, 4, ci, h)
    ws2.row_dimensions[4].height = 22

    order   = ["EXCELENTE","ÓTIMO","BOM","SATISFATÓRIO","CRÍTICO"]
    dur_ref = {"EXCELENTE":"2029/2030","ÓTIMO":"2028/2029","BOM":"2027/2028","SATISFATÓRIO":"2026/2027","CRÍTICO":"Troca"}
    status  = {"EXCELENTE":"Hardware ideal, alta longevidade","ÓTIMO":"Hardware adequado, operação estável",
               "BOM":"Funcional, ajustes pontuais necessários","SATISFATÓRIO":"Limite operacional, planejar renovação",
               "CRÍTICO":"Substituição imediata recomendada"}
    for ri, cat in enumerate(order, 5):
        qtd = (df_out['Classificação'] == cat).sum(); ws2.row_dimensions[ri].height = 24
        bg, fg = BADGE_COLORS[cat]
        c = ws2.cell(row=ri, column=1, value=cat)
        c.fill = PatternFill("solid", fgColor=bg); c.font = Font(name='Arial', bold=True, size=9, color=fg)
        c.alignment = Alignment(horizontal='center', vertical='center'); c.border = brd()
        for ci, v in enumerate([qtd, f"{qtd/total*100:.0f}%", dur_ref[cat], status[cat]], 2):
            cc = ws2.cell(row=ri, column=ci, value=v)
            cc.font = Font(name='Arial', size=9)
            cc.alignment = Alignment(horizontal='center' if ci < 5 else 'left', vertical='center')
            cc.border = brd()
    for ci, w in enumerate([16,8,8,16,38], 1):
        ws2.column_dimensions[get_column_letter(ci)].width = w

    # ── ABA 3: LEGENDA ────────────────────────────────────────────────────────
    ws3 = wb.create_sheet("Legenda")
    title_strip(ws3, 1, "LEGENDA – METODOLOGIA ALTCOM 365", 3)
    legend_rows = [
        ("Classificação","Critério base","Sufixo / Ação"),
        ("EXCELENTE","CPU topo de linha + 16 GB RAM + SSD ≥ 480 GB + Win 11","—"),
        ("ÓTIMO","CPU moderna + requisitos mínimos atendidos","—"),
        ("BOM","CPU intermediária superior + requisitos mínimos","—"),
        ("SATISFATÓRIO","CPU intermediária inferior ou requisitos parciais","—"),
        ("CRÍTICO","CPU obsoleta — substituição necessária","—"),
        ("","",""),
        ("Sufixo","Condição","Ação recomendada"),
        ("- Man. Prev.","Uso de armazenamento > 70%","Preventiva de disco"),
        ("- Upgrade","SO < Win 11, RAM < 8 GB ou SSD < 220 GB","Upgrade de componente"),
    ]
    ws3.row_dimensions[2].height = 5
    for ri, (a, b, c_val) in enumerate(legend_rows, 3):
        ws3.row_dimensions[ri].height = 22
        if ri in (3, 11):
            for ci, v in enumerate([a, b, c_val], 1): hdr(ws3, ri, ci, v)
        elif a in BADGE_COLORS:
            bg, fg = BADGE_COLORS[a]
            cell = ws3.cell(row=ri, column=1, value=a)
            cell.fill = PatternFill("solid", fgColor=bg); cell.font = Font(name='Arial', bold=True, size=9, color=fg)
            cell.alignment = Alignment(horizontal='center', vertical='center'); cell.border = brd()
            for ci, v in enumerate([b, c_val], 2): dat(ws3, ri, ci, v, ha='left')
        else:
            for ci, v in enumerate([a, b, c_val], 1):
                dat(ws3, ri, ci, v, ha='left' if ci > 1 else 'center')
    for ci, w in enumerate([18, 58, 28], 1):
        ws3.column_dimensions[get_column_letter(ci)].width = w

    wb.save(output_path)
