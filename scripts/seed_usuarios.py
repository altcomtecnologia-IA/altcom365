#!/usr/bin/env python3
"""
scripts/seed_usuarios.py
Upsert idempotente da tabela `usuario` (portal/models.py) a partir de um
arquivo local — NUNCA de um literal neste arquivo. A lista real de
e-mail/papel não entra no repositório em nenhuma forma (ver
.gitignore: scripts/*.local.csv).

Uso:
    DATABASE_URL=postgresql://... python scripts/seed_usuarios.py CAMINHO_DO_ARQUIVO

Formato do arquivo, uma pessoa por linha, campos separados por ';' ou ':'
(tolera os dois — colado de cliente de e-mail vem inconsistente):

    email;papel[;nome]

papel ∈ {n1n2, n3, gestor} (mesmo CHECK de ck_usuario_papel). Linhas em
branco e iniciadas com '#' são ignoradas. Tolera e-mail vindo como link
markdown ([texto](mailto:endereco)) — extrai só o endereço.

Comportamento (decidido deliberadamente, não obviedade):
  - TUDO validado antes de tocar o banco. Uma linha inválida aborta o
    script inteiro, sem escrever nada — nunca um seed parcial por causa
    de um typo na linha 30.
  - E-mail novo: INSERT com papel, nome (se informado) e ativo=true.
  - E-mail já existente: UPDATE só de papel e nome. NUNCA mexe em
    `ativo` — se alguém foi desativado de propósito (desligamento, papel
    sendo revisado), rodar este script de novo por causa de uma pessoa
    nova não pode reativar essa conta em silêncio. Reativar é uma ação
    deliberada, feita à parte.
  - Idempotente: rodar duas vezes com o mesmo arquivo não duplica nem
    falha — a segunda vez só confirma que já está tudo como deveria.
  - Uma transação só. Todos os upserts committed juntos, ou nenhum.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

PAPEIS_VALIDOS = {'n1n2', 'n3', 'gestor'}

# O e-mail vem como texto puro OU como link markdown '[texto](mailto:x)'
# (comum quando a lista é colada de um cliente de e-mail/chat que
# autolinka). O mailto: em si tem ':' dentro — por isso este padrão é
# casado ANTES de qualquer split por ';'/':' na linha inteira, e só o
# restante da linha (papel[;nome]) é que é dividido por separador solto.
_MARKDOWN_LINK_NO_INICIO = re.compile(r'^\[([^\]]+)\]\(mailto:([^)]+)\)\s*[;:]\s*(.*)$')


def _parse_linha(linha: str, numero: int):
    """Retorna (email, papel, nome|None) ou levanta ValueError com o motivo."""
    bruta = linha.rstrip('\n').strip()
    if not bruta or bruta.startswith('#'):
        return None

    m = _MARKDOWN_LINK_NO_INICIO.match(bruta)
    if m:
        email = m.group(2).strip().lower()
        resto = m.group(3)
    else:
        primeiro_sep = re.search(r'[;:]', bruta)
        if not primeiro_sep:
            raise ValueError(
                f"linha {numero}: esperado 'email;papel[;nome]', achei {bruta!r}"
            )
        email = bruta[:primeiro_sep.start()].strip().lower()
        resto = bruta[primeiro_sep.end():]

    if '@' not in email:
        raise ValueError(f"linha {numero}: {email!r} não parece e-mail")

    campos_resto = [p.strip() for p in re.split(r'[;:]', resto)]
    papel = campos_resto[0].lower() if campos_resto else ''
    nome = campos_resto[1] if len(campos_resto) >= 2 and campos_resto[1] else None

    if papel not in PAPEIS_VALIDOS:
        raise ValueError(
            f"linha {numero}: papel {papel!r} inválido — "
            f"tem que ser um de {sorted(PAPEIS_VALIDOS)}"
        )

    return email, papel, nome


def parse_arquivo(caminho: str):
    """Lê e valida o arquivo inteiro antes de qualquer escrita no banco."""
    linhas = []
    with open(caminho, encoding='utf-8') as f:
        for numero, linha in enumerate(f, start=1):
            resultado = _parse_linha(linha, numero)
            if resultado is not None:
                linhas.append(resultado)

    if not linhas:
        raise ValueError(f"{caminho}: nenhuma linha válida encontrada")

    emails_vistos = {}
    for email, papel, nome in linhas:
        if email in emails_vistos:
            raise ValueError(f"e-mail {email!r} aparece mais de uma vez no arquivo")
        emails_vistos[email] = True

    return linhas


def _montar_app():
    from flask import Flask
    from extensoes import db, normalizar_database_url

    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        raise SystemExit(
            "DATABASE_URL não definida. Exemplo:\n"
            "  DATABASE_URL=postgresql://... python scripts/seed_usuarios.py ARQUIVO"
        )

    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = normalizar_database_url(db_url)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    return app, db


def seed(caminho: str):
    linhas = parse_arquivo(caminho)
    print(f"{len(linhas)} linha(s) válida(s) em {caminho}.")

    app, db = _montar_app()
    from portal.models import Usuario

    criados, atualizados, sem_mudanca = [], [], []

    with app.app_context():
        for email, papel, nome in linhas:
            usuario = Usuario.query.filter_by(email=email).first()
            if usuario is None:
                usuario = Usuario(email=email, papel=papel, nome=nome, ativo=True)
                db.session.add(usuario)
                criados.append(email)
            else:
                mudou = usuario.papel != papel or (nome and usuario.nome != nome)
                usuario.papel = papel
                if nome:
                    usuario.nome = nome
                # ativo: deliberadamente não tocado — ver docstring do módulo.
                (atualizados if mudou else sem_mudanca).append(email)

        db.session.commit()

    print(f"Criados:        {len(criados)}" + (f" ({', '.join(criados)})" if criados else ""))
    print(f"Atualizados:    {len(atualizados)}" + (f" ({', '.join(atualizados)})" if atualizados else ""))
    print(f"Sem mudança:    {len(sem_mudanca)}" + (f" ({', '.join(sem_mudanca)})" if sem_mudanca else ""))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f"Uso: {sys.argv[0]} CAMINHO_DO_ARQUIVO", file=sys.stderr)
        sys.exit(1)
    seed(sys.argv[1])
