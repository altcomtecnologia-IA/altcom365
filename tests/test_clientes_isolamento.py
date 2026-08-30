"""
tests/test_clientes_isolamento.py
Regra executável, não convenção (Decisão 1, complemento A — passo 3):
o pacote clientes/ nunca lê g.identidade, o atributo populado pelo
before_request compartilhado com o Laudos em altcom_auth/middleware.py.

Por quê como AST e não como busca de substring: "g.identidade" é
literalmente um prefixo de "g.identidade_clientes" (o atributo que
clientes/auth.py usa de propósito, ver seu docstring). Um grep ingênuo por
essa string acusaria falso positivo em toda linha que já está correta.
Só um parse de verdade — procurando um ast.Attribute com attr == 'identidade'
cujo value seja exatamente o nome 'g' — distingue os dois casos.

Este teste não depende de banco nem de rede: só lê os arquivos-fonte.
"""
import ast
from pathlib import Path

CLIENTES_DIR = Path(__file__).parent.parent / 'clientes'


def _arquivos_python():
    return sorted(CLIENTES_DIR.rglob('*.py'))


def _usa_g_identidade(caminho: Path):
    """
    Retorna a lista de números de linha onde o arquivo acessa `g.identidade`
    (atributo exato — não `g.identidade_clientes` nem qualquer outro nome
    que apenas comece com 'identidade').
    """
    fonte = caminho.read_text(encoding='utf-8')
    arvore = ast.parse(fonte, filename=str(caminho))
    ocorrencias = []
    for node in ast.walk(arvore):
        if (isinstance(node, ast.Attribute)
                and node.attr == 'identidade'
                and isinstance(node.value, ast.Name)
                and node.value.id == 'g'):
            ocorrencias.append(node.lineno)
    return ocorrencias


def test_pacote_clientes_existe_e_tem_arquivos():
    """Guarda contra o teste passar vazio por engano (diretório errado, etc.)."""
    arquivos = _arquivos_python()
    assert len(arquivos) > 0, f"nenhum .py encontrado em {CLIENTES_DIR}"


def test_clientes_nunca_le_g_identidade():
    violacoes = {}
    for caminho in _arquivos_python():
        linhas = _usa_g_identidade(caminho)
        if linhas:
            violacoes[str(caminho.relative_to(CLIENTES_DIR.parent))] = linhas

    assert not violacoes, (
        "clientes/ deve ser 100% independente de g.identidade (populado "
        "por altcom_auth/middleware.py, que hoje carrega o bug conhecido "
        "_GRUPO_PADRAO — ver ISSUE_GRUPO_PADRAO.md). Use "
        "clientes.auth.resolver_identidade() / g.identidade_clientes. "
        f"Violações encontradas: {violacoes}"
    )


def test_deteccao_nao_falso_positivo_em_identidade_clientes():
    """
    Prova que o teste acima não acusaria g.identidade_clientes por engano —
    se acusasse, o teste principal seria inútil (sempre vermelho).
    """
    trecho = "g.identidade_clientes = {}\n"
    arvore = ast.parse(trecho)
    ocorrencias = [
        node.lineno for node in ast.walk(arvore)
        if (isinstance(node, ast.Attribute) and node.attr == 'identidade'
            and isinstance(node.value, ast.Name) and node.value.id == 'g')
    ]
    assert ocorrencias == []
