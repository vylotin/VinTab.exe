import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from .config import D2_MR_N2, NELSON_RUN, EPSILON

@dataclass
class ResultadoLaney:
    df: pd.DataFrame
    fases: list
    desvio: float = float("nan")
    desc_stats: pd.Series = field(default_factory=pd.Series)

def detectar_runs(serie: pd.Series, media: float, n: int = NELSON_RUN) -> pd.Series:
    acima = (serie > media).astype(int)
    abaixo = (serie < media).astype(int)
    runs = pd.Series(False, index=serie.index)
    for i in range(n - 1, len(serie)):
        janela_acima = acima.iloc[i - n + 1: i + 1].sum()
        janela_abaixo = abaixo.iloc[i - n + 1: i + 1].sum()
        if janela_acima == n or janela_abaixo == n:
            runs.iloc[i] = True
    return runs

def calcular_analises_completas(dados: pd.DataFrame, col_data: str, col_num: str, col_den: str, col_fase: str, tipo: str, mult: float) -> ResultadoLaney:
    d = dados.copy()
    d[col_data] = pd.to_datetime(d[col_data], errors="coerce")
    d[col_num] = pd.to_numeric(d[col_num], errors="coerce")
    d[col_den] = pd.to_numeric(d[col_den], errors="coerce")
    d = d.dropna(subset=[col_num, col_den, col_data]).sort_values(by=col_data).reset_index(drop=True)

    if d.empty:
        return ResultadoLaney(df=d, fases=[])

    fase_virtual = False
    if col_fase == "Nenhuma" or col_fase not in d.columns:
        col_fase = "_FASE_"
        d[col_fase] = 1
        fase_virtual = True

    # Indicadores do tipo P (proporção) são matematicamente limitados ao intervalo [0, mult]
    # (ex: 0% a 100% de utilização). Indicadores do tipo U (densidade/taxa) não têm teto teórico.
    is_p_chart = tipo.upper().startswith("P")

    d["TAXA"] = np.where(d[col_den] > 0, (d[col_num] / d[col_den]) * mult, np.nan)
    for col in ["MEDIA", "LSC", "LIC"]:
        d[col] = np.nan
    d["LSC_TEORICO"] = False  # sinaliza pontos em que o LSC estatístico foi saturado pelo teto teórico (mult)
    d["LIC_TEORICO"] = False  # sinaliza pontos em que o LIC estatístico foi saturado pelo piso teórico (0)

    fases = d[col_fase].dropna().unique()

    for fase in fases:
        mask = d[col_fase] == fase
        df_f = d[mask]
        den_sum = df_f[col_den].sum()
        num_sum = df_f[col_num].sum()
        if den_sum == 0:
            continue

        u_bar = num_sum / den_sum
        d.loc[mask, "MEDIA"] = u_bar * mult

        if tipo.upper().startswith("U"):
            sigma_i = np.sqrt(u_bar / df_f[col_den].clip(lower=EPSILON))
        else:
            sigma_i = np.sqrt((u_bar * (1 - u_bar)) / df_f[col_den].clip(lower=EPSILON))

        taxa_pura = df_f[col_num] / df_f[col_den].clip(lower=EPSILON)
        sigma_safe = np.where(sigma_i == 0, EPSILON, sigma_i)
        z_scores = (taxa_pura - u_bar) / sigma_safe

        mr = np.abs(z_scores.diff())
        mr_bar = mr.dropna().mean()
        sigma_z = (mr_bar / D2_MR_N2) if (pd.notna(mr_bar) and mr_bar > 0) else 1.0

        margem = 3 * sigma_z * sigma_i * mult
        lsc_bruto = (u_bar * mult) + margem
        lic_bruto = (u_bar * mult) - margem

        # --- CORREÇÃO PRINCIPAL: saturação nos limites teoricamente possíveis ---
        # LIC nunca pode ser negativo (aplica-se a P e U)
        d.loc[mask, "LIC_TEORICO"] = (lic_bruto < 0).values
        lic_final = np.maximum(0, lic_bruto)

        # LSC de indicadores P (proporção) nunca pode ultrapassar 100% (ou 1,0 * mult)
        if is_p_chart:
            d.loc[mask, "LSC_TEORICO"] = (lsc_bruto > mult).values
            lsc_final = np.minimum(mult, lsc_bruto)
        else:
            lsc_final = lsc_bruto

        d.loc[mask, "LSC"] = lsc_final
        d.loc[mask, "LIC"] = lic_final

    # --- CORREÇÃO: outlier deve considerar violação em AMBOS os limites, não só o superior ---
    d["OUTLIER"] = (d["TAXA"] > d["LSC"]) | (d["TAXA"] < d["LIC"])
    d["RUN"] = False

    for fase in fases:
        mask = d[col_fase] == fase
        media_fase = d.loc[mask, "MEDIA"].iloc[0] if mask.sum() > 0 else np.nan
        if pd.notna(media_fase) and mask.sum() >= NELSON_RUN:
            d.loc[mask, "RUN"] = detectar_runs(d.loc[mask, "TAXA"], media_fase, n=NELSON_RUN).values

    d["CAUSA_ESPECIAL"] = d["OUTLIER"] | d["RUN"]

    desc = d["TAXA"].describe()
    desvio = desc.get("std", np.nan)

    if fase_virtual:
        d = d.drop(columns=["_FASE_"], errors="ignore")

    return ResultadoLaney(
        df=d,
        fases=list(fases),
        desvio=desvio,
        desc_stats=desc
    )
