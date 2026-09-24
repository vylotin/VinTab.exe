# config.py

CONFIG_INDICADORES = {
    "IPCS": {
        "num": "IPCS", 
        "den": "CVC-DIA", 
        "fase": "STAGE ICS",
        "tipo": "U (Taxas / Densidade)", 
        "mult": 1000
    },
    "ITU-CV": {
        "num": "ITU-CV", 
        "den": "CVD-DIA", 
        "fase": "STAGE ITUAC", 
        "tipo": "U (Taxas / Densidade)", 
        "mult": 1000
    },
    "PAV": {
        "num": "PAV", 
        "den": "VM-DIA", 
        "fase": "STAGE PAV", 
        "tipo": "U (Taxas / Densidade)", 
        "mult": 1000
    },
    "CVC": {
        "num": "CVC-DIA", 
        "den": "PCT-dia", 
        "fase": "STAGE CVC", 
        "tipo": "P (Proporções)", 
        "mult": 100
    },
    "CVD": {
        "num": "CVD-DIA", 
        "den": "PCT-dia", 
        "fase": "STAGE CVD", 
        "tipo": "P (Proporções)", 
        "mult": 100
    },
    "VM": {
        "num": "VM-DIA", 
        "den": "PCT-dia", 
        "fase": "STAGE VM", 
        "tipo": "P (Proporções)", 
        "mult": 100
    },
    "ISC CL": {
            "num": "ISC CL", 
            "den": "CL", 
            "fase": "STAGE CL",
            "tipo": "P (Proporções)", 
            "mult": 100
        }
}

# Constantes SPC (Statistical Process Control) nomeadas
D2_MR_N2 = 1.128      # Constante d2 para Moving Range de amplitude 2 (Tabela SPC)
NELSON_RUN = 7        # Regra 2 de Nelson: 7 pontos consecutivos do mesmo lado da média
EPSILON = 1e-9        # Proteção matemática contra divisão por zero em denominators pequenos

ARQUIVO_SALVO = "Base_CCIH_Atualizada.xlsx"

# Nomes de aba (case/espaço-insensível) em que as metas de UTI abaixo devem aparecer
UNIDADES_META = ["UTI ADULTO", "UTI 1", "UTI 2", "UTI 3", "UTI 4"]

# Metas de referência, exibidas como linha pontilhada no gráfico.
# Cada indicador pode ter UMA OU MAIS metas simultâneas (ex: P50 + SBIBAE),
# todas desenhadas juntas, cada uma com seu próprio rótulo e cor.
#
# "abas": lista de abas (case/espaço-insensível) em que a meta aparece,
#         ou None para aparecer em QUALQUER aba (sem restrição).
METAS_PERCENTIL50 = {
    "IPCS": [
        {"nome": "P50", "valor": 2.5, "abas": UNIDADES_META, "cor": "#6A0DAD"},
        {"nome": "SBIBAE", "valor": 3.70, "abas": None, "cor": "#E67E22"}, # <--- As duas metas juntas na mesma lista!
    ],
    "ITU-CV": [
        {"nome": "P50", "valor": 1.3, "abas": UNIDADES_META, "cor": "#6A0DAD"},
    ],
    "PAV": [
        {"nome": "P50", "valor": 6.9, "abas": UNIDADES_META, "cor": "#6A0DAD"},
    ],
    "ISC CL": [
        {"nome": "SBIBAE", "valor": 1.41, "abas": None, "cor": "#E67E22"},
    ],
}
TITULOS_CUSTOMIZADOS = {
    'ISC CL': 'TAXA ISC CL',
}