import plotly.graph_objects as go
import pandas as pd
import numpy as np
from config import METAS_PERCENTIL50, TITULOS_CUSTOMIZADOS

COR_LIMITE_ESTATISTICO = "#B22222"   # vermelho: limite calculado (Laney), varia por ponto
COR_LIMITE_TEORICO = "#111111"       # preto: teto/piso teórico saturado (ex: 100% ou 0), igual ao Minitab
COR_MEDIA = "#228B22"

def _plotar_linha_segmentada(fig, x_vals, y_vals, flags_teorico, width=0.8):
    """
    Desenha uma linha em degraus (hv) trocando de cor conforme o trecho é
    limite estatístico (vermelho) ou limite teórico saturado (preto), evitando
    quebras visuais entre os segmentos.
    """
    n = len(x_vals)
    if n == 0:
        return
    i = 0
    while i < n:
        flag_atual = flags_teorico[i]
        j = i
        while j < n and flags_teorico[j] == flag_atual:
            j += 1
        inicio = i - 1 if i > 0 else i  # sobrepõe 1 ponto com o segmento anterior p/ não deixar "buraco"
        seg_x = x_vals[inicio:j]
        seg_y = y_vals[inicio:j]
        cor = COR_LIMITE_TEORICO if flag_atual else COR_LIMITE_ESTATISTICO
        fig.add_trace(go.Scatter(
            x=seg_x, y=seg_y, mode="lines", line_shape="hv",
            line=dict(color=cor, width=width), hoverinfo="skip", showlegend=False
        ))
        i = j

def _aba_bate(aba_nome: str, abas_permitidas) -> bool:
    """
    Compara o nome da aba de forma tolerante a maiúsculas/espaços (ex: 'uti 1' == 'UTI1').
    Se abas_permitidas for None, a meta vale para QUALQUER aba (sem restrição).
    """
    if abas_permitidas is None:
        return True
    alvo = aba_nome.strip().upper().replace(" ", "")
    return alvo in {u.strip().upper().replace(" ", "") for u in abas_permitidas}

def construir_figura_plotly(df_calc: pd.DataFrame, config: dict, nome_ind: str, col_data: str, fases: list, col_fase_atual: str, mostrar_rotulos: bool, aba_nome: str) -> go.Figure:
    """Gera gráfico Laney isolado por fase com quebras estritas e limites variáveis reais."""
    fig = go.Figure()
    modo_grafico = "lines+markers+text" if mostrar_rotulos else "lines+markers"

    # 1. Identificar o tipo de gráfico e definir símbolos e sufixos
    is_u_chart = config["tipo"].upper().startswith("U")
    simbolo_media = "Ū" if is_u_chart else "P̄"

    # Customização flexível do sufixo de valor (permite string vazia)
    sufixo_valor = config.get("sufixo_valor")
    if sufixo_valor is None:
        sufixo_valor = "" if is_u_chart else "%"

    ultimo_global = df_calc.iloc[-1]
    df_calc = df_calc.copy()

    # Colunas de saturação teórica podem não existir se vier de um statistics.py antigo;
    # neste caso assume-se "sem saturação" para manter compatibilidade retroativa.
    if "LSC_TEORICO" not in df_calc.columns:
        df_calc["LSC_TEORICO"] = False
    if "LIC_TEORICO" not in df_calc.columns:
        df_calc["LIC_TEORICO"] = False

    fases_coords = []
    for fase in fases:
        if col_fase_atual != "Nenhuma" and col_fase_atual in df_calc.columns:
            df_fase = df_calc[df_calc[col_fase_atual] == fase]
        else:
            df_fase = df_calc

        if not df_fase.empty:
            fases_coords.append({
                "fase": fase,
                "df": df_fase,
                "start": df_fase[col_data].iloc[0],
                "end": df_fase[col_data].iloc[-1]
            })

    vlines_datas = []

    for idx, coord in enumerate(fases_coords):
        df_fase = coord["df"]
        start_date = coord["start"]
        end_date = coord["end"]

        if idx > 0:
            end_anterior = fases_coords[idx - 1]["end"]
            left_midpoint = end_anterior + (start_date - end_anterior) / 2
            vlines_datas.append(left_midpoint)
        else:
            left_midpoint = start_date

        if idx < len(fases_coords) - 1:
            start_proxima = fases_coords[idx + 1]["start"]
            right_midpoint = end_date + (start_proxima - end_date) / 2
        else:
            right_midpoint = end_date

        # Preserva os degraus e a variação real de LSC e LIC conforme o denominador do mês
        x_limites = [left_midpoint] + df_fase[col_data].tolist() + [right_midpoint]
        y_lsc = [df_fase["LSC"].iloc[0]] + df_fase["LSC"].tolist() + [df_fase["LSC"].iloc[-1]]
        y_lic = [df_fase["LIC"].iloc[0]] + df_fase["LIC"].tolist() + [df_fase["LIC"].iloc[-1]]
        y_media = [df_fase["MEDIA"].iloc[0]] + df_fase["MEDIA"].tolist() + [df_fase["MEDIA"].iloc[-1]]
        flag_lsc = [bool(df_fase["LSC_TEORICO"].iloc[0])] + df_fase["LSC_TEORICO"].tolist() + [bool(df_fase["LSC_TEORICO"].iloc[-1])]
        flag_lic = [bool(df_fase["LIC_TEORICO"].iloc[0])] + df_fase["LIC_TEORICO"].tolist() + [bool(df_fase["LIC_TEORICO"].iloc[-1])]

        # LSC e LIC agora trocam de cor (vermelho -> preto) nos trechos saturados no teto/piso teórico
        _plotar_linha_segmentada(fig, x_limites, y_lsc, flag_lsc, width=0.8)
        _plotar_linha_segmentada(fig, x_limites, y_lic, flag_lic, width=0.8)
        fig.add_trace(go.Scatter(x=x_limites, y=y_media, mode="lines", line_shape="hv", line=dict(color=COR_MEDIA, width=1), hoverinfo="skip"))

        # Aplicar sufixo dinâmico nos rótulos e hover
        fig.add_trace(go.Scatter(
            x=df_fase[col_data], y=df_fase["TAXA"], mode=modo_grafico,
            line=dict(color="#0055A4", width=1.2), marker=dict(size=8, color="#0055A4"),
            text=df_fase["TAXA"].apply(lambda v: f"{v:.1f}{sufixo_valor}") if mostrar_rotulos else None,
            textposition="top center",
            hovertemplate=f"<b>%{{x|%b/%y}}</b><br>Taxa: %{{y:.2f}}{sufixo_valor}<extra></extra>"
        ))

        ultimo_fase = df_fase.iloc[-1]
        if pd.notna(ultimo_fase["MEDIA"]):
            fig.add_annotation(
                x=end_date, y=ultimo_fase["MEDIA"], text=f"{simbolo_media} = {ultimo_fase['MEDIA']:.2f}{sufixo_valor}",
                showarrow=False, xanchor="right", yanchor="bottom", yshift=3,
                font=dict(size=11, family="Arial", color=COR_MEDIA)
            )

    df_out = df_calc[df_calc["OUTLIER"]]
    if not df_out.empty:
        fig.add_trace(go.Scatter(x=df_out[col_data], y=df_out["TAXA"], mode="markers", marker=dict(size=10, color="#D50000", symbol="square"), hoverinfo="skip"))

    df_run = df_calc[df_calc["RUN"] & ~df_calc["OUTLIER"]]
    if not df_run.empty:
        fig.add_trace(go.Scatter(x=df_run[col_data], y=df_run["TAXA"], mode="markers", marker=dict(size=10, color="#f59e0b", symbol="diamond"), hoverinfo="skip"))

    for data_linha in vlines_datas:
        fig.add_vline(x=data_linha, line_width=1, line_dash="dash", line_color="#9370DB")

    # Ajustar os rótulos globais de limite e média na direita com margem segura expandida
    for valor, label, cor_label in [
        (ultimo_global["LSC"], f"LSC={ultimo_global['LSC']:.2f}{sufixo_valor}", "#000000"),
        (ultimo_global["MEDIA"], f"{simbolo_media}={ultimo_global['MEDIA']:.2f}{sufixo_valor}", "#000000"),
        (ultimo_global["LIC"], f"LIC={ultimo_global['LIC']:.2f}{sufixo_valor}", "#000000")
    ]:
        if pd.notna(valor):
            fig.add_annotation(
                x=1.01, y=valor, xref="paper", yref="y", text=label,
                showarrow=False, xanchor="left", font=dict(size=14, family="Arial", color=cor_label)
            )

    # Todas as metas cadastradas para este indicador que valem para a aba atual
    # (ex: P50 restrito a UTIs + SBIBAE sem restrição) são desenhadas JUNTAS,
    # cada uma com sua própria linha pontilhada, cor e rótulo.
    metas_aplicaveis = [
        m for m in METAS_PERCENTIL50.get(nome_ind, [])
        if _aba_bate(aba_nome, m.get("abas"))
    ]
    if metas_aplicaveis:
        data_min, data_max = df_calc[col_data].min(), df_calc[col_data].max()
        for meta in metas_aplicaveis:
            meta_valor = meta["valor"]
            meta_cor = meta.get("cor", "#6A0DAD")
            meta_nome = meta.get("nome", "Meta")
            fig.add_trace(go.Scatter(
                x=[data_min, data_max], y=[meta_valor, meta_valor], mode="lines",
                line=dict(color=meta_cor, width=1.5, dash="dot"), hoverinfo="skip", showlegend=False
            ))
            fig.add_annotation(
                x=1.01, y=meta_valor, xref="paper", yref="y",
                text=f"{meta_nome}={meta_valor:g}{sufixo_valor}", showarrow=False,
                xanchor="left", font=dict(size=12, family="Arial", color=meta_cor)
            )

    # Customização flexível do título secundário (evita a inserção automática do traço se estiver em branco)
   # Customização flexível do título secundário (evita a inserção automática do traço se estiver em branco)
    sufixo_titulo = config.get("titulo_grafico")
    
    if sufixo_titulo is None:
        # --- EXCEÇÃO APLICADA AQUI ---
        # Se o nome do indicador contiver "ISC CL", muda o sufixo automaticamente
        if "ISC CL" in str(nome_ind).upper():
            sufixo_titulo = ""
        else:
            # Regra padrão original
            sufixo_titulo = "Densidade de Incidência (TDI)" if is_u_chart else "Taxa de Utilização (%)"

    if sufixo_titulo.strip():
        texto_titulo_completo = f"{nome_ind} — {sufixo_titulo} ({aba_nome.upper()})"
    else:
        texto_titulo_completo = f"{nome_ind} ({aba_nome.upper()})"

    fig.update_layout(
        autosize=True, height=480,
        title=dict(text=texto_titulo_completo, font=dict(size=20, color="#333"), x=0.5, xanchor="center"),
        plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified",
        xaxis=dict(type="date", tickformat="%b/%y", showgrid=False, linecolor="gray", mirror=True),
        yaxis=dict(showgrid=False, linecolor="gray", mirror=True),
        showlegend=False, margin=dict(l=50, r=150, t=60, b=40),  # Margem ampliada à direita para evitar cortes
        font=dict(size=14, family="Arial")
            )
    return fig