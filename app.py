"""
Altcom 365 v2 — Backend Flask
Suporta upload do Relatório Milvus Completo (todos os clientes)
e geração de laudos em ZIP.
"""
import os, sys, io, uuid, pickle, zipfile, tempfile
from datetime import datetime, timedelta
from flask import Flask, request, send_file, jsonify, render_template, session

sys.path.insert(0, os.path.dirname(__file__))
from engine_altcom365  import classify, BADGE_COLORS
from build_laudo       import (build_laudo_cliente, build_relatorio_interno,
                                normalize_df, is_new_format)
from alertas_internos  import (calcular_versao_referencia, calcular_alertas,
                                resumo_alertas)
import pandas as pd

app = Flask(__name__)
app.secret_key          = os.environ.get('SECRET_KEY', 'altcom365-v2-secret-key')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB

ALLOWED_EXT = {'.xlsx', '.xls'}
SESS_DIR    = tempfile.gettempdir()
SESS_TTL    = timedelta(hours=1)

# Colunas obrigatórias (novo formato); DATA DE ATUALIZAÇÃO é opcional
COLUNAS_OBRIGATORIAS = [
    'NOME DO DISPOSITIVO', 'TIPO DO DISPOSITIVO', 'SISTEMA OPERACIONAL',
    'PROCESSADOR', 'NÚCLEOS DO PROCESSADOR', 'MEMÓRIA RAM TOTAL',
    'ARMAZENAMENTO INTERNO TOTAL', 'ARMAZENAMENTO INTERNO UTILIZADO',
    'ARMAZENAMENTO INTERNO DISPONÍVEL', 'VERSÃO DO CLIENT',
    'APELIDO', 'USUÁRIO LOGADO', 'EXCLUÍDO', 'NOME FANTASIA DO CLIENTE',
]

# ── Helpers de sessão ─────────────────────────────────────────────────────────

def _sess_path(sid):
    return os.path.join(SESS_DIR, f"altcom365_{sid}.pkl")

def _save_session(data: dict) -> str:
    sid = str(uuid.uuid4())
    with open(_sess_path(sid), 'wb') as f:
        pickle.dump(data, f)
    return sid

def _load_session(sid: str):
    path = _sess_path(sid)
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        data = pickle.load(f)
    # Expira em 1h
    if datetime.now() - data.get('timestamp', datetime.min) > SESS_TTL:
        os.unlink(path)
        return None
    return data

def _clear_old_sessions():
    """Remove arquivos de sessão expirados."""
    try:
        prefix = "altcom365_"
        for fn in os.listdir(SESS_DIR):
            if fn.startswith(prefix) and fn.endswith('.pkl'):
                path = os.path.join(SESS_DIR, fn)
                try:
                    if os.path.getmtime(path) < (datetime.now() - SESS_TTL).timestamp():
                        os.unlink(path)
                except Exception:
                    pass
    except Exception:
        pass

def _get_current_session():
    sid = session.get('sess_id')
    if not sid:
        return None
    return _load_session(sid)

def allowed_file(filename):
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXT

# ── Rota principal ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

# ══════════════════════════════════════════════════════════════════════════════
# ROTAS V2 — novo formato Milvus Completo
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/upload-completo', methods=['POST'])
def upload_completo():
    """
    Recebe o Relatório Milvus Completo.
    Valida colunas, aplica filtros, calcula versão de referência.
    Retorna lista de clientes detectados.
    """
    _clear_old_sessions()

    if 'arquivo' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado.'}), 400
    f = request.files['arquivo']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'erro': 'Formato inválido. Envie um arquivo .xlsx'}), 400

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name

        df = pd.read_excel(tmp_path)

        # Detecta formato antigo
        if not is_new_format(df):
            return jsonify({
                'erro': 'Este arquivo parece estar no formato antigo. '
                        'Use o Relatório Milvus Completo (export com todos os clientes).'
            }), 400

        # Valida colunas obrigatórias
        faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
        if faltando:
            return jsonify({'erro': f'Colunas ausentes: {", ".join(faltando)}'}), 400

        # Filtros automáticos
        df = df[df['EXCLUÍDO'].astype(str).str.upper() != 'SIM']
        df = df[~df['NOME FANTASIA DO CLIENTE'].astype(str).str.lower()
                  .str.contains('altcom', na=False)]
        # Remove servidores (qualquer critério basta)
        df = df[~df['PROCESSADOR'].astype(str).str.contains('Xeon', case=False, na=False)]
        if 'SERVIDOR' in df.columns:
            df = df[df['SERVIDOR'].astype(str).str.upper() != 'SIM']
        df = df[df['TIPO DO DISPOSITIVO'].astype(str).str.lower() != 'servidor']
        df = df.reset_index(drop=True)

        total = len(df)
        if total == 0:
            return jsonify({'erro': 'Nenhum dispositivo ativo encontrado após filtros.'}), 400

        # Calcula versão de referência do agente (sobre o parque inteiro)
        versao_ref, n_desat, pct_desat = calcular_versao_referencia(df)
        tem_data_at = 'DATA DE ATUALIZAÇÃO' in df.columns

        # Lista de clientes ordenada
        clientes = sorted(
            df['NOME FANTASIA DO CLIENTE'].dropna().unique().tolist()
        )

        # Contagem de dispositivos por cliente
        contagem = df['NOME FANTASIA DO CLIENTE'].value_counts().to_dict()

        # Salva sessão
        sess_data = {
            'df':               df,
            'versao_ref':       versao_ref,
            'n_desatualizadas': n_desat,
            'pct_desatualizadas': pct_desat,
            'tem_data_at':      tem_data_at,
            'timestamp':        datetime.now(),
        }
        sid = _save_session(sess_data)
        session['sess_id'] = sid

        return jsonify({
            'clientes':          clientes,
            'contagem':          contagem,
            'total_dispositivos': total,
            'tem_data_at':       tem_data_at,
            'versao_ref':        versao_ref,
            'n_desatualizadas':  n_desat,
            'pct_desatualizadas': pct_desat,
        })

    except Exception as e:
        return jsonify({'erro': f'Erro ao processar arquivo: {str(e)}'}), 500
    finally:
        if tmp_path:
            try: os.unlink(tmp_path)
            except: pass


@app.route('/preview-clientes', methods=['POST'])
def preview_clientes():
    """
    Recebe lista de clientes selecionados.
    Retorna preview consolidado: resumo de classificações + contadores de alertas.
    """
    data = request.get_json(force=True, silent=True) or {}
    clientes_sel = data.get('clientes', [])
    if not clientes_sel:
        return jsonify({'erro': 'Nenhum cliente selecionado.'}), 400

    sess_data = _get_current_session()
    if sess_data is None:
        return jsonify({'erro': 'Sessão expirada. Faça o upload novamente.'}), 400

    df          = sess_data['df']
    versao_ref  = sess_data['versao_ref']
    n_desat     = sess_data['n_desatualizadas']
    pct_desat   = sess_data['pct_desatualizadas']

    # Filtra clientes selecionados
    df_sel = df[df['NOME FANTASIA DO CLIENTE'].isin(clientes_sel)].copy()
    if df_sel.empty:
        return jsonify({'erro': 'Nenhum dispositivo encontrado para os clientes selecionados.'}), 400

    # Calcula alertas + normaliza
    df_alertas = calcular_alertas(df_sel, versao_ref)
    df_norm    = normalize_df(df_alertas)

    # Constrói resumo
    preview = resumo_alertas(df_norm, versao_ref, n_desat, pct_desat)
    preview['clientes_selecionados'] = clientes_sel

    return jsonify(preview)


@app.route('/baixar-laudos-cliente', methods=['POST'])
def baixar_laudos_cliente():
    """
    Gera ZIP com 1 Excel de Laudo do Cliente por cliente selecionado.
    """
    data = request.get_json(force=True, silent=True) or {}
    clientes_sel = data.get('clientes', [])
    if not clientes_sel:
        return jsonify({'erro': 'Nenhum cliente selecionado.'}), 400

    sess_data = _get_current_session()
    if sess_data is None:
        return jsonify({'erro': 'Sessão expirada. Faça o upload novamente.'}), 400

    df         = sess_data['df']
    versao_ref = sess_data['versao_ref']

    try:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for cliente in clientes_sel:
                df_cli = df[df['NOME FANTASIA DO CLIENTE'] == cliente].copy()
                if df_cli.empty:
                    continue
                df_alertas = calcular_alertas(df_cli, versao_ref)
                df_norm    = normalize_df(df_alertas)

                with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
                    out_path = tmp.name
                try:
                    build_laudo_cliente(df_norm, out_path, cliente)
                    with open(out_path, 'rb') as fout:
                        xlsx_data = fout.read()
                finally:
                    try: os.unlink(out_path)
                    except: pass

                safe_name = ''.join(c if c.isalnum() or c in ' _-' else '_'
                                    for c in cliente)[:40]
                zf.writestr(f"Laudo_Eficiencia_{safe_name}.xlsx", xlsx_data)

        zip_buf.seek(0)
        return send_file(
            zip_buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name='Laudos_Clientes_Altcom365.zip',
        )
    except Exception as e:
        return jsonify({'erro': f'Erro ao gerar laudos: {str(e)}'}), 500


@app.route('/baixar-relatorios-internos', methods=['POST'])
def baixar_relatorios_internos():
    """
    Gera ZIP com 1 Excel de Relatório Interno por cliente selecionado.
    """
    data = request.get_json(force=True, silent=True) or {}
    clientes_sel = data.get('clientes', [])
    if not clientes_sel:
        return jsonify({'erro': 'Nenhum cliente selecionado.'}), 400

    sess_data = _get_current_session()
    if sess_data is None:
        return jsonify({'erro': 'Sessão expirada. Faça o upload novamente.'}), 400

    df         = sess_data['df']
    versao_ref = sess_data['versao_ref']

    try:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for cliente in clientes_sel:
                df_cli = df[df['NOME FANTASIA DO CLIENTE'] == cliente].copy()
                if df_cli.empty:
                    continue
                df_alertas = calcular_alertas(df_cli, versao_ref)
                df_norm    = normalize_df(df_alertas)

                with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
                    out_path = tmp.name
                try:
                    build_relatorio_interno(df_norm, out_path, cliente, versao_ref)
                    with open(out_path, 'rb') as fout:
                        xlsx_data = fout.read()
                finally:
                    try: os.unlink(out_path)
                    except: pass

                safe_name = ''.join(c if c.isalnum() or c in ' _-' else '_'
                                    for c in cliente)[:40]
                zf.writestr(f"Relatorio_Interno_{safe_name}.xlsx", xlsx_data)

        zip_buf.seek(0)
        return send_file(
            zip_buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name='Relatorios_Internos_Altcom365.zip',
        )
    except Exception as e:
        return jsonify({'erro': f'Erro ao gerar relatórios internos: {str(e)}'}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROTAS LEGADAS — formato antigo (retrocompatibilidade)
# ══════════════════════════════════════════════════════════════════════════════

def _load_file_df(req_files):
    if 'arquivo' not in req_files:
        return None, jsonify({'erro': 'Nenhum arquivo enviado.'}), 400
    f = req_files['arquivo']
    if not f.filename or not allowed_file(f.filename):
        return None, jsonify({'erro': 'Formato inválido. Envie um arquivo .xlsx'}), 400
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        f.save(tmp.name)
    return tmp.name, None, None

def _validate_cols_antigo(df):
    colunas_req = ['Processador', 'Memória RAM total', 'Armazenamento total',
                   'Armazenamento utilizado', 'Sistema operacional', 'Nome do dispositivo']
    return [c for c in colunas_req if c not in df.columns]

def _get_cliente(df):
    return str(df['Cliente'].iloc[0]).strip() if 'Cliente' in df.columns else "Cliente"


@app.route('/gerar', methods=['POST'])
def gerar():
    path, err_resp, err_code = _load_file_df(request.files)
    if err_resp:
        return err_resp, err_code

    output_path = None
    try:
        df = pd.read_excel(path)
        df = normalize_df(df)
        faltando = _validate_cols_antigo(df)
        if faltando:
            return jsonify({'erro': f'Colunas não encontradas: {", ".join(faltando)}'}), 400

        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            output_path = tmp.name

        cliente = _get_cliente(df)
        build_laudo_cliente(df, output_path, cliente)

        with open(output_path, 'rb') as fout:
            data = fout.read()

        return send_file(
            io.BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f"Laudo_Eficiencia_{cliente.replace(' ','_')}.xlsx",
        )
    except Exception as e:
        return jsonify({'erro': f'Erro ao processar: {str(e)}'}), 500
    finally:
        for p in [path, output_path]:
            if p:
                try: os.unlink(p)
                except: pass


@app.route('/gerar/interno', methods=['POST'])
def gerar_interno():
    path, err_resp, err_code = _load_file_df(request.files)
    if err_resp:
        return err_resp, err_code

    output_path = None
    try:
        df = pd.read_excel(path)
        df = normalize_df(df)
        faltando = _validate_cols_antigo(df)
        if faltando:
            return jsonify({'erro': f'Colunas não encontradas: {", ".join(faltando)}'}), 400

        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            output_path = tmp.name

        cliente = _get_cliente(df)
        build_relatorio_interno(df, output_path, cliente)

        with open(output_path, 'rb') as fout:
            data = fout.read()

        return send_file(
            io.BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f"Relatorio_Interno_{cliente.replace(' ','_')}.xlsx",
        )
    except Exception as e:
        return jsonify({'erro': f'Erro ao processar relatório interno: {str(e)}'}), 500
    finally:
        for p in [path, output_path]:
            if p:
                try: os.unlink(p)
                except: pass


@app.route('/preview', methods=['POST'])
def preview():
    """Rota legada para formato antigo (1 cliente por vez)."""
    path, err_resp, err_code = _load_file_df(request.files)
    if err_resp:
        return err_resp, err_code

    try:
        df = pd.read_excel(path)
        df = normalize_df(df)
        faltando = _validate_cols_antigo(df)
        if faltando:
            return jsonify({'erro': f'Colunas não encontradas: {", ".join(faltando)}'}), 400

        results = df.apply(classify, axis=1)
        df_out  = pd.concat([df.reset_index(drop=True),
                             results.reset_index(drop=True)], axis=1)

        cliente = _get_cliente(df)
        total   = len(df_out)
        hoje    = pd.Timestamp.today()

        order  = ["EXCELENTE", "ÓTIMO", "BOM", "SATISFATÓRIO", "CRÍTICO"]
        resumo = []
        for cat in order:
            qtd = int((df_out['Classificação'] == cat).sum())
            if qtd == 0:
                continue
            mask = df_out['Classificação'] == cat
            mp   = int(df_out.loc[mask, 'Badge'].str.contains('Man. Prev.').sum())
            up   = int(df_out.loc[mask, 'Badge'].str.contains('Upgrade').sum())
            bg, fg = BADGE_COLORS[cat]
            resumo.append({'label': cat, 'qtd': qtd, 'pct': round(qtd/total*100),
                           'man_prev': mp, 'upgrade': up, 'bg': bg, 'fg': fg})

        from build_laudo import _parse_data_at
        tem_milvus = 'Data de atualização' in df.columns

        def _uso(v):
            s = str(v)
            if 'nan' in s.lower(): return None
            try: return float(s.replace('%','').strip())
            except: return None

        n_armazena = n_windows = n_milvus = n_troca = 0
        for _, row_a in df_out.iterrows():
            classif = str(row_a.get('Classificação',''))
            uso  = _uso(row_a.get('Armazenamento utilizado',''))
            so   = str(row_a.get('Sistema operacional','')).lower()
            dt_v = row_a.get('Data de atualização', None) if tem_milvus else None
            dt   = _parse_data_at(dt_v) if dt_v is not None else None
            milvus_flag = dt is not None and (hoje - dt).days > 40
            if classif == 'CRÍTICO':
                if (uso is not None and uso > 70) or 'windows 10' in so or milvus_flag:
                    n_troca += 1
                continue
            if uso is not None and uso > 70:
                n_armazena += 1
            if 'windows 10' in so:
                n_windows += 1
            if milvus_flag:
                n_milvus += 1

        return jsonify({
            'cliente': cliente, 'total': total, 'resumo': resumo,
            'alertas': {
                'armazenamento': n_armazena, 'windows': n_windows,
                'milvus': n_milvus, 'tem_milvus': tem_milvus,
                'laudados_troca': n_troca,
            },
        })

    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        try: os.unlink(path)
        except: pass


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
