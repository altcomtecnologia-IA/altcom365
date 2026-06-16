"""
Altcom 365 v2 — Backend Flask
Suporta upload do Relatório Milvus Completo (todos os clientes)
e geração de laudos em ZIP.

V11: PostgreSQL + SQLAlchemy + Flask-Migrate + APScheduler
"""
import os, sys, io, uuid, pickle, zipfile, tempfile, logging
from datetime import datetime, timedelta
from flask import Flask, request, send_file, jsonify, render_template, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

sys.path.insert(0, os.path.dirname(__file__))
from engine_altcom365  import classify, BADGE_COLORS
from build_laudo       import (build_laudo_cliente, build_relatorio_interno,
                                normalize_df, is_new_format)
from alertas_internos  import (calcular_versao_referencia, calcular_alertas,
                                resumo_alertas)
from models import db, ClientesMap, DispositivosMap, LaudosSnapshots
import pandas as pd
import milvus_api
import threading
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key          = os.environ.get('SECRET_KEY', 'altcom365-v2-secret-key')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB

# ── Banco de dados ─────────────────────────────────────────────────────────────
_db_url = os.environ.get('DATABASE_URL', '')
# Render entrega 'postgres://' mas SQLAlchemy 1.4+ exige 'postgresql://'
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI']    = _db_url or 'sqlite:///altcom365_dev.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)

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
# PHASE 2 — VISUALIZAÇÃO EM TELA
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/visualizar')
def visualizar():
    """Tela de visualização integrada — Phase 2."""
    return render_template('visualizar.html')


@app.route('/api/dados-visualizacao', methods=['GET'])
def api_dados_visualizacao():
    """
    Retorna dados completos por cliente para a tela de visualização.
    Auto-salva snapshot no PostgreSQL para cada cliente.
    """
    sess_data = _get_current_session()
    if sess_data is None:
        return jsonify({'erro': 'Sessão expirada. Faça o upload novamente.'}), 400

    df         = sess_data['df']
    versao_ref = sess_data['versao_ref']
    n_desat    = sess_data['n_desatualizadas']
    pct_desat  = sess_data['pct_desatualizadas']

    clientes   = sorted(df['NOME FANTASIA DO CLIENTE'].dropna().unique().tolist())
    resultado  = []
    snaps_buf  = []

    for cliente in clientes:
        df_cli     = df[df['NOME FANTASIA DO CLIENTE'] == cliente].copy()
        df_alertas = calcular_alertas(df_cli, versao_ref)
        df_norm    = normalize_df(df_alertas)

        resumo  = resumo_alertas(df_norm, versao_ref, n_desat, pct_desat)
        results = df_norm.apply(classify, axis=1)

        dispositivos = []
        for i, row in df_norm.iterrows():
            classif = results.loc[i, 'Classificação'] if i in results.index else ''

            alertas_ativos = []
            for col, tipo in [
                ('_alerta_armazenamento', 'armazenamento'),
                ('_alerta_windows',       'windows'),
                ('_alerta_sem_contato',   'sem_contato'),
                ('_alerta_milvus',        'milvus'),
            ]:
                val = row.get(col, '')
                if val:
                    alertas_ativos.append({'tipo': tipo, 'msg': str(val)})

            # Após normalize_df, colunas UPPERCASE viram mixed-case (COL_MAP_NOVO)
            data_at = row.get('Data de atualização', row.get('DATA DE ATUALIZAÇÃO', ''))
            if hasattr(data_at, 'strftime'):
                data_at = data_at.strftime('%d/%m/%Y %H:%M')
            elif data_at and str(data_at).lower() not in ('nan', 'nat', 'none', ''):
                data_at = str(data_at)
            else:
                data_at = '—'

            dispositivos.append({
                'hostname':       str(row.get('Nome do dispositivo', row.get('NOME DO DISPOSITIVO', '—'))),
                'classificacao':  classif,
                'versao':         str(row.get('Versão do client', row.get('VERSÃO DO CLIENT', '—'))),
                'so':             str(row.get('Sistema operacional', row.get('SISTEMA OPERACIONAL', '—'))),
                'ultimo_contato': data_at,
                'alertas':        alertas_ativos,
            })

        resultado.append({
            'nome':         cliente,
            'resumo':       resumo,
            'dispositivos': dispositivos,
        })

        # Buffer snapshot para salvar no banco
        r = resumo.get('resumo', [])
        snaps_buf.append(LaudosSnapshots(
            nome_fantasia      = cliente,
            data_processamento = datetime.utcnow(),
            total_dispositivos = resumo.get('total', 0),
            qtd_critico        = next((x['qtd'] for x in r if x['label'] == 'CRÍTICO'),       0),
            qtd_satisfatorio   = next((x['qtd'] for x in r if x['label'] == 'SATISFATÓRIO'),  0),
            qtd_bom            = next((x['qtd'] for x in r if x['label'] == 'BOM'),           0),
            qtd_otimo          = next((x['qtd'] for x in r if x['label'] == 'ÓTIMO'),         0),
            qtd_excelente      = next((x['qtd'] for x in r if x['label'] == 'EXCELENTE'),     0),
            qtd_alertas        = resumo.get('alertas', {}),
        ))

    # Salva todos os snapshots de uma vez
    try:
        for snap in snaps_buf:
            db.session.add(snap)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f'Snapshots não salvos: {e}')

    return jsonify({
        'clientes':    resultado,
        'versao_ref':  versao_ref,
        'total_geral': len(df),
    })


@app.route('/api/snapshots/<path:nome_fantasia>', methods=['GET'])
def api_snapshots(nome_fantasia):
    """Histórico de snapshots de um cliente (últimos 20)."""
    try:
        snaps = (LaudosSnapshots.query
                 .filter_by(nome_fantasia=nome_fantasia)
                 .order_by(LaudosSnapshots.data_processamento.desc())
                 .limit(20)
                 .all())
        return jsonify({'snapshots': [s.to_dict() for s in snaps]})
    except Exception as e:
        return jsonify({'erro': str(e)}), 500


@app.route('/baixar-laudo-unico', methods=['POST'])
def baixar_laudo_unico():
    """Download do laudo de eficiência de um único cliente."""
    data    = request.get_json(force=True, silent=True) or {}
    cliente = data.get('cliente', '')
    if not cliente:
        return jsonify({'erro': 'Cliente não informado.'}), 400

    sess_data = _get_current_session()
    if sess_data is None:
        return jsonify({'erro': 'Sessão expirada. Faça o upload novamente.'}), 400

    df         = sess_data['df']
    versao_ref = sess_data['versao_ref']
    df_cli     = df[df['NOME FANTASIA DO CLIENTE'] == cliente].copy()
    if df_cli.empty:
        return jsonify({'erro': 'Cliente não encontrado.'}), 404

    try:
        df_norm = normalize_df(calcular_alertas(df_cli, versao_ref))
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            out_path = tmp.name
        try:
            build_laudo_cliente(df_norm, out_path, cliente)
            xlsx_data = open(out_path, 'rb').read()
        finally:
            try: os.unlink(out_path)
            except: pass

        safe = ''.join(c if c.isalnum() or c in ' _-' else '_' for c in cliente)[:40]
        return send_file(io.BytesIO(xlsx_data),
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True,
                         download_name=f'Laudo_Eficiencia_{safe}.xlsx')
    except Exception as e:
        return jsonify({'erro': f'Erro ao gerar laudo: {str(e)}'}), 500


@app.route('/baixar-relatorio-unico', methods=['POST'])
def baixar_relatorio_unico():
    """Download do relatório interno de um único cliente."""
    data    = request.get_json(force=True, silent=True) or {}
    cliente = data.get('cliente', '')
    if not cliente:
        return jsonify({'erro': 'Cliente não informado.'}), 400

    sess_data = _get_current_session()
    if sess_data is None:
        return jsonify({'erro': 'Sessão expirada. Faça o upload novamente.'}), 400

    df         = sess_data['df']
    versao_ref = sess_data['versao_ref']
    df_cli     = df[df['NOME FANTASIA DO CLIENTE'] == cliente].copy()
    if df_cli.empty:
        return jsonify({'erro': 'Cliente não encontrado.'}), 404

    try:
        df_norm = normalize_df(calcular_alertas(df_cli, versao_ref))
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            out_path = tmp.name
        try:
            build_relatorio_interno(df_norm, out_path, cliente, versao_ref)
            xlsx_data = open(out_path, 'rb').read()
        finally:
            try: os.unlink(out_path)
            except: pass

        safe = ''.join(c if c.isalnum() or c in ' _-' else '_' for c in cliente)[:40]
        return send_file(io.BytesIO(xlsx_data),
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True,
                         download_name=f'Relatorio_Interno_{safe}.xlsx')
    except Exception as e:
        return jsonify({'erro': f'Erro ao gerar relatório: {str(e)}'}), 500



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


# ══════════════════════════════════════════════════════════════════════════════
# ROTAS V10 — Integração com API Milvus
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/milvus-status', methods=['GET'])
def milvus_status():
    """Informa se o token da API Milvus está configurado."""
    return jsonify({'configurado': milvus_api.token_configurado()})


@app.route('/sincronizar-milvus', methods=['POST'])
def sincronizar_milvus():
    """
    Busca os dispositivos diretamente da API Milvus.
    Aplica os mesmos filtros de /upload-completo e retorna a mesma estrutura JSON.
    """
    _clear_old_sessions()

    df, erro = milvus_api.listar_dispositivos()
    if erro:
        return jsonify({'erro': erro}), 400

    try:
        # Detecta formato (a API já entrega no formato certo)
        if not is_new_format(df):
            return jsonify({
                'erro': 'A API Milvus retornou dados em formato inesperado. '
                        'Verifique o mapeamento de campos em milvus_api.py.'
            }), 400

        # Valida colunas obrigatórias
        faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
        if faltando:
            return jsonify({'erro': f'Colunas ausentes na resposta da API: {", ".join(faltando)}'}), 400

        # Mesmos filtros automáticos de /upload-completo
        df = df[df['EXCLUÍDO'].astype(str).str.upper() != 'SIM']
        df = df[~df['NOME FANTASIA DO CLIENTE'].astype(str).str.lower()
                  .str.contains('altcom', na=False)]
        # Remove servidores
        df = df[~df['PROCESSADOR'].astype(str).str.contains('Xeon', case=False, na=False)]
        if 'SERVIDOR' in df.columns:
            df = df[df['SERVIDOR'].astype(str).str.upper() != 'SIM']
        df = df[df['TIPO DO DISPOSITIVO'].astype(str).str.lower() != 'servidor']
        df = df.reset_index(drop=True)

        total = len(df)
        if total == 0:
            return jsonify({'erro': 'Nenhum dispositivo ativo encontrado após filtros.'}), 400

        # Calcula versão de referência do agente
        versao_ref, n_desat, pct_desat = calcular_versao_referencia(df)
        tem_data_at = 'DATA DE ATUALIZAÇÃO' in df.columns

        # Lista de clientes
        clientes = sorted(df['NOME FANTASIA DO CLIENTE'].dropna().unique().tolist())
        contagem = df['NOME FANTASIA DO CLIENTE'].value_counts().to_dict()

        # Salva sessão (reutiliza o mesmo mecanismo do upload)
        sess_data = {
            'df':               df,
            'versao_ref':       versao_ref,
            'n_desatualizadas': n_desat,
            'pct_desatualizadas': pct_desat,
            'tem_data_at':      tem_data_at,
            'timestamp':        datetime.now(),
            'fonte':            'api',
        }
        sid = _save_session(sess_data)
        session['sess_id'] = sid

        return jsonify({
            'clientes':           clientes,
            'contagem':           contagem,
            'total_dispositivos': total,
            'tem_data_at':        tem_data_at,
            'versao_ref':         versao_ref,
            'n_desatualizadas':   n_desat,
            'pct_desatualizadas': pct_desat,
            'fonte':              'api',
        })

    except Exception as e:
        return jsonify({'erro': f'Erro ao processar dados da API: {str(e)}'}), 500


# ── V11: Rotas de sincronização de mapeamento ─────────────────────────────────

@app.route('/sync-status', methods=['GET'])
def sync_status():
    """
    Retorna o status atual dos mapeamentos Milvus gravados no banco.
    Exibido na interface como "IDs sincronizados há X horas · N dispositivos mapeados".
    """
    n_clientes     = ClientesMap.query.count()
    n_dispositivos = DispositivosMap.query.count()

    ultima_disp = (
        db.session.query(db.func.max(DispositivosMap.ultima_sync)).scalar()
    )
    ultima_cli = (
        db.session.query(db.func.max(ClientesMap.ultima_sync)).scalar()
    )

    def _horas(dt):
        if not dt:
            return None
        diff = datetime.utcnow() - dt
        return round(diff.total_seconds() / 3600, 1)

    return jsonify({
        'clientes':     {'total': n_clientes,     'ultima_sync_h': _horas(ultima_cli)},
        'dispositivos': {'total': n_dispositivos, 'ultima_sync_h': _horas(ultima_disp)},
    })


@app.route('/sync-clientes', methods=['POST'])
def sync_clientes():
    """
    Sincroniza a tabela clientes_map com GET /api/cliente/busca do Milvus.
    1 chamada — retorna lista de clientes com cliente_id e token.
    Necessário para criar chamados (usa cliente_id + token por cliente).
    """
    mtoken = os.environ.get('MILVUS_API_TOKEN')
    if not mtoken:
        return jsonify({'erro': 'MILVUS_API_TOKEN nao configurado.'}), 400

    try:
        import requests as req
        resp = req.get(
            'https://apiintegracao.milvus.com.br/api/cliente/busca',
            headers={'Authorization': mtoken, 'Content-Type': 'application/json'},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.exception('sync_clientes: erro na chamada Milvus')
        return jsonify({'erro': f'Falha ao buscar clientes: {exc}'}), 500

    lista = data if isinstance(data, list) else data.get('lista', data.get('data', []))
    agora = datetime.utcnow()
    atualizados = 0

    for item in lista:
        nome = (item.get('nome_fantasia') or item.get('razao_social') or '').strip()
        if not nome:
            continue
        cid  = item.get('id') or item.get('cliente_id')
        ctok = item.get('token') or item.get('api_token') or ''
        entry = ClientesMap.query.filter_by(nome_fantasia=nome).first()
        if entry:
            entry.milvus_cliente_id = cid
            entry.milvus_token      = ctok
            entry.ultima_sync       = agora
        else:
            entry = ClientesMap(
                nome_fantasia=nome,
                milvus_cliente_id=cid,
                milvus_token=ctok,
                ultima_sync=agora,
            )
            db.session.add(entry)
        atualizados += 1
    db.session.commit()

    logger.info('sync_clientes: %d clientes sincronizados', atualizados)
    return jsonify({'ok': True, 'clientes_sincronizados': atualizados})


@app.route('/sync-ids', methods=['POST'])
def sync_ids():
    """
    Sincroniza a tabela dispositivos_map via POST /api/dispositivos/listagem.
    Uma única chamada com total_registros=1000 traz todo o parque (~470 dispositivos).
    Executa de forma síncrona — sem background thread, sem problema de multi-worker.
    """
    import requests as req

    mtoken = os.environ.get('MILVUS_API_TOKEN')
    if not mtoken:
        return jsonify({'ok': False, 'erro': 'MILVUS_API_TOKEN nao configurado.'}), 400

    try:
        resp = req.post(
            'https://apiintegracao.milvus.com.br/api/dispositivos/listagem',
            json={'is_paginate': True, 'total_registros': 1000, 'pagina': 1},
            headers={'Authorization': mtoken, 'Content-Type': 'application/json'},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return jsonify({'ok': False, 'erro': str(e)}), 500

    lista    = data.get('lista', [])
    agora    = datetime.utcnow()
    mapeados = 0

    for item in lista:
        host = (item.get('hostname') or '').strip()
        nome = (item.get('nome_fantasia') or '').strip()
        if not host or not nome:
            continue
        did   = item.get('id') or item.get('dispositivo_id')
        ativo = item.get('is_ativo', True)
        entry = DispositivosMap.query.filter_by(hostname=host, nome_fantasia=nome).first()
        if entry:
            entry.milvus_dispositivo_id = did
            entry.is_ativo    = ativo
            entry.ultima_sync = agora
        else:
            db.session.add(DispositivosMap(
                hostname=host, nome_fantasia=nome,
                milvus_dispositivo_id=did,
                is_ativo=ativo, ultima_sync=agora,
            ))
        mapeados += 1

    db.session.commit()
    logger.info('sync_ids: %d dispositivos mapeados', mapeados)

    return jsonify({
        'ok':            True,
        'total_mapeados': mapeados,
        'ultima_sync':   agora.isoformat(),
    })


@app.route('/sync-ids/status', methods=['GET'])
def sync_ids_status():
    """Retorna contagem atual de IDs mapeados no banco."""
    n     = DispositivosMap.query.count()
    ultima = db.session.query(db.func.max(DispositivosMap.ultima_sync)).scalar()
    return jsonify({
        'status':         'idle',
        'total_mapeados': n,
        'ultima_sync':    ultima.isoformat() if ultima else None,
    })


@app.route('/api/push-milvus', methods=['POST'])
def api_push_milvus():
    """
    Phase 3 — Dispara push de atualização do agente Milvus para os dispositivos
    desatualizados (versão != versao_ref) de um cliente específico.

    Body JSON: {"cliente": "Nome Fantasia Do Cliente"}
    """
    data_req  = request.get_json(silent=True) or {}
    nome_cli  = (data_req.get('cliente') or '').strip()

    if not nome_cli:
        return jsonify({'ok': False, 'erro': 'Nome do cliente não informado.'}), 400

    sess_data = _get_current_session()
    if sess_data is None:
        return jsonify({'ok': False, 'erro': 'Sessão expirada. Faça upload novamente.'}), 400

    df         = sess_data['df']
    versao_ref = sess_data['versao_ref']

    df_cli = df[df['NOME FANTASIA DO CLIENTE'] == nome_cli].copy()
    if df_cli.empty:
        return jsonify({'ok': False, 'erro': f'Cliente "{nome_cli}" não encontrado na sessão.'}), 404

    # Identificar desatualizados via _alerta_milvus
    df_alertas = calcular_alertas(df_cli, versao_ref)
    mask_desat = df_alertas.get('_alerta_milvus', pd.Series(dtype=str)).fillna('').str.len() > 0
    df_desat   = df_alertas[mask_desat]

    if df_desat.empty:
        return jsonify({
            'ok':    True,
            'msg':   'Todos os dispositivos já estão na versão de referência.',
            'total': 0,
        })

    hostnames = df_desat['NOME DO DISPOSITIVO'].dropna().tolist()

    # Buscar IDs Milvus no banco
    milvus_ids   = []
    nao_mapeados = []
    for host in hostnames:
        entry = DispositivosMap.query.filter_by(
            hostname=host.strip(), nome_fantasia=nome_cli
        ).first()
        if entry and entry.milvus_dispositivo_id:
            milvus_ids.append(entry.milvus_dispositivo_id)
        else:
            nao_mapeados.append(host)

    if not milvus_ids:
        return jsonify({
            'ok':           False,
            'erro':         ('Nenhum dispositivo mapeado no banco. '
                             'Execute "Sincronizar IDs" e tente novamente.'),
            'nao_mapeados': nao_mapeados,
            'total_desat':  len(hostnames),
        }), 400

    # Chamar API Milvus
    ok, msg, detalhes = milvus_api.push_atualizacao(milvus_ids)
    logger.info('push-milvus: cliente=%s | ids=%s | ok=%s | msg=%s',
                nome_cli, milvus_ids, ok, msg)

    return jsonify({
        'ok':            ok,
        'msg':           msg,
        'total_enviado': len(milvus_ids),
        'nao_mapeados':  nao_mapeados,
        'detalhes_api':  detalhes,
    }), 200 if ok else 502


# ── APScheduler — job diário de sync de IDs ──────────────────────────────────
@app.route('/download/relatorio-consolidado', methods=['GET'])
def download_relatorio_consolidado():
    """
    Gera um único arquivo Excel com todos os Relatórios Internos da sessão atual,
    uma aba por cliente (pandas ExcelWriter — robusto, sem cópia de cells).
    """
    try:
        sess_data = _get_current_session()
        if sess_data is None:
            return jsonify({'erro': 'Sessão expirada. Faça o upload novamente.'}), 400

        df         = sess_data['df']
        versao_ref = sess_data['versao_ref']
        clientes   = sorted(df['NOME FANTASIA DO CLIENTE'].dropna().unique().tolist())

        if not clientes:
            return jsonify({'erro': 'Nenhum cliente encontrado na sessão.'}), 400

        # Mapear colunas de exibição (igual ao relatório interno)
        COLUNAS_EXIBIR = {
            'hostname':                    'Hostname',
            'USUÁRIO LOGADO':              'Usuário',
            'Versão do client':            'Versão Agente',
            'Data de atualização':         'Última Atualização',
            'ARMAZENAMENTO INTERNO TOTAL': 'HD Total (GB)',
            'ARMAZENAMENTO INTERNO UTILIZADO': 'HD Usado (GB)',
            'uso_pct':                     'HD Uso %',
            'Sistema operacional':         'S.O.',
            'Modelo do computador':        'Modelo',
            '_alerta_versao':              'Alerta: Versão',
            '_alerta_armazenamento':       'Alerta: Armazenamento',
            '_alerta_windows':             'Alerta: Windows',
            '_alerta_tempo_sem_uso':       'Alerta: Sem uso',
        }

        buf = io.BytesIO()
        errors_por_cliente = []

        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            sheets_criadas = 0
            for cliente in clientes:
                try:
                    df_cli = df[df['NOME FANTASIA DO CLIENTE'] == cliente].copy()
                    if df_cli.empty:
                        continue

                    df_alertas = calcular_alertas(df_cli, versao_ref)
                    df_norm    = normalize_df(df_alertas)

                    # Filtrar apenas dispositivos com pelo menos 1 alerta
                    if '_tem_alerta' in df_norm.columns:
                        df_export = df_norm[df_norm['_tem_alerta'] == True].copy()
                    else:
                        df_export = df_norm.copy()

                    # Selecionar e renomear colunas disponíveis
                    cols_presentes = {k: v for k, v in COLUNAS_EXIBIR.items()
                                      if k in df_export.columns}
                    df_out = df_export[list(cols_presentes.keys())].rename(
                        columns=cols_presentes
                    )

                    # Se não há coluna de versão, usar a primeira coluna disponível
                    if df_out.empty:
                        # Inclui todos mesmo sem alerta (cliente sem problemas)
                        df_out = df_export[[c for c in list(cols_presentes.keys())
                                            if c in df_export.columns]]
                        df_out = df_out.rename(columns=cols_presentes)

                    # Nome da aba: max 31 chars, remover chars inválidos
                    sheet_name = ''.join(c for c in cliente
                                         if c not in r'/\*?:[]')[:31].strip()
                    if not sheet_name:
                        sheet_name = f'Cliente_{sheets_criadas+1}'

                    df_out.to_excel(writer, sheet_name=sheet_name, index=False)
                    sheets_criadas += 1

                except Exception as cli_err:
                    logger.warning('consolidado: erro cliente %s: %s', cliente, cli_err)
                    errors_por_cliente.append(f'{cliente}: {cli_err}')

        if sheets_criadas == 0:
            err_msg = 'Nenhuma aba gerada.'
            if errors_por_cliente:
                err_msg += ' Erros: ' + ' | '.join(errors_por_cliente[:3])
            return jsonify({'erro': err_msg}), 400

        buf.seek(0)
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='Relatorio_Consolidado_Altcom365.xlsx',
        )

    except Exception as e:
        logger.exception('download_relatorio_consolidado: erro inesperado')
        return jsonify({'erro': f'Erro inesperado: {str(e)}'}), 500


def _job_sync_ids_diario():
    """Executado às 02:00 diariamente para manter DispositivosMap atualizado."""
    import requests as _req
    import time as _t
    token = os.environ.get('MILVUS_API_TOKEN')
    if not token:
        logger.warning('job_sync_ids: MILVUS_API_TOKEN não configurado — pulando')
        return
    with app.app_context():
        from models import db as _db, DispositivosMap as _DM
        try:
            pagina = 1
            agora  = datetime.utcnow()
            while True:
                resp = _req.post(
                    'https://apiintegracao.milvus.com.br/api/dispositivos/listagem',
                    json={'is_paginate': True, 'total_registros': 1000, 'pagina': 1},
                    headers={'Authorization': token, 'Content-Type': 'application/json'},
                    timeout=30,
                )
                resp.raise_for_status()
                data    = resp.json()
                lista   = data.get('lista', [])
                last_pg = int(data.get('meta', {}).get('paginate', {}).get('last_page', 1))
                for item in lista:
                    host = (item.get('hostname') or '').strip()
                    nome = (item.get('nome_fantasia') or '').strip()
                    if not host or not nome:
                        continue
                    did   = item.get('id') or item.get('dispositivo_id')
                    ativo = item.get('is_ativo', True)
                    entry = _DM.query.filter_by(hostname=host, nome_fantasia=nome).first()
                    if entry:
                        entry.milvus_dispositivo_id = did
                        entry.is_ativo    = ativo
                        entry.ultima_sync = agora
                    else:
                        _db.session.add(_DM(
                            hostname=host, nome_fantasia=nome,
                            milvus_dispositivo_id=did,
                            is_ativo=ativo, ultima_sync=agora,
                        ))
                _db.session.commit()
                break  # total_registros=1000 traz tudo em 1 chamada — sem paginação
            logger.info('job_sync_ids: concluído — %d dispositivos', len(lista))
        except Exception:
            logger.exception('job_sync_ids: erro durante execução diária')

try:
    _scheduler = BackgroundScheduler(timezone='America/Sao_Paulo')
    _scheduler.add_job(_job_sync_ids_diario, 'cron', hour=2, minute=0,
                       id='sync_ids_diario', replace_existing=True)
    _scheduler.start()
    logger.info('APScheduler iniciado — sync_ids agendado para 02:00 BRT')
except Exception as _sch_err:
    logger.warning('APScheduler não iniciado: %s', _sch_err)


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)


