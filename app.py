"""
Altcom 365 – Gerador de Laudo de Eficiência Técnica
Backend Flask – versão standalone
"""
import os, sys, io, tempfile
from flask import Flask, request, send_file, jsonify, render_template

sys.path.insert(0, os.path.dirname(__file__))
from engine_altcom365 import classify, BADGE_COLORS
from build_laudo import build_laudo_cliente, build_relatorio_interno

import pandas as pd

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB

ALLOWED_EXT = {'.xlsx', '.xls'}

def allowed_file(filename):
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXT

@app.route('/')
def index():
    return render_template('index.html')

# ── helpers ───────────────────────────────────────────────────────────────────
def _load_df(request_files):
    if 'arquivo' not in request_files:
        return None, jsonify({'erro': 'Nenhum arquivo enviado.'}), 400
    f = request_files['arquivo']
    if not f.filename or not allowed_file(f.filename):
        return None, jsonify({'erro': 'Formato inválido. Envie um arquivo .xlsx'}), 400
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        f.save(tmp.name)
        path = tmp.name
    return path, None, None

def _validate_cols(df):
    colunas_req = ['Processador', 'Memória RAM total', 'Armazenamento total',
                   'Armazenamento utilizado', 'Sistema operacional', 'Nome do dispositivo']
    faltando = [c for c in colunas_req if c not in df.columns]
    return faltando

def _get_cliente(df):
    return str(df['Cliente'].iloc[0]).strip() if 'Cliente' in df.columns else "Cliente"

# ── /gerar  — Laudo do Cliente ────────────────────────────────────────────────
@app.route('/gerar', methods=['POST'])
def gerar():
    path, err_resp, err_code = _load_df(request.files)
    if err_resp:
        return err_resp, err_code

    output_path = None
    try:
        df = pd.read_excel(path)
        faltando = _validate_cols(df)
        if faltando:
            return jsonify({'erro': f'Colunas não encontradas: {", ".join(faltando)}'}), 400

        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp_out:
            output_path = tmp_out.name

        build_laudo_cliente(path, output_path)

        cliente = _get_cliente(df)
        nome    = f"Laudo_Eficiencia_{cliente.replace(' ','_')}.xlsx"

        with open(output_path, 'rb') as fout:
            data = fout.read()

        return send_file(
            io.BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nome
        )
    except Exception as e:
        return jsonify({'erro': f'Erro ao processar: {str(e)}'}), 500
    finally:
        for p in [path, output_path]:
            if p:
                try: os.unlink(p)
                except: pass

# ── /gerar/interno  — Relatório Interno Altcom ───────────────────────────────
@app.route('/gerar/interno', methods=['POST'])
def gerar_interno():
    path, err_resp, err_code = _load_df(request.files)
    if err_resp:
        return err_resp, err_code

    output_path = None
    try:
        df = pd.read_excel(path)
        faltando = _validate_cols(df)
        if faltando:
            return jsonify({'erro': f'Colunas não encontradas: {", ".join(faltando)}'}), 400

        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp_out:
            output_path = tmp_out.name

        build_relatorio_interno(path, output_path)

        cliente = _get_cliente(df)
        nome    = f"Relatorio_Interno_{cliente.replace(' ','_')}.xlsx"

        with open(output_path, 'rb') as fout:
            data = fout.read()

        return send_file(
            io.BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nome
        )
    except Exception as e:
        return jsonify({'erro': f'Erro ao processar relatório interno: {str(e)}'}), 500
    finally:
        for p in [path, output_path]:
            if p:
                try: os.unlink(p)
                except: pass

# ── /preview  — retorna resumo cliente + alertas internos ────────────────────
@app.route('/preview', methods=['POST'])
def preview():
    path, err_resp, err_code = _load_df(request.files)
    if err_resp:
        return err_resp, err_code

    try:
        df = pd.read_excel(path)
        faltando = _validate_cols(df)
        if faltando:
            return jsonify({'erro': f'Colunas não encontradas: {", ".join(faltando)}'}), 400

        results = df.apply(classify, axis=1)
        df_out  = pd.concat([df, results], axis=1)

        cliente = _get_cliente(df)
        total   = len(df_out)

        # ── Resumo Cliente ───────────────────────────────────────────────────
        order  = ["EXCELENTE", "ÓTIMO", "BOM", "SATISFATÓRIO", "CRÍTICO"]
        resumo = []
        for cat in order:
            qtd = int((df_out['Classificação'] == cat).sum())
            if qtd == 0:
                continue
            mp = int(df_out[df_out['Badge'].str.contains('Man. Prev.') & (df_out['Classificação']==cat)].shape[0])
            up = int(df_out[df_out['Badge'].str.contains('Upgrade')    & (df_out['Classificação']==cat)].shape[0])
            bg, fg = BADGE_COLORS[cat]
            resumo.append({'label': cat, 'qtd': qtd, 'pct': round(qtd/total*100),
                           'man_prev': mp, 'upgrade': up, 'bg': bg, 'fg': fg})

        # ── Alertas Internos ─────────────────────────────────────────────────
        hoje = pd.Timestamp.today()

        # Armazenamento > 70%
        def _uso(v):
            s = str(v)
            if 'nan' in s.lower(): return None
            try: return float(s.replace('%','').strip())
            except: return None

        n_armazena = sum(
            1 for v in df['Armazenamento utilizado']
            if (lambda u: u is not None and u > 70)(_uso(v))
        )

        # Windows desatualizado
        n_windows = int(df['Sistema operacional']
                        .str.lower().str.contains('windows 10').sum())

        # Agente Milvus > 40 dias
        n_milvus     = 0
        tem_milvus   = 'Data de atualização' in df.columns
        if tem_milvus:
            from build_laudo import _parse_data_at
            for v in df['Data de atualização']:
                dt = _parse_data_at(v)
                if dt is not None:
                    try:
                        delta = (hoje - dt).days
                        if delta > 40:
                            n_milvus += 1
                    except Exception:
                        pass

        alertas = {
            'armazenamento': n_armazena,
            'windows':       n_windows,
            'milvus':        n_milvus,
            'tem_milvus':    tem_milvus,
        }

        return jsonify({
            'cliente': cliente,
            'total':   total,
            'resumo':  resumo,
            'alertas': alertas,
        })

    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        try: os.unlink(path)
        except: pass

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
