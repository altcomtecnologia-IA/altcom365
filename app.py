"""
Altcom 365 – Gerador de Laudo de Eficiência Técnica
Backend Flask – versão standalone com autenticação
"""
import os, sys, io, tempfile
from flask import Flask, request, send_file, jsonify, render_template, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash

sys.path.insert(0, os.path.dirname(__file__))
from engine_altcom365 import classify, BADGE_COLORS
from build_laudo import build_laudo

import pandas as pd

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB
app.secret_key = os.environ.get('SECRET_KEY', 'altcom365-dev-key-mude-em-producao')

# ── FLASK-LOGIN ───────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Faca login para acessar o sistema.'

@login_manager.unauthorized_handler
def unauthorized():
    """Retorna JSON para chamadas AJAX, redirect para navegação normal."""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'erro': 'Sessão expirada. Recarregue a página e faça login novamente.', 'auth': False}), 401
    return redirect(url_for('login'))

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

def get_users():
    raw = os.environ.get('USERS', '')
    users = {}
    for i, entry in enumerate(raw.split(','), start=1):
        entry = entry.strip()
        if ':' in entry:
            username, pw_hash = entry.split(':', 1)
            users[username.strip()] = {'id': str(i), 'hash': pw_hash.strip()}
    return users

@login_manager.user_loader
def load_user(user_id):
    users = get_users()
    for username, data in users.items():
        if data['id'] == user_id:
            return User(user_id, username)
    return None

# ── AUTH ROUTES ───────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        users = get_users()

        if username in users and check_password_hash(users[username]['hash'], password):
            user = User(users[username]['id'], username)
            login_user(user, remember=True)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))

        error = 'Usuario ou senha incorretos.'

    return render_template('login.html', error=error)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ── APP ROUTES ────────────────────────────────────────────────────────────────
ALLOWED_EXT = {'.xlsx', '.xls'}

def allowed_file(filename):
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXT

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=current_user.username)

@app.route('/gerar', methods=['POST'])
@login_required
def gerar():
    if 'arquivo' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado.'}), 400

    f = request.files['arquivo']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'erro': 'Formato invalido. Envie um arquivo .xlsx'}), 400

    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp_in:
        f.save(tmp_in.name)
        input_path = tmp_in.name

    output_path = None
    try:
        df = pd.read_excel(input_path)
        colunas_req = ['Processador', 'Memória RAM total', 'Armazenamento total',
                       'Armazenamento utilizado', 'Sistema operacional', 'Nome do dispositivo']
        faltando = [c for c in colunas_req if c not in df.columns]
        if faltando:
            return jsonify({'erro': 'Colunas nao encontradas: ' + ', '.join(faltando)}), 400

        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp_out:
            output_path = tmp_out.name

        build_laudo(input_path, output_path)

        if 'Cliente' in df.columns:
            cliente = str(df['Cliente'].iloc[0]).strip()
        else:
            cliente = 'Cliente'
        nome_arquivo = 'Laudo_Eficiencia_' + cliente.replace(' ', '_') + '.xlsx'

        with open(output_path, 'rb') as fout:
            data = fout.read()

        return send_file(
            io.BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=nome_arquivo
        )

    except Exception as e:
        return jsonify({'erro': 'Erro ao processar: ' + str(e)}), 500

    finally:
        for p in [input_path, output_path]:
            if p:
                try:
                    os.unlink(p)
                except Exception:
                    pass

@app.route('/preview', methods=['POST'])
@login_required
def preview():
    if 'arquivo' not in request.files:
        return jsonify({'erro': 'Nenhum arquivo enviado.'}), 400

    f = request.files['arquivo']
    if not f.filename or not allowed_file(f.filename):
        return jsonify({'erro': 'Formato invalido.'}), 400

    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        f.save(tmp.name)
        path = tmp.name

    try:
        df = pd.read_excel(path)
        colunas_req = ['Processador', 'Memória RAM total', 'Armazenamento total',
                       'Armazenamento utilizado', 'Sistema operacional', 'Nome do dispositivo']
        faltando = [c for c in colunas_req if c not in df.columns]
        if faltando:
            return jsonify({'erro': 'Colunas nao encontradas: ' + ', '.join(faltando)}), 400

        results = df.apply(classify, axis=1)
        df_out = pd.concat([df, results], axis=1)

        if 'Cliente' in df.columns:
            cliente = str(df['Cliente'].iloc[0]).strip()
        else:
            cliente = 'Cliente'
        total = len(df_out)

        order = ['EXCELENTE', 'ÓTIMO', 'BOM', 'SATISFATÓRIO', 'CRÍTICO']
        resumo = []
        for cat in order:
            qtd = int((df_out['Classificação'] == cat).sum())
            if qtd == 0:
                continue
            mp = int(df_out[df_out['Badge'].str.contains('Man. Prev.') & (df_out['Classificação'] == cat)].shape[0])
            up = int(df_out[df_out['Badge'].str.contains('Upgrade') & (df_out['Classificação'] == cat)].shape[0])
            bg, fg = BADGE_COLORS[cat]
            resumo.append({'label': cat, 'qtd': qtd, 'pct': round(qtd / total * 100),
                           'man_prev': mp, 'upgrade': up, 'bg': bg, 'fg': fg})

        return jsonify({'cliente': cliente, 'total': total, 'resumo': resumo})

    except Exception as e:
        return jsonify({'erro': str(e)}), 500
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
