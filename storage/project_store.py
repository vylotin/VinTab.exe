import sqlite3
from contextlib import closing
from pathlib import Path


def listar_conjuntos(caminho_banco):
    caminho = Path(caminho_banco).resolve()
    uri = caminho.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conexao:
        resultados = conexao.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    return [resultado[0] for resultado in resultados]


def ler_conjunto(caminho_banco, nome_conjunto):
    caminho = Path(caminho_banco).resolve()
    uri = caminho.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conexao:
        colunas = _obter_colunas(conexao, nome_conjunto)
        linhas = conexao.execute(
            f"SELECT * FROM {_identificador(nome_conjunto)}"
        ).fetchall()
    return colunas, linhas


def criar_conjunto(caminho_projeto, nome_conjunto, colunas):
    caminho = _validar_projeto(caminho_projeto)
    nome = _validar_nome(nome_conjunto, "nome do conjunto")
    colunas = _validar_colunas(colunas)
    definicoes = ", ".join(f"{_identificador(coluna)} NUMERIC" for coluna in colunas)

    with closing(sqlite3.connect(caminho)) as conexao:
        with conexao:
            conexao.execute(
                f"CREATE TABLE {_identificador(nome)} ({definicoes})"
            )


def salvar_conjunto(caminho_projeto, nome_conjunto, colunas, linhas):
    caminho = _validar_projeto(caminho_projeto)
    nome = _validar_nome(nome_conjunto, "nome do conjunto")
    colunas = _validar_colunas(colunas)
    tabela_sql = _identificador(nome)
    colunas_existentes = _ler_colunas_projeto(caminho, nome)
    if colunas_existentes != colunas:
        raise ValueError("As colunas do conjunto foram alteradas durante a edição.")

    linhas_validas = []
    for linha in linhas:
        if len(linha) != len(colunas):
            raise ValueError("Uma linha contém quantidade incorreta de valores.")
        valores = [
            None if valor is None or not str(valor).strip() else str(valor)
            for valor in linha
        ]
        if any(valor not in (None, "") for valor in valores):
            linhas_validas.append(valores)

    marcadores = ", ".join("?" for _ in colunas)
    nomes_colunas = ", ".join(_identificador(coluna) for coluna in colunas)
    with closing(sqlite3.connect(caminho)) as conexao:
        with conexao:
            conexao.execute(f"DELETE FROM {tabela_sql}")
            if linhas_validas:
                conexao.executemany(
                    f"INSERT INTO {tabela_sql} ({nomes_colunas}) VALUES ({marcadores})",
                    linhas_validas,
                )


def _ler_colunas_projeto(caminho_projeto, nome_conjunto):
    with closing(sqlite3.connect(caminho_projeto)) as conexao:
        return _obter_colunas(conexao, nome_conjunto)


def _obter_colunas(conexao, nome_conjunto):
    nome = _validar_nome(nome_conjunto, "nome do conjunto")
    resultado = conexao.execute(
        f"PRAGMA table_info({_identificador(nome)})"
    ).fetchall()
    if not resultado:
        raise ValueError(f"Conjunto não encontrado: {nome}")
    return [coluna[1] for coluna in resultado]


def _validar_projeto(caminho_projeto):
    caminho = Path(caminho_projeto).resolve()
    if caminho.suffix.lower() != ".vt":
        raise ValueError("A edição de dados está disponível somente em projetos .vt.")
    if not caminho.is_file():
        raise FileNotFoundError(f"Projeto não encontrado: {caminho}")
    return caminho


def _validar_colunas(colunas):
    nomes = [_validar_nome(coluna, "nome da coluna") for coluna in colunas]
    if not nomes:
        raise ValueError("Informe pelo menos uma coluna.")
    if len({nome.casefold() for nome in nomes}) != len(nomes):
        raise ValueError("Os nomes das colunas não podem se repetir.")
    return nomes


def _validar_nome(valor, descricao):
    nome = str(valor).strip()
    if not nome or "\x00" in nome:
        raise ValueError(f"Informe um {descricao} válido.")
    if nome.casefold().startswith("sqlite_"):
        raise ValueError("Nomes iniciados por 'sqlite_' são reservados pelo SQLite.")
    return nome


def _identificador(valor):
    return '"' + valor.replace('"', '""') + '"'