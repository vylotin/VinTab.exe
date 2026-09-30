# data_layer.py
import os
import shutil
import pandas as pd
from io import BytesIO
from datetime import datetime

def carregar_excel(conteudo_bytes: bytes) -> dict[str, pd.DataFrame]:
    with pd.ExcelFile(BytesIO(conteudo_bytes)) as xls:
        return {aba: xls.parse(aba) for aba in xls.sheet_names}

def ler_arquivo(fonte) -> tuple[dict[str, pd.DataFrame], bytes]:
    if isinstance(fonte, str):
        with open(fonte, "rb") as f:
            raw = f.read()
    else:
        raw = fonte.read()
        fonte.seek(0)
    return carregar_excel(raw), raw

def realizar_backup_seguranca(path_origem: str):
    """Cria uma cópia de segurança antes de qualquer sobrescrição para evitar perda total de dados."""
    if os.path.exists(path_origem):
        pasta_backup = "backups_ccih"
        os.makedirs(pasta_backup, exist_ok=True)
        
        # Gera um nome único com a data e hora exata da alteração
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_arquivo = os.path.basename(path_origem)
        path_destino = os.path.join(pasta_backup, f"backup_{timestamp}_{nome_arquivo}")
        
        try:
            shutil.copy2(path_origem, path_destino)
            
            # Limpeza automática: Mantém apenas os 30 backups mais recentes para não lotar o HD
            backups_existentes = sorted(
                [os.path.join(pasta_backup, f) for f in os.listdir(pasta_backup)],
                key=os.path.getctime
            )
            while len(backups_existentes) > 30:
                arquivo_antigo = backups_existentes.pop(0)
                os.remove(arquivo_antigo)
                
        except Exception as e:
            print(f"Aviso: Falha ao gerar backup de segurança: {e}")

def salvar_excel_multiaba(path_destino: str, df_atualizado: pd.DataFrame, aba_ativa: str, abas_dict: dict):
    realizar_backup_seguranca(path_destino)
    
    if os.path.exists(path_destino):
        with pd.ExcelWriter(path_destino, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df_atualizado.to_excel(writer, sheet_name=aba_ativa, index=False)
    else:
        with pd.ExcelWriter(path_destino, engine="openpyxl") as writer:
            for aba, df_aba in abas_dict.items():
                df_salvar = df_atualizado if aba == aba_ativa else df_aba
                df_salvar.to_excel(writer, sheet_name=aba, index=False)

def excluir_aba_excel(path_destino: str, aba_para_excluir: str, abas_dict: dict):
    """Exclui uma aba reescrevendo o arquivo Excel sem ela."""
    realizar_backup_seguranca(path_destino)
    
    # Remove a aba do dicionário em memória
    if aba_para_excluir in abas_dict:
        del abas_dict[aba_para_excluir]
        
    # Trava extra: Garante que o dicionário tenha pelo menos uma aba para o Excel não corromper
    if not abas_dict:
        abas_dict["Planilha1"] = pd.DataFrame(columns=["Data", "Numerador", "Denominador"])

    # Reescreve o arquivo Excel DO ZERO (sem a aba que foi deletada)
    with pd.ExcelWriter(path_destino, engine="openpyxl") as writer:
        for aba, df_aba in abas_dict.items():
            df_aba.to_excel(writer, sheet_name=aba, index=False)