import pandas as pd
import sqlite3

# 1. Cria a base de dados SQLite (o ficheiro é criado automaticamente)
conn = sqlite3.connect('base_vintab.db')

# 2. Lê o ficheiro Excel de uma só vez (todas as abas)
# Substitua 'seu_ficheiro_vintab.xlsx' pelo nome real do seu ficheiro
excel_multiabas = pd.read_excel('Base_CCIH_Atualizada.xlsx', sheet_name=None)

# 3. Transforma cada aba numa tabela dentro da base de dados
for nome_aba, dados in excel_multiabas.items():
    # Limpa nomes de abas que tenham espaços para evitar erros no SQL
    nome_tabela_limpo = nome_aba.replace(" ", "_").lower() 
    dados.to_sql(nome_tabela_limpo, conn, index=False, if_exists='replace')

print("Sucesso! Base de dados criada com todas as tabelas.")