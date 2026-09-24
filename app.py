# app.py
import streamlit as st
import pandas as pd
import os
import zipfile
import uuid
import json
from datetime import datetime
from io import BytesIO
from html import escape

# Importações dos módulos refatorados e seguros
from config import CONFIG_INDICADORES, ARQUIVO_SALVO
from audit import registrar_auditoria
from auth import render_login_widget
from data_layer import ler_arquivo, salvar_excel_multiaba, carregar_excel, excluir_aba_excel
from statistics import calcular_analises_completas, ResultadoLaney
from charts import construir_figura_plotly

# =====================================================================
# PERSISTÊNCIA DOS GRÁFICOS PERSONALIZADOS (JSON)
# =====================================================================
ARQUIVO_CUSTOM = "custom_charts.json"

def carregar_custom():
    if os.path.exists(ARQUIVO_CUSTOM):
        try:
            with open(ARQUIVO_CUSTOM, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def salvar_custom(dados):
    with open(ARQUIVO_CUSTOM, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=4)

if "indicadores_custom" not in st.session_state:
    st.session_state.indicadores_custom = carregar_custom()

st.set_page_config(page_title="CCIH — Painel de Controle | HUGO", layout="wide", initial_sidebar_state="expanded", page_icon="🦠")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;800&family=IBM+Plex+Mono&display=swap');
    html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
    .stPlotlyChart { image-rendering: -webkit-optimize-contrast; image-rendering: crisp-edges; }
    </style>
""", unsafe_allow_html=True)    

MESES_PT = {1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun', 7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'}
MESES_INV = {v.lower(): k for k, v in MESES_PT.items()}

def format_to_pt(date_val):
    if pd.isnull(date_val): return ""
    try:
        dt = pd.to_datetime(date_val)
        return f"{MESES_PT[dt.month]}/{dt.strftime('%y')}"
    except:
        return str(date_val)

def parse_from_pt(str_val):
    if pd.isnull(str_val) or str_val == "": return pd.NaT
    if isinstance(str_val, datetime): return str_val
    try:
        str_val = str(str_val).replace('-', '/').replace(' ', '')
        m, y = str_val.split('/')
        mes = MESES_INV[m.strip()[:3].lower()]
        ano = int(y) if int(y) > 100 else int(y) + 2000
        return pd.to_datetime(f"{ano}-{mes:02d}-01")
    except:
        return pd.to_datetime(str_val, errors='coerce')

# =====================================================================
# INTERFACE LATERAL: FLUXO DE DADOS E NOVA ABA
# =====================================================================
with st.sidebar:
    st.markdown("### 🦠 CCIH Control Charts")
    st.markdown("---")
    st.header("📂 Fonte de Dados")

    abas_dict = None
    if os.path.exists(ARQUIVO_SALVO):
        st.success("✅ Base salva encontrada")
        usar_salvo = st.checkbox("Continuar com a base salva", value=True)
    else:
        usar_salvo = False

    if usar_salvo:
        try:
            abas_dict, _ = ler_arquivo(ARQUIVO_SALVO)
        except Exception:
            st.error("Erro ao carregar base de dados.")
            st.stop()
    else:
        arquivo_up = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
        if arquivo_up:
            abas_dict, _ = ler_arquivo(arquivo_up)

    df = None
    aba_selecionada = None
    if abas_dict is not None:
        aba_selecionada = st.selectbox("📑 Aba (Dataset)", list(abas_dict.keys()))
        df = abas_dict[aba_selecionada]
        
        st.markdown("---")
        with st.expander("➕ Criar Nova Aba no Excel"):
            st.caption("Adiciona uma nova planilha limpa ao arquivo.")
            nova_aba = st.text_input("Nome da Nova Aba:")
            nomes_colunas = st.text_input("Colunas iniciais (separadas por vírgula):", value="Data, Numerador, Denominador, Fase")
            if st.button("Criar e Salvar Aba", use_container_width=True):
                if nova_aba and nova_aba not in abas_dict:
                    cols = [c.strip() for c in nomes_colunas.split(",") if c.strip()]
                    df_novo = pd.DataFrame(columns=cols)
                    abas_dict[nova_aba] = df_novo
                    salvar_excel_multiaba(ARQUIVO_SALVO, df_novo, nova_aba, abas_dict)
                    st.success(f"Aba '{nova_aba}' criada com sucesso!")
                    carregar_excel.clear()
                    st.rerun()
                elif nova_aba in abas_dict:
                    st.warning("Esta aba já existe!")

st.title("🦠 CCIH — Painel de Monitoramento")

if df is None:
    st.info("👈 Carregue uma base de dados na barra lateral para inicializar o painel.")
    st.stop()

# =====================================================================
# EDIÇÃO DE DADOS 
# =====================================================================
colunas_disponiveis = df.columns.tolist()
col_data = next((c for c in colunas_disponiveis if any(k in c.lower() for k in ["data", "mes", "mês"])), colunas_disponiveis[0] if colunas_disponiveis else "Data")

df_display = df.copy()
if not df_display.empty and col_data in df_display.columns:
    df_display[col_data] = df_display[col_data].apply(format_to_pt)

with st.expander("✏️ Edição de Dados em Tempo Real", expanded=False):
    st.markdown("Você pode editar as datas no padrão brasileiro (Ex: **Jun/26**) e adicionar novas linhas.")
    
    c_add_col, c_add_btn = st.columns([3, 1])
    nova_col_nome = c_add_col.text_input("Precisa de mais colunas? Digite o nome e adicione:", placeholder="Ex: Fase 2, Observacao")
    if c_add_btn.button("➕ Adicionar Coluna à Aba", use_container_width=True):
        if nova_col_nome and nova_col_nome not in df_display.columns:
            df_temp = df.copy()
            df_temp[nova_col_nome] = None
            try:
                salvar_excel_multiaba(ARQUIVO_SALVO, df_temp, aba_selecionada, abas_dict)
                carregar_excel.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao criar coluna: {e}")
                
    df_editado_str = st.data_editor(df_display, num_rows="dynamic", use_container_width=True, height=250)

    df_editado = df_editado_str.copy()
    if not df_editado.empty and col_data in df_editado.columns:
        df_editado[col_data] = df_editado[col_data].apply(parse_from_pt)
        df_editado = df_editado.dropna(subset=[col_data]) 

    # [NOVO] BLOCO DE GERENCIAMENTO (SALVAR E EXCLUIR) PROTEGIDO POR SENHA
    st.markdown("#### 🔒 Gerenciamento da Planilha (Administrador)")
    if render_login_widget():
        c_save, c_del = st.columns(2)
        
        # Botão para Salvar
        if c_save.button("💾 Salvar Alterações na Tabela", use_container_width=True):
            try:
                salvar_excel_multiaba(ARQUIVO_SALVO, df_editado, aba_selecionada, abas_dict)
                carregar_excel.clear()
                registrar_auditoria(f"SAVE_DATA | aba={aba_selecionada} | linhas={len(df_editado)}")
                st.success("✅ Arquivo atualizado em disco com sucesso.")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Falha de gravação: {e}")
                
        # Botão para Excluir a Aba
        if c_del.button(f"🗑️ Excluir permanentemente a aba '{aba_selecionada}'", use_container_width=True):
            if len(abas_dict) <= 1:
                st.error("⚠️ O arquivo precisa ter pelo menos uma aba. Crie uma nova aba antes de excluir esta.")
            else:
                try:
                    excluir_aba_excel(ARQUIVO_SALVO, aba_selecionada, abas_dict)
                    carregar_excel.clear()
                    registrar_auditoria(f"DELETE_TAB | aba={aba_selecionada}")
                    st.success(f"✅ Aba '{aba_selecionada}' excluída do arquivo com sucesso.")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Falha ao excluir aba: {e}")

colunas_disponiveis = df_editado.columns.tolist()

indicadores_ativos = {
    nome: cfg for nome, cfg in CONFIG_INDICADORES.items()
    if cfg["num"] in colunas_disponiveis and cfg["den"] in colunas_disponiveis
}

indicadores_custom_ativos = {
    nome: cfg for nome, cfg in st.session_state.indicadores_custom.items()
    if cfg["num"] in colunas_disponiveis and cfg["den"] in colunas_disponiveis
}

# =====================================================================
# MENU DE DOWNLOAD EM LOTE DOS GRÁFICOS (requer: pip install kaleido)
# =====================================================================
import zipfile as _zipfile_dl
from io import BytesIO as _BytesIO_dl

def _gerar_figura_export(nome_ind, cfg):
    fase_cfg = cfg.get("fase", "Nenhuma")
    fase_valida = fase_cfg if fase_cfg in colunas_disponiveis else "Nenhuma"
    res = calcular_analises_completas(
        df_editado.copy(), col_data, cfg["num"], cfg["den"], fase_valida, cfg["tipo"], cfg.get("mult", 100)
    )
    if res.df.empty:
        return None
    return construir_figura_plotly(res.df, cfg, nome_ind, col_data, res.fases, fase_valida, False, aba_selecionada)

todos_indicadores = {**indicadores_ativos, **indicadores_custom_ativos}

with st.sidebar:
    st.markdown("---")
    with st.expander("📥 Baixar Gráficos"):
        selecionados = st.multiselect("Escolha os gráficos:", list(todos_indicadores.keys()))
        formato = st.radio("Formato:", ["PNG", "PDF"], horizontal=True, key="formato_export_ccih")
        if st.button("⬇️ Gerar ZIP", use_container_width=True, disabled=not selecionados):
            buffer_zip = _BytesIO_dl()
            with _zipfile_dl.ZipFile(buffer_zip, "w") as zf:
                for nome in selecionados:
                    fig_exp = _gerar_figura_export(nome, todos_indicadores[nome])
                    if fig_exp is None:
                        continue
                    ext = "png" if formato == "PNG" else "pdf"
                    img_bytes = fig_exp.to_image(format=ext, scale=2)
                    zf.writestr(f"{nome}.{ext}", img_bytes)
            buffer_zip.seek(0)
            st.download_button(
                "💾 Salvar ZIP", data=buffer_zip, file_name="graficos_ccih.zip",
                mime="application/zip", use_container_width=True, key="btn_dl_zip_ccih"
            )

def renderizar_bloco_grafico(nome_ind, config, m_col, mostrar_rotulos=False):
    df_para_calculo = df_editado.copy()
    fase_cfg = config.get("fase", "Nenhuma")
    fase_valida = fase_cfg if fase_cfg in colunas_disponiveis else "Nenhuma"
    
    res: ResultadoLaney = calcular_analises_completas(
        df_para_calculo, col_data, config["num"], config["den"], 
        fase_valida, config["tipo"], config.get("mult", 100)
    )
    if res.df.empty: return

    meses_br = [format_to_pt(d) for d in res.df[col_data].tolist()]
    res.df['mes_br'] = meses_br

    fig_tela = construir_figura_plotly(
        res.df, config, nome_ind, col_data, res.fases, fase_valida, mostrar_rotulos, aba_selecionada
    )
    
    datas_reais = res.df[col_data].tolist()
    ticks_limpos = [m if i % 3 == 0 else " " for i, m in enumerate(meses_br)]
    
    fig_tela.update_layout(
        xaxis=dict(
            tickmode="array", 
            tickvals=datas_reais, 
            ticktext=ticks_limpos, 
            showgrid=False, 
            mirror=True,
            ticks="outside",
            tickcolor="gray",
            ticklen=6,
            tickangle=0
        ),
        margin=dict(l=50, r=150, t=50, b=80) 
    )
    
    chave_unica = uuid.uuid4().hex[:8] 
    
    # Define o nome do arquivo mesclando o indicador e a aba (Ex: IPCS_UTI ADULTO)
    nome_download = f"{nome_ind}_{aba_selecionada}"
    
    m_col.plotly_chart(
        fig_tela, 
        use_container_width=True, 
        key=f"chart_{chave_unica}",
        config={
            'toImageButtonOptions': {
                'format': 'png',           # Formato da imagem
                'filename': nome_download, # Aqui vai o nome personalizado!
                'height': 600,
                'width': 800,
                'scale': 10                 # Aumenta a qualidade/resolução
            }
        }
    )
    return fig_tela

# =====================================================================
# SEPARAÇÃO DE AMBIENTES (ABAS MESTRAS)
# =====================================================================
st.markdown("---")
aba_mestra_padrao, aba_mestra_livre = st.tabs(["🏥 Controle de Infecção (Padrão)", "🛠️ Laboratório Livre (Personalizados)"])

with aba_mestra_padrao:
    st.markdown(f"#### 📍 Indicadores Oficiais — {escape(aba_selecionada.upper())}")
    if not indicadores_ativos:
        st.warning("Nenhum indicador oficial da CCIH foi detectado nesta planilha. As colunas necessárias não foram encontradas.")
    
    PARES_MONITORAMENTO = {"IPCS x CVC": ("IPCS", "CVC"), "ITU-CV x CVD": ("ITU-CV", "CVD"), "PAV x VM": ("PAV", "VM")}
    abas_internas = st.tabs(list(PARES_MONITORAMENTO.keys()) + ["Outros Oficiais"])
    indicadores_renderizados = []

    for idx, (titulo_aba, (ind_infec, ind_uso)) in enumerate(PARES_MONITORAMENTO.items()):
        with abas_internas[idx]:
            col1, col2 = st.columns(2)
            if ind_infec in indicadores_ativos:
                renderizar_bloco_grafico(ind_infec, indicadores_ativos[ind_infec], col1)
                indicadores_renderizados.append(ind_infec)
            if ind_uso in indicadores_ativos:
                renderizar_bloco_grafico(ind_uso, indicadores_ativos[ind_uso], col2)
                indicadores_renderizados.append(ind_uso)

    with abas_internas[-1]:
        restantes = [i for i in indicadores_ativos.keys() if i not in indicadores_renderizados]
        for ind_extra in restantes:
            renderizar_bloco_grafico(ind_extra, indicadores_ativos[ind_extra], st)

with aba_mestra_livre:
    st.markdown("#### 🛠️ Área de Criação de Gráficos Livres")
    st.caption("Gráficos criados aqui ficam salvos no sistema de forma independente dos painéis fixos.")
    
    with st.expander("➕ Adicionar Novo Gráfico", expanded=True):
        c1, c2 = st.columns(2)
        nome_custom = c1.text_input("Nome do Gráfico (Ex: Taxa ISC-CL Neurologia)", key="add_nome")
        tipo_custom = c2.radio("Comportamento Estatístico", ["U (Densidade/Taxas)", "P (Proporções/Porcentagem)"], horizontal=True, key="add_tipo")

        c3, c4, c5 = st.columns(3)
        opcoes_matematicas = [c for c in colunas_disponiveis if c != col_data]
        if not opcoes_matematicas: 
            opcoes_matematicas = colunas_disponiveis
        
        num_custom = c3.selectbox("Numerador (Variável de eventos/ocorrências)", opcoes_matematicas, key="add_num")
        den_custom = c4.selectbox("Denominador (Volume de risco/Tempo de exposição)", opcoes_matematicas, key="add_den")
        fase_custom = c5.selectbox("Quebra de Fase (Opcional)", ["Nenhuma"] + colunas_disponiveis, key="add_fase")

        c6, c7, c8 = st.columns(3)
        mult_custom = c6.number_input("Multiplicador de Escala (Ex: 100, 1000, 10000)", min_value=1, value=1000, step=1, key="add_mult")
        titulo_eixo = c7.text_input("Título Secundário (Deixe vazio para não exibir nada)", value="", placeholder="Ex: TDI, Taxa de Eventos...", key="add_tit")
        sufixo_v = c8.text_input("Sufixo numérico (Deixe vazio para não exibir nada)", value="", placeholder="Ex: %, por 10k...", key="add_suf")

        if st.button("Criar Gráfico e Salvar", use_container_width=True, type="primary"):
            if not nome_custom:
                st.warning("⚠️ Dê um nome ao gráfico antes de salvar.")
            elif num_custom == den_custom:
                st.error("⚠️ O Numerador e o Denominador não podem ser a mesma coluna.")
            else:
                st.session_state.indicadores_custom[nome_custom] = {
                    "num": num_custom,
                    "den": den_custom,
                    "fase": fase_custom,
                    "tipo": tipo_custom,
                    "mult": mult_custom,
                    "titulo_grafico": titulo_eixo,
                    "sufixo_valor": sufixo_v
                }
                salvar_custom(st.session_state.indicadores_custom)
                st.success("✅ Gráfico criado e armazenado com sucesso!")
                st.rerun()

    st.markdown("---")
    
    if not indicadores_custom_ativos:
        st.info("Você ainda não possui gráficos personalizados criados ou compatíveis com as colunas desta aba.")
    else:
        nomes_custom = list(indicadores_custom_ativos.keys())
        for i in range(0, len(nomes_custom), 2):
            colA, colB = st.columns(2)
            
            with colA:
                ind_a = nomes_custom[i]
                renderizar_bloco_grafico(ind_a, indicadores_custom_ativos[ind_a], colA)
                if st.button(f"🗑️ Deletar '{ind_a}'", key=f"del_{ind_a}"):
                    del st.session_state.indicadores_custom[ind_a]
                    salvar_custom(st.session_state.indicadores_custom)
                    st.rerun()

            if i + 1 < len(nomes_custom):
                with colB:
                    ind_b = nomes_custom[i + 1]
                    renderizar_bloco_grafico(ind_b, indicadores_custom_ativos[ind_b], colB)
                    if st.button(f"🗑️ Deletar '{ind_b}'", key=f"del_{ind_b}"):
                        del st.session_state.indicadores_custom[ind_b]
                        salvar_custom(st.session_state.indicadores_custom)
                        st.rerun()
                        # =====================================================================
# RODAPÉ E CRÉDITOS
# =====================================================================
st.markdown("""
    <div style="text-align: center; padding: 20px; color: #475569; font-size: 0.8rem; border-top: 1px solid rgba(0,0,0,0.05); margin-top: 40px;">
        <b>Controle de Infecção Hospitalar | CIDS | SCIH | HUGO</b><br>
        <i>Desenvolvido por Pedro Vinicius Reis da Rocha</i>
    </div>
""", unsafe_allow_html=True)
