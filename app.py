"""
Altcom 365 – Gerador de Laudo de Eficiência Técnica
Backend Flask – versão standalone
"""
import os, sys, io, tempfile
from flask import Flask, request, send_file, jsonify, render_template

sys.path.insert(0, os.path.dirname(__file__))
from engine_altcom365 import classify, BADGE_COLORS
from build_laudo import build_laudo

import pandas as pd

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB

ALLOWED_EXT = {'.xlsx', '.xls'}

def allowed_file(filename):
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXT

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/gerar', methods=['POST'])
def gerar():
    if 'arquivo' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado.'}), 400

    f = request.files['arquivo']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'erro': 'Formato inválido. Envie um arquivo .xlsx'}), 400

    # Salva temporariamente o upload
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp_in:
        f.save(tmp_in.name)
        input_path = tmp_in.name

    try:
        # Valida que tem as colunas esperadas
        df = pd.read_excel(input_path)
        colunas_req = ['Processador', 'Memória RAM total', 'Armazenamento total',
                       'Armazenamento utilizado', 'Sistema operacional', 'Nome do dispositivo']
        faltando = [c for c in colunas_req if c not in df.columns]
        if faltando:
            return jsonify({'erro': f'Colunas não encontradas: {", ".join(faltando)}'}), 400

        # Gera o laudo
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp_out:
            output_path = tmp_out.name

        build_laudo(input_path, output_path)

        # Nome do cliente para o arquivo de saída
        cliente = str(df['Cliente'].iloc[0]).strip() if 'Cliente' in df.columns else "Cliente"
        nome_arquivo = f"Laudo_Eficiencia_{cliente.replace(' ','_')}.xlsx"

        # Lê e envia o arquivo gerado
        with open(output_path, 'rb') as fout:
            data = fout.read()

        return send_file(
            io.BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nome_arquivo
        )

    except Exception as e:
        return jsonify({'erro': f'Erro ao processar: {str(e)}'}), 500

    finally:
        for p in [input_path, output_path]:
            try: os.unlink(p)
            except: pass

@app.route('/preview', methods=['POST'])
def preview():
    """Retorna JSON com resumo para exibir antes do download."""
    if 'arquivo' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado.'}), 400

    f = request.files['arquivo']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'erro': 'Formato inválido.'}), 400

    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        f.save(tmp.name)
        path = tmp.name

    try:
        df = pd.read_excel(path)
        colunas_req = ['Processador', 'Memória RAM total', 'Armazenamento total',
                       'Armazenamento utilizado', 'Sistema operacional', 'Nome do dispositivo']
        faltando = [c for c in colunas_req if c not in df.columns]
        if faltando:
            return jsonify({'erro': f'Colunas não encontradas: {", ".join(faltando)}'}), 400

        results = df.apply(classify, axis=1)
        df_out  = pd.concat([df, results], axis=1)

        cliente = str(df['Cliente'].iloc[0]).strip() if 'Cliente' in df.columns else "Cliente"
        total   = len(df_out)

        order = ["EXCELENTE", "ÓTIMO", "BOM", "SATISFATÓRIO", "CRÍTICO"]
        resumo = []
        for cat in order:
            qtd = int((df_out['Classificação'] == cat).sum())
            if qtd == 0: continue
            mp = int(df_out[df_out['Badge'].str.contains('Man. Prev.') & (df_out['Classificação']==cat)].shape[0])
            up = int(df_out[df_out['Badge'].str.contains('Upgrade')    & (df_out['Classificação']==cat)].shape[0])
            bg, fg = BADGE_COLORS[cat]
            resumo.append({'label': cat, 'qtd': qtd, 'pct': round(qtd/total*100),
                           'man_prev': mp, 'upgrade': up, 'bg': bg, 'fg': fg})

        return jsonify({'cliente': cliente, 'total': total, 'resumo': resumo})

    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        try: os.unlink(path)
        except: pass

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
