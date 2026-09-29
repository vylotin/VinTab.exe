import sqlite3
import sys
import json
import os
from contextlib import closing
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Qt, QUrl, Signal, Slot
from PySide6.QtWebEngineWidgets import QWebEngineView

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QInputDialog,
    QLabel,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from storage.project_store import (
    criar_conjunto,
    importar_planilha_excel,
    ler_conjunto,
    listar_conjuntos,
    salvar_conjunto,
)
from core.audit import ler_registros_auditoria, registrar_auditoria
from core.config import CONFIG_INDICADORES


COLUNAS_PADRAO = (
    "MÊS, PCT-dia, CVC-DIA, STAGE CVC, IPCS, STAGE ICS, CVD-DIA, STAGE CVD, "
    "ITU-CV, STAGE ITUAC, VM-DIA, STAGE VM, PAV, STAGE PAV"
)


class _SinaisTarefa(QObject):
    concluida = Signal(object, object, object, bool)


class _Tarefa(QRunnable):
    def __init__(self, funcao, ao_concluir, ao_falhar):
        super().__init__()
        self.funcao = funcao
        self.ao_concluir = ao_concluir
        self.ao_falhar = ao_falhar
        self.sinais = _SinaisTarefa()

    def run(self):
        try:
            resultado = self.funcao()
        except Exception as erro:
            self.sinais.concluida.emit(self.ao_concluir, self.ao_falhar, erro, True)
        else:
            self.sinais.concluida.emit(self.ao_concluir, self.ao_falhar, resultado, False)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("VinTab Desktop")
        self.setMinimumSize(1180, 760)
        self._caminho_banco = None
        self._botoes_navegacao = {}
        self._pool_tarefas = QThreadPool(self)
        self._tarefas_ativas = 0
        self._salvando_dados = False
        self._dados_sujos = False
        self._caminho_excel = None
        self._solicitacao_graficos = 0
        self._settings = QSettings("VinTab", "VinTab")

        self._criar_menu()
        self._criar_interface()

        raiz_recursos = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        banco_padrao = raiz_recursos / "base_vintab.db"
        if banco_padrao.is_file():
            self._carregar_banco(str(banco_padrao), exibir_erro=False)

    def closeEvent(self, event):
        if not self._dados_sujos:
            event.accept()
            return

        dialogo = QMessageBox(self)
        dialogo.setIcon(QMessageBox.Icon.Warning)
        dialogo.setWindowTitle("Salvar alterações antes de sair?")
        dialogo.setText("O projeto atual tem alterações que ainda não foram salvas.")
        salvar = dialogo.addButton("Salvar e sair", QMessageBox.ButtonRole.AcceptRole)
        descartar = dialogo.addButton("Descartar e sair", QMessageBox.ButtonRole.DestructiveRole)
        cancelar = dialogo.addButton("Continuar editando", QMessageBox.ButtonRole.RejectRole)
        dialogo.setDefaultButton(cancelar)
        dialogo.exec()
        clicado = dialogo.clickedButton()
        if clicado == salvar:
            event.ignore()
            self._salvar_dados(ao_concluir=self.close)
        elif clicado == descartar:
            event.accept()
        else:
            event.ignore()

    def _executar_em_segundo_plano(self, funcao, ao_concluir, ao_falhar=None, mensagem="Processando..."):
        tarefa = _Tarefa(funcao, ao_concluir, ao_falhar)
        tarefa.sinais.concluida.connect(self._finalizar_tarefa)
        self._tarefas_ativas += 1
        self._indicador_tarefa.show()
        self.statusBar().showMessage(mensagem)
        self._pool_tarefas.start(tarefa)

    @Slot(object, object, object, bool)
    def _finalizar_tarefa(self, ao_concluir, ao_falhar, resultado, falhou):
        self._tarefas_ativas -= 1
        if self._tarefas_ativas == 0:
            self._indicador_tarefa.hide()
            self.statusBar().showMessage("Operação concluída", 3000)
        self._atualizar_acoes_projeto()
        if falhou:
            if ao_falhar:
                ao_falhar(resultado)
            else:
                QMessageBox.critical(self, "Erro", str(resultado))
            return
        ao_concluir(resultado)

    def _criar_menu(self):
        menu_bar = self.menuBar()

        menu_banco = menu_bar.addMenu("Banco")
        self._adicionar_acao(menu_banco, "Abrir banco...", self._selecionar_banco)
        self._adicionar_acao(menu_banco, "Fechar banco", self._fechar_banco)

        menu_graficos = menu_bar.addMenu("Gráficos")
        self._adicionar_acao(menu_graficos, "Cartas de controle", lambda: self._ir_para("graficos"))
        self._adicionar_acao(menu_graficos, "Indicadores", lambda: self._ir_para("graficos"))

        menu_projetos = menu_bar.addMenu("Projetos .vt")
        self._adicionar_acao(menu_projetos, "Novo projeto...", self._novo_projeto)
        self._adicionar_acao(menu_projetos, "Abrir projeto...", self._selecionar_projeto)
        self._adicionar_acao(menu_projetos, "Informações do projeto", lambda: self._ir_para("projetos"))

        menu_auditoria = menu_bar.addMenu("Auditoria")
        self._adicionar_acao(menu_auditoria, "Visualizar registros", lambda: self._ir_para("auditoria"))

        menu_importacao = menu_bar.addMenu("Importação Excel")
        self._adicionar_acao(menu_importacao, "Selecionar planilha...", self._selecionar_excel)

    @staticmethod
    def _adicionar_acao(menu, texto, callback):
        acao = QAction(texto, menu)
        acao.triggered.connect(lambda checked=False: callback())
        menu.addAction(acao)

    def _criar_interface(self):
        widget_central = QWidget(self)
        widget_central.setObjectName("widgetCentral")
        self.setCentralWidget(widget_central)
        self.setStatusBar(QStatusBar(self))
        self._indicador_tarefa = QProgressBar()
        self._indicador_tarefa.setRange(0, 0)
        self._indicador_tarefa.setFixedWidth(150)
        self._indicador_tarefa.setMaximumHeight(12)
        self._indicador_tarefa.setTextVisible(False)
        self._indicador_tarefa.hide()
        self.statusBar().addPermanentWidget(self._indicador_tarefa)

        layout_principal = QVBoxLayout(widget_central)
        layout_principal.setContentsMargins(24, 18, 24, 14)
        layout_principal.setSpacing(14)

        cabecalho = QVBoxLayout()
        cabecalho.setSpacing(6)
        titulo = QLabel("VinTab")
        titulo.setObjectName("titulo")
        cabecalho.addWidget(titulo)
        subtitulo = QLabel("Cartas de controle e indicadores assistenciais")
        subtitulo.setObjectName("subtitulo")
        cabecalho.addWidget(subtitulo)
        layout_principal.addLayout(cabecalho)

        separador = QFrame()
        separador.setFrameShape(QFrame.HLine)
        separador.setObjectName("separador")
        layout_principal.addWidget(separador)

        corpo = QHBoxLayout()
        corpo.setSpacing(18)
        layout_principal.addLayout(corpo, 1)

        barra_lateral = QFrame()
        barra_lateral.setObjectName("barraLateral")
        barra_lateral.setFixedWidth(194)
        navegacao = QVBoxLayout(barra_lateral)
        navegacao.setSpacing(5)
        navegacao.setContentsMargins(10, 14, 10, 12)
        marca = QLabel("VINTAB")
        marca.setObjectName("marca")
        navegacao.addWidget(marca)
        contexto = QLabel("ANÁLISE ASSISTENCIAL")
        contexto.setObjectName("contextoNavegacao")
        navegacao.addWidget(contexto)
        navegacao.addSpacing(14)
        for chave, rotulo in (
            ("inicio", "Início"),
            ("banco", "Banco"),
            ("graficos", "Gráficos"),
            ("projetos", "Projetos .vt"),
            ("dados", "Dados"),
            ("auditoria", "Auditoria"),
            ("excel", "Importação Excel"),
        ):
            botao = QPushButton(rotulo)
            botao.setProperty("nav", True)
            botao.setCheckable(True)
            botao.setMinimumHeight(38)
            botao.clicked.connect(lambda checked=False, pagina=chave: self._ir_para(pagina))
            self._botoes_navegacao[chave] = botao
            navegacao.addWidget(botao)
        navegacao.addStretch()
        corpo.addWidget(barra_lateral, 0)

        self._paginas = QStackedWidget()
        corpo.addWidget(self._paginas, 1)
        self._indice_paginas = {}
        self._criar_paginas()
        self._ir_para("projetos")

        self.statusBar().showMessage("VinTab iniciado")

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #F2F6F4;
                color: #20342F;
                font-family: "Segoe UI";
                font-size: 13px;
            }

            QMenuBar {
                background-color: #FFFFFF;
                color: #20342F;
                border-bottom: 1px solid #D9E3DE;
                padding: 5px 8px;
            }

            QMenuBar::item:selected, QMenu::item:selected {
                background-color: #E4F0EB;
                color: #155D50;
            }

            QMenu {
                background-color: #FFFFFF;
                color: #20342F;
                border: 1px solid #D9E3DE;
            }

            #titulo {
                color: #183E37;
                font-size: 28px;
                font-weight: 700;
            }

            #subtitulo, #mensagemInicial {
                color: #687B75;
                font-size: 14px;
            }

            #barraLateral {
                background-color: #173D36;
                border-radius: 7px;
            }

            #marca {
                background: transparent;
                color: #FFFFFF;
                font-size: 21px;
                font-weight: 700;
                padding: 2px 8px;
            }

            #contextoNavegacao {
                background: transparent;
                color: #A9C6BC;
                font-size: 10px;
                padding: 0 8px;
            }

            #separador {
                color: #D9E3DE;
            }

            #rodape {
                color: #687B75;
                font-size: 13px;
            }

            QFrame[frameShape="4"] {
                background-color: #D9E3DE;
                border: none;
                min-height: 1px;
                max-height: 1px;
            }

            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CBD9D2;
                border-radius: 5px;
                color: #24443B;
                padding: 8px 12px;
                text-align: center;
            }

            QPushButton:hover {
                background-color: #F5FAF7;
                border-color: #589884;
            }

            QPushButton:checked {
                background-color: #2A6D5E;
                border-color: #2A6D5E;
                color: #FFFFFF;
            }

            QPushButton[nav="true"] {
                background-color: transparent;
                border: 1px solid transparent;
                color: #DDEAE5;
                padding: 9px 11px;
                text-align: left;
            }

            QPushButton[nav="true"]:hover {
                background-color: #245348;
                border-color: #32695D;
            }

            QPushButton[nav="true"]:checked {
                background-color: #D9ECE5;
                border-color: #D9ECE5;
                color: #174B40;
                font-weight: 600;
            }

            QPushButton[role="primary"] {
                background-color: #1D705F;
                border-color: #1D705F;
                color: #FFFFFF;
                font-weight: 600;
            }

            QPushButton[role="primary"]:hover {
                background-color: #185C4E;
                border-color: #185C4E;
            }

            QPushButton[role="danger"] {
                background-color: #FFF9F7;
                border-color: #E6C7BF;
                color: #963E32;
            }

            QPushButton[role="danger"]:hover {
                background-color: #FCECE8;
                border-color: #C97B6A;
            }

            QPushButton:disabled {
                background-color: #EEF2F0;
                border-color: #E1E8E4;
                color: #9AA9A3;
            }

            QLineEdit, QComboBox, QTableWidget {
                background-color: #FFFFFF;
                color: #20342F;
                border: 1px solid #CBD9D2;
                border-radius: 4px;
                selection-background-color: #D8ECE4;
                selection-color: #174B40;
            }

            QLineEdit, QComboBox {
                padding: 7px 9px;
            }

            QTableWidget {
                gridline-color: #E8EEEB;
                alternate-background-color: #F7F9F8;
                outline: 0;
            }

            QTableWidget::item {
                padding: 6px;
                border: none;
            }

            QTableWidget::item:selected {
                background-color: #D8ECE4;
                color: #174B40;
            }

            QHeaderView::section {
                background-color: #EAF1ED;
                color: #49665D;
                border: none;
                border-bottom: 1px solid #CFDCD5;
                padding: 8px 7px;
                font-weight: 600;
            }

            #tituloPagina {
                color: #183E37;
                font-size: 22px;
                font-weight: 700;
            }

            #descricaoPagina {
                color: #687B75;
                font-size: 13px;
            }

            #surface {
                background-color: #FFFFFF;
                border: 1px solid #DEE7E2;
                border-radius: 6px;
            }

            #statusProjetoAtivo {
                background-color: #EAF4EF;
                border: 1px solid #D1E5DA;
                border-radius: 5px;
                color: #245B49;
                padding: 10px 12px;
            }

            #emptyState {
                color: #687B75;
                padding: 18px;
            }

            QStatusBar {
                background-color: #FFFFFF;
                color: #52665E;
                border-top: 1px solid #D9E3DE;
            }

            QProgressBar {
                background-color: #E7EFEB;
                border: none;
                border-radius: 4px;
            }

            QProgressBar::chunk {
                background-color: #3A927A;
                border-radius: 4px;
            }
            """
        )

    def _criar_paginas(self):
        inicio = self._nova_pagina(
            "Área de trabalho",
            "Abra um projeto recente ou comece um arquivo de análise.",
        )
        acoes_inicio = QHBoxLayout()
        for titulo, callback, papel in (
            ("Novo projeto .vt", self._novo_projeto, "primary"),
            ("Abrir projeto .vt", self._selecionar_projeto, ""),
            ("Projetos recentes", lambda: self._ir_para("projetos"), ""),
        ):
            botao = QPushButton(titulo)
            botao.setMinimumHeight(48)
            if papel:
                botao.setProperty("role", papel)
            botao.clicked.connect(lambda checked=False, acao=callback: acao())
            acoes_inicio.addWidget(botao)
        inicio.layout().addLayout(acoes_inicio)
        self._adicionar_pagina("inicio", inicio)

        banco = self._nova_pagina(
            "Banco de dados",
            "Abra um arquivo SQLite para consultar as tabelas existentes.",
        )
        acoes_banco = QHBoxLayout()
        abrir_banco = QPushButton("Abrir banco SQLite...")
        abrir_banco.clicked.connect(self._selecionar_banco)
        fechar_banco = QPushButton("Fechar banco")
        fechar_banco.clicked.connect(self._fechar_banco)
        acoes_banco.addWidget(abrir_banco)
        acoes_banco.addWidget(fechar_banco)
        acoes_banco.addStretch()
        banco.layout().addLayout(acoes_banco)
        self._rotulo_banco = QLabel("Nenhum banco aberto")
        self._rotulo_banco.setWordWrap(True)
        banco.layout().addWidget(self._rotulo_banco)
        self._lista_tabelas = QTreeWidget()
        self._lista_tabelas.setHeaderLabels(["Tabelas do banco"])
        banco.layout().addWidget(self._lista_tabelas, 1)
        self._adicionar_pagina("banco", banco)

        projetos = self._nova_pagina(
            "Projetos VinTab",
            "Crie, reabra e organize seus arquivos de trabalho .vt.",
        )
        self._rotulo_projeto = QLabel("Nenhum projeto aberto")
        self._rotulo_projeto.setObjectName("statusProjetoAtivo")
        self._rotulo_projeto.setWordWrap(True)
        projetos.layout().addWidget(self._rotulo_projeto)

        acoes_projeto = QHBoxLayout()
        novo_projeto = QPushButton("Novo projeto")
        novo_projeto.setProperty("role", "primary")
        novo_projeto.clicked.connect(self._novo_projeto)
        abrir_projeto = QPushButton("Abrir projeto...")
        abrir_projeto.clicked.connect(self._selecionar_projeto)
        atualizar_projetos = QPushButton("Atualizar lista")
        atualizar_projetos.clicked.connect(self._atualizar_lista_projetos)
        acoes_projeto.addWidget(novo_projeto)
        acoes_projeto.addWidget(abrir_projeto)
        acoes_projeto.addStretch()
        acoes_projeto.addWidget(atualizar_projetos)
        projetos.layout().addLayout(acoes_projeto)

        self._pesquisa_projetos = QLineEdit()
        self._pesquisa_projetos.setPlaceholderText("Pesquisar por nome ou pasta")
        self._pesquisa_projetos.setClearButtonEnabled(True)
        self._pesquisa_projetos.textChanged.connect(self._filtrar_projetos)
        projetos.layout().addWidget(self._pesquisa_projetos)

        self._tabela_projetos = QTableWidget(0, 5)
        self._tabela_projetos.setHorizontalHeaderLabels(
            ["Projeto .vt", "Pasta", "Modificado", "Tamanho", "Estado"]
        )
        self._tabela_projetos.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tabela_projetos.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tabela_projetos.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tabela_projetos.setAlternatingRowColors(True)
        self._tabela_projetos.verticalHeader().setVisible(False)
        self._tabela_projetos.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._tabela_projetos.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        for coluna in (2, 3, 4):
            self._tabela_projetos.horizontalHeader().setSectionResizeMode(
                coluna, QHeaderView.ResizeToContents
            )
        self._tabela_projetos.itemSelectionChanged.connect(
            self._atualizar_acoes_projeto
        )
        self._tabela_projetos.cellDoubleClicked.connect(
            lambda _linha, _coluna: self._abrir_projeto_selecionado()
        )
        projetos.layout().addWidget(self._tabela_projetos, 1)

        self._rotulo_lista_projetos = QLabel("Nenhum projeto recente")
        self._rotulo_lista_projetos.setObjectName("emptyState")
        projetos.layout().addWidget(self._rotulo_lista_projetos)

        acoes_selecao = QHBoxLayout()
        self._botao_abrir_projeto = QPushButton("Abrir selecionado")
        self._botao_abrir_projeto.setProperty("role", "primary")
        self._botao_abrir_projeto.clicked.connect(self._abrir_projeto_selecionado)
        self._botao_abrir_pasta = QPushButton("Abrir pasta")
        self._botao_abrir_pasta.clicked.connect(self._abrir_pasta_projeto)
        self._botao_mover_projeto = QPushButton("Mover/Renomear...")
        self._botao_mover_projeto.clicked.connect(self._mover_projeto_selecionado)
        self._botao_remover_recente = QPushButton("Remover da lista")
        self._botao_remover_recente.clicked.connect(self._remover_projeto_recente)
        self._botao_excluir_projeto = QPushButton("Mover para a Lixeira")
        self._botao_excluir_projeto.setProperty("role", "danger")
        self._botao_excluir_projeto.clicked.connect(
            self._excluir_projeto_selecionado
        )
        for botao in (
            self._botao_abrir_projeto,
            self._botao_abrir_pasta,
            self._botao_mover_projeto,
            self._botao_remover_recente,
            self._botao_excluir_projeto,
        ):
            acoes_selecao.addWidget(botao)
        projetos.layout().addLayout(acoes_selecao)
        self._atualizar_acoes_projeto()
        self._adicionar_pagina("projetos", projetos)
        self._atualizar_lista_projetos()

        dados = self._nova_pagina(
            "Dados do projeto",
            "Crie um conjunto de dados, edite as células e salve as alterações no arquivo .vt.",
        )
        controles_dados = QHBoxLayout()
        self._combo_conjuntos = QComboBox()
        self._combo_conjuntos.setMinimumWidth(220)
        self._combo_conjuntos.currentTextChanged.connect(self._carregar_conjunto)
        self._botao_criar_conjunto = QPushButton("Novo conjunto...")
        self._botao_criar_conjunto.clicked.connect(self._criar_conjunto)
        self._botao_adicionar_linha = QPushButton("Adicionar linha")
        self._botao_adicionar_linha.clicked.connect(self._adicionar_linha)
        self._botao_excluir_linha = QPushButton("Excluir linha")
        self._botao_excluir_linha.setProperty("role", "danger")
        self._botao_excluir_linha.clicked.connect(self._excluir_linha)
        self._botao_salvar_dados = QPushButton("Salvar dados")
        self._botao_salvar_dados.setProperty("role", "primary")
        self._botao_salvar_dados.clicked.connect(self._salvar_dados)
        controles_dados.addWidget(self._combo_conjuntos)
        controles_dados.addWidget(self._botao_criar_conjunto)
        controles_dados.addStretch()
        controles_dados.addWidget(self._botao_adicionar_linha)
        controles_dados.addWidget(self._botao_excluir_linha)
        controles_dados.addWidget(self._botao_salvar_dados)
        dados.layout().addLayout(controles_dados)
        self._tabela_dados = QTableWidget()
        self._tabela_dados.setAlternatingRowColors(True)
        self._tabela_dados.itemChanged.connect(self._marcar_dados_alterados)
        dados.layout().addWidget(self._tabela_dados, 1)
        self._rotulo_estado_dados = QLabel("Abra um projeto .vt para criar e editar dados.")
        self._rotulo_estado_dados.setObjectName("descricaoPagina")
        dados.layout().addWidget(self._rotulo_estado_dados)
        self._adicionar_pagina("dados", dados)
        self._atualizar_estado_editor()

        graficos = self._nova_pagina(
            "Gráficos e indicadores",
            "Cartas de controle calculadas a partir do conjunto de dados selecionado.",
        )
        controles_graficos = QHBoxLayout()
        self._combo_conjuntos_grafico = QComboBox()
        self._combo_conjuntos_grafico.setMinimumWidth(240)
        self._combo_conjuntos_grafico.currentTextChanged.connect(
            self._atualizar_painel_graficos
        )
        atualizar_graficos = QPushButton("Atualizar gráficos")
        atualizar_graficos.clicked.connect(self._atualizar_painel_graficos)
        controles_graficos.addWidget(self._combo_conjuntos_grafico)
        controles_graficos.addWidget(atualizar_graficos)
        controles_graficos.addStretch()
        graficos.layout().addLayout(controles_graficos)
        self._rotulo_graficos = QLabel("Abra um banco de dados para carregar gráficos.")
        self._rotulo_graficos.setWordWrap(True)
        self._rotulo_graficos.setObjectName("descricaoPagina")
        graficos.layout().addWidget(self._rotulo_graficos)
        self._web_graficos = QWebEngineView()
        self._web_graficos.setMinimumHeight(540)
        graficos.layout().addWidget(self._web_graficos, 1)
        self._adicionar_pagina("graficos", graficos)

        auditoria = self._nova_pagina(
            "Auditoria",
            "Registro local das alterações, importações e abertura de bancos.",
        )
        atualizar_auditoria = QPushButton("Atualizar registros")
        atualizar_auditoria.clicked.connect(self._atualizar_auditoria)
        auditoria.layout().addWidget(atualizar_auditoria)
        self._tabela_auditoria = QTableWidget(0, 2)
        self._tabela_auditoria.setHorizontalHeaderLabels(["Data e hora", "Ação"])
        self._tabela_auditoria.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tabela_auditoria.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tabela_auditoria.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._tabela_auditoria.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        auditoria.layout().addWidget(self._tabela_auditoria, 1)
        self._adicionar_pagina("auditoria", auditoria)

        excel = self._nova_pagina(
            "Importação Excel",
            "Importe todas as abas de uma planilha como conjuntos de dados em um projeto VinTab.",
        )
        selecionar_excel = QPushButton("Selecionar arquivo Excel...")
        selecionar_excel.clicked.connect(self._selecionar_excel)
        excel.layout().addWidget(selecionar_excel)
        self._rotulo_excel = QLabel("Nenhuma planilha selecionada")
        self._rotulo_excel.setWordWrap(True)
        excel.layout().addWidget(self._rotulo_excel)
        acoes_excel = QHBoxLayout()
        self._botao_importar_excel = QPushButton("Importar no projeto aberto")
        self._botao_importar_excel.clicked.connect(
            lambda: self._importar_excel_para_projeto(novo=False)
        )
        self._botao_importar_excel_novo = QPushButton("Importar criando projeto...")
        self._botao_importar_excel_novo.clicked.connect(
            lambda: self._importar_excel_para_projeto(novo=True)
        )
        acoes_excel.addWidget(self._botao_importar_excel)
        acoes_excel.addWidget(self._botao_importar_excel_novo)
        acoes_excel.addStretch()
        excel.layout().addLayout(acoes_excel)
        self._botao_importar_excel.setEnabled(False)
        self._botao_importar_excel_novo.setEnabled(False)
        excel.layout().addStretch()
        self._adicionar_pagina("excel", excel)

    @staticmethod
    def _nova_pagina(titulo, descricao):
        pagina = QWidget()
        layout = QVBoxLayout(pagina)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(18)

        rotulo_titulo = QLabel(titulo)
        rotulo_titulo.setObjectName("tituloPagina")
        layout.addWidget(rotulo_titulo)

        rotulo_descricao = QLabel(descricao)
        rotulo_descricao.setObjectName("descricaoPagina")
        rotulo_descricao.setWordWrap(True)
        layout.addWidget(rotulo_descricao)
        return pagina

    def _adicionar_pagina(self, chave, pagina):
        self._paginas.addWidget(pagina)
        self._indice_paginas[chave] = self._paginas.count() - 1

    def _ir_para(self, chave):
        self._paginas.setCurrentIndex(self._indice_paginas[chave])
        for nome, botao in self._botoes_navegacao.items():
            botao.setChecked(nome == chave)
        self.statusBar().showMessage(f"Área: {self._botoes_navegacao[chave].text()}")
        if chave == "projetos":
            self._atualizar_lista_projetos()
        elif chave == "graficos":
            self._atualizar_painel_graficos()
        elif chave == "auditoria":
            self._atualizar_auditoria()

    def _selecionar_banco(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir banco de dados",
            "",
            "Bancos SQLite (*.db *.sqlite *.sqlite3 *.vt);;Todos os arquivos (*)",
        )
        if caminho:
            self._carregar_banco(caminho)

    def _selecionar_projeto(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir projeto VinTab",
            self._diretorio_projetos(),
            "Projetos VinTab (*.vt);;Todos os arquivos (*)",
        )
        if caminho:
            self._settings.setValue("projects/lastDirectory", str(Path(caminho).parent))
            self._carregar_banco(caminho)

    def _diretorio_projetos(self):
        diretorio = Path(self._settings.value("projects/lastDirectory", ""))
        if not diretorio.is_dir():
            documentos = Path.home() / "Documents"
            diretorio = documentos if documentos.is_dir() else Path.home()
        return str(diretorio)

    def _novo_projeto(self):
        if self._dados_sujos:
            self._confirmar_alteracoes_nao_salvas(self._novo_projeto)
            return
        nome_sugerido = str(Path(self._diretorio_projetos()) / "Novo projeto.vt")
        caminho, _ = QFileDialog.getSaveFileName(
            self,
            "Criar projeto VinTab",
            nome_sugerido,
            "Projeto VinTab (*.vt)",
        )
        if not caminho:
            return
        if not caminho.lower().endswith(".vt"):
            caminho += ".vt"
        if Path(caminho).exists():
            QMessageBox.warning(self, "Projeto existente", "Escolha um nome de arquivo que ainda não exista.")
            return
        self._settings.setValue("projects/lastDirectory", str(Path(caminho).parent))

        def criar_arquivo():
            with closing(sqlite3.connect(caminho)):
                pass

        self._executar_em_segundo_plano(
            criar_arquivo,
            lambda _resultado: self._projeto_criado(caminho),
            lambda erro: QMessageBox.critical(self, "Erro ao criar projeto", str(erro)),
            "Criando projeto...",
        )

    def _projeto_criado(self, caminho):
        registrar_auditoria(f"CREATE_PROJECT | caminho={caminho}")
        self._carregar_banco(caminho)

    def _caminhos_projetos_recentes(self):
        caminhos = self._settings.value("projects/recent", [])
        if isinstance(caminhos, str):
            caminhos = [caminhos]
        if not isinstance(caminhos, (list, tuple)):
            return []
        return [str(caminho) for caminho in caminhos if str(caminho).strip()]

    def _registrar_projeto_recente(self, caminho):
        projeto = str(Path(caminho).resolve())
        recentes = [
            recente for recente in self._caminhos_projetos_recentes()
            if recente.casefold() != projeto.casefold()
        ]
        self._settings.setValue("projects/recent", [projeto, *recentes[:19]])
        self._settings.setValue("projects/lastDirectory", str(Path(projeto).parent))
        self._atualizar_lista_projetos()

    def _atualizar_lista_projetos(self):
        caminhos = self._caminhos_projetos_recentes()
        if self._caminho_banco and Path(self._caminho_banco).suffix.lower() == ".vt":
            ativo = str(Path(self._caminho_banco).resolve())
            if ativo.casefold() not in {c.casefold() for c in caminhos}:
                caminhos.insert(0, ativo)

        self._tabela_projetos.setRowCount(len(caminhos))
        projeto_ativo = str(Path(self._caminho_banco).resolve()).casefold() if (
            self._caminho_banco and Path(self._caminho_banco).suffix.lower() == ".vt"
        ) else None

        for linha, caminho_texto in enumerate(caminhos):
            caminho = Path(caminho_texto).expanduser()
            existe = caminho.is_file() and caminho.suffix.lower() == ".vt"
            nome = QTableWidgetItem(caminho.name)
            nome.setData(Qt.ItemDataRole.UserRole, caminho_texto)
            self._tabela_projetos.setItem(linha, 0, nome)
            self._tabela_projetos.setItem(linha, 1, QTableWidgetItem(str(caminho.parent)))

            if existe:
                try:
                    informacoes = caminho.stat()
                    atualizado = datetime.fromtimestamp(informacoes.st_mtime).strftime(
                        "%d/%m/%Y %H:%M"
                    )
                    tamanho = self._formatar_tamanho(informacoes.st_size)
                except OSError:
                    atualizado, tamanho = "Indisponível", "Indisponível"
            else:
                atualizado, tamanho = "—", "—"

            self._tabela_projetos.setItem(linha, 2, QTableWidgetItem(atualizado))
            self._tabela_projetos.setItem(linha, 3, QTableWidgetItem(tamanho))
            estado = (
                "Aberto" if caminho_texto.casefold() == projeto_ativo
                else "Disponível" if existe
                else "Não encontrado"
            )
            self._tabela_projetos.setItem(linha, 4, QTableWidgetItem(estado))
            if caminho_texto.casefold() == projeto_ativo:
                self._tabela_projetos.selectRow(linha)

        self._rotulo_lista_projetos.setVisible(not caminhos)
        self._rotulo_lista_projetos.setText(
            "Nenhum projeto recente. Crie um projeto ou abra um arquivo .vt para começar."
        )
        self._filtrar_projetos(self._pesquisa_projetos.text())
        self._atualizar_acoes_projeto()

    @staticmethod
    def _formatar_tamanho(tamanho):
        unidades = ("B", "KB", "MB", "GB")
        valor = float(tamanho)
        for unidade in unidades:
            if valor < 1024 or unidade == unidades[-1]:
                return f"{valor:.0f} {unidade}" if unidade == "B" else f"{valor:.1f} {unidade}"
            valor /= 1024

    def _filtrar_projetos(self, texto):
        filtro = texto.strip().casefold()
        visiveis = 0
        for linha in range(self._tabela_projetos.rowCount()):
            conteudo = " ".join(
                self._tabela_projetos.item(linha, coluna).text()
                for coluna in range(self._tabela_projetos.columnCount())
                if self._tabela_projetos.item(linha, coluna)
            ).casefold()
            visivel = not filtro or filtro in conteudo
            self._tabela_projetos.setRowHidden(linha, not visivel)
            visiveis += visivel
        tem_projetos = self._tabela_projetos.rowCount() > 0
        self._rotulo_lista_projetos.setVisible(not tem_projetos or visiveis == 0)
        if tem_projetos and visiveis == 0:
            self._rotulo_lista_projetos.setText("Nenhum projeto corresponde à pesquisa.")
        linha_selecionada = self._tabela_projetos.currentRow()
        if linha_selecionada >= 0 and self._tabela_projetos.isRowHidden(linha_selecionada):
            self._tabela_projetos.clearSelection()
            self._atualizar_acoes_projeto()

    def _caminho_projeto_selecionado(self):
        linha = self._tabela_projetos.currentRow()
        if linha < 0 or not self._tabela_projetos.item(linha, 0):
            return None
        return self._tabela_projetos.item(linha, 0).data(Qt.ItemDataRole.UserRole)

    def _atualizar_acoes_projeto(self):
        if not hasattr(self, "_botao_abrir_projeto"):
            return
        caminho = self._caminho_projeto_selecionado()
        existe = bool(
            caminho and Path(caminho).suffix.lower() == ".vt" and Path(caminho).is_file()
        )
        ocupado = self._tarefas_ativas > 0
        esta_aberto = bool(
            caminho and self._caminho_banco
            and str(Path(caminho).resolve()).casefold()
            == str(Path(self._caminho_banco).resolve()).casefold()
        )
        self._botao_abrir_projeto.setEnabled(existe and not ocupado)
        self._botao_abrir_pasta.setEnabled(existe)
        self._botao_mover_projeto.setEnabled(existe and not ocupado)
        self._botao_remover_recente.setEnabled(bool(caminho) and not ocupado and not esta_aberto)
        self._botao_excluir_projeto.setEnabled(existe and not ocupado)

    def _abrir_projeto_selecionado(self):
        caminho = self._caminho_projeto_selecionado()
        if not caminho or not Path(caminho).is_file():
            QMessageBox.warning(self, "Projeto indisponível", "Este arquivo não foi encontrado.")
            self._atualizar_lista_projetos()
            return
        self._carregar_banco(caminho)

    def _abrir_pasta_projeto(self):
        caminho = self._caminho_projeto_selecionado()
        if caminho and Path(caminho).is_file():
            abriu = QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(caminho).parent)))
            if not abriu:
                QMessageBox.warning(self, "Pasta indisponível", "Não foi possível abrir esta pasta.")

    def _mover_projeto_selecionado(self):
        caminho = self._caminho_projeto_selecionado()
        if not caminho or not Path(caminho).is_file():
            return
        ativo = bool(
            self._caminho_banco
            and str(Path(caminho).resolve()).casefold()
            == str(Path(self._caminho_banco).resolve()).casefold()
        )
        if ativo and self._dados_sujos:
            self._confirmar_alteracoes_nao_salvas(
                self._mover_projeto_selecionado
            )
            return

        destino, _ = QFileDialog.getSaveFileName(
            self,
            "Mover ou renomear projeto",
            str(Path(caminho)),
            "Projeto VinTab (*.vt)",
        )
        if not destino:
            return
        if Path(destino).suffix.lower() != ".vt":
            destino += ".vt"
        origem = Path(caminho).resolve()
        destino_path = Path(destino).resolve()
        if origem == destino_path:
            return
        if destino_path.exists():
            QMessageBox.warning(
                self,
                "Destino já existe",
                "Escolha outro nome ou outra pasta; nenhum arquivo foi substituído.",
            )
            return
        if not destino_path.parent.is_dir():
            QMessageBox.warning(
                self, "Pasta indisponível", "A pasta de destino não existe."
            )
            return

        def mover_arquivo():
            import shutil

            return shutil.move(str(origem), str(destino_path))

        self._executar_em_segundo_plano(
            mover_arquivo,
            lambda _resultado: self._projeto_movido(str(origem), str(destino_path), ativo),
            lambda erro: QMessageBox.critical(self, "Não foi possível mover", str(erro)),
            "Movendo projeto...",
        )

    def _projeto_movido(self, origem, destino, estava_ativo):
        recentes = [
            recente for recente in self._caminhos_projetos_recentes()
            if recente.casefold() != origem.casefold()
            and recente.casefold() != destino.casefold()
        ]
        recentes.insert(0, destino)
        self._settings.setValue("projects/recent", recentes[:20])
        self._settings.setValue("projects/lastDirectory", str(Path(destino).parent))
        if estava_ativo:
            self._caminho_banco = destino
            self._rotulo_banco.setText(destino)
            self._rotulo_projeto.setText(
                f"PROJETO ATIVO  /  {Path(destino).name}\n{Path(destino).parent}"
            )
        registrar_auditoria(f"MOVE_PROJECT | origem={origem} | destino={destino}")
        self._atualizar_lista_projetos()
        self.statusBar().showMessage(f"Projeto movido para {destino}", 5000)

    def _remover_projeto_recente(self):
        caminho = self._caminho_projeto_selecionado()
        if not caminho:
            return
        restantes = [
            recente for recente in self._caminhos_projetos_recentes()
            if recente.casefold() != str(caminho).casefold()
        ]
        self._settings.setValue("projects/recent", restantes)
        self._atualizar_lista_projetos()
        self.statusBar().showMessage("Projeto removido da lista; o arquivo não foi alterado.", 5000)

    def _excluir_projeto_selecionado(self):
        caminho = self._caminho_projeto_selecionado()
        if not caminho or not Path(caminho).is_file():
            return

        esta_aberto = bool(
            self._caminho_banco
            and str(Path(self._caminho_banco).resolve()).casefold()
            == str(Path(caminho).resolve()).casefold()
        )
        mensagem = f"O arquivo '{Path(caminho).name}' será movido para a Lixeira."
        if esta_aberto and self._dados_sujos:
            mensagem += " O projeto aberto tem alterações não salvas, que serão descartadas."
        elif esta_aberto:
            mensagem += " O projeto aberto será fechado."

        dialogo = QMessageBox(self)
        dialogo.setIcon(QMessageBox.Icon.Warning)
        dialogo.setWindowTitle("Mover projeto para a Lixeira")
        dialogo.setText(mensagem)
        dialogo.setInformativeText("Esta ação pode ser desfeita pela Lixeira do sistema.")
        mover = dialogo.addButton("Mover para a Lixeira", QMessageBox.ButtonRole.DestructiveRole)
        cancelar = dialogo.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        dialogo.setDefaultButton(cancelar)
        dialogo.exec()
        if dialogo.clickedButton() != mover:
            return

        def mover_para_lixeira():
            from send2trash import send2trash

            send2trash(caminho)

        self._executar_em_segundo_plano(
            mover_para_lixeira,
            lambda _resultado: self._projeto_enviado_para_lixeira(caminho, esta_aberto),
            lambda erro: QMessageBox.critical(self, "Não foi possível excluir", str(erro)),
            "Movendo projeto para a Lixeira...",
        )

    def _projeto_enviado_para_lixeira(self, caminho, estava_aberto):
        restantes = [
            recente for recente in self._caminhos_projetos_recentes()
            if recente.casefold() != str(caminho).casefold()
        ]
        self._settings.setValue("projects/recent", restantes)
        if estava_aberto:
            self._fechar_banco(confirmar=False)
        registrar_auditoria(f"DELETE_PROJECT | caminho={caminho}")
        self._atualizar_lista_projetos()
        QMessageBox.information(
            self,
            "Projeto movido",
            f"'{Path(caminho).name}' foi movido para a Lixeira.",
        )

    def _carregar_banco(self, caminho, exibir_erro=True):
        caminho_resolvido = str(Path(caminho).resolve())
        if self._dados_sujos and caminho_resolvido != self._caminho_banco:
            self._confirmar_alteracoes_nao_salvas(
                lambda: self._carregar_banco(caminho, exibir_erro)
            )
            return
        caminho_anterior = self._caminho_banco

        def ler_banco():
            uri = Path(caminho).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as conexao:
                tabelas = conexao.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                ).fetchall()
            return [nome for (nome,) in tabelas]

        def banco_carregado(tabelas):
            if self._caminho_banco != str(Path(caminho).resolve()):
                return
            self._rotulo_banco.setText(self._caminho_banco)
            if Path(self._caminho_banco).suffix.lower() == ".vt":
                projeto = Path(self._caminho_banco)
                self._rotulo_projeto.setText(
                    f"PROJETO ATIVO  /  {projeto.name}\n{projeto.parent}"
                )
                self._registrar_projeto_recente(self._caminho_banco)
            else:
                self._rotulo_projeto.setText("Nenhum projeto .vt aberto")
            self._lista_tabelas.clear()
            for nome_tabela in tabelas:
                self._lista_tabelas.addTopLevelItem(QTreeWidgetItem([nome_tabela]))
            self._atualizar_conjuntos()
            if exibir_erro:
                destino = (
                    "dados"
                    if Path(self._caminho_banco).suffix.lower() == ".vt"
                    else "banco"
                )
                self._ir_para(destino)
            registrar_auditoria(
                f"OPEN_DATABASE | caminho={self._caminho_banco} | tabelas={len(tabelas)}"
            )
            self._atualizar_auditoria()
            self.statusBar().showMessage(f"Banco aberto: {len(tabelas)} tabela(s)")

        def erro_ao_abrir(erro):
            if self._caminho_banco == caminho_resolvido:
                self._caminho_banco = caminho_anterior
                self._rotulo_banco.setText(caminho_anterior or "Nenhum banco aberto")
                self._rotulo_projeto.setText(
                    caminho_anterior
                    if caminho_anterior and Path(caminho_anterior).suffix.lower() == ".vt"
                    else "Nenhum projeto aberto"
                )
                self._atualizar_estado_editor()
                self._atualizar_lista_projetos()
            if exibir_erro:
                QMessageBox.critical(self, "Não foi possível abrir o banco", str(erro))

        self._caminho_banco = caminho_resolvido
        self._executar_em_segundo_plano(
            ler_banco,
            banco_carregado,
            erro_ao_abrir,
            "Abrindo banco de dados...",
        )

    def _fechar_banco(self, confirmar=True):
        if confirmar and self._dados_sujos:
            self._confirmar_alteracoes_nao_salvas(
                lambda: self._fechar_banco(confirmar=False)
            )
            return
        if self._caminho_banco:
            registrar_auditoria(f"CLOSE_DATABASE | caminho={self._caminho_banco}")
        self._dados_sujos = False
        self._caminho_banco = None
        self._rotulo_banco.setText("Nenhum banco aberto")
        self._rotulo_projeto.setText("Nenhum projeto aberto")
        self._lista_tabelas.clear()
        self._atualizar_conjuntos()
        self.statusBar().showMessage("Banco fechado")

    def _confirmar_alteracoes_nao_salvas(self, ao_continuar):
        dialogo = QMessageBox(self)
        dialogo.setIcon(QMessageBox.Icon.Warning)
        dialogo.setWindowTitle("Alterações não salvas")
        dialogo.setText("Há alterações que ainda não foram gravadas no projeto.")
        dialogo.setInformativeText("Salve antes de continuar ou descarte as alterações.")
        salvar = dialogo.addButton("Salvar e continuar", QMessageBox.ButtonRole.AcceptRole)
        descartar = dialogo.addButton(
            "Descartar e continuar", QMessageBox.ButtonRole.DestructiveRole
        )
        cancelar = dialogo.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        dialogo.setDefaultButton(cancelar)
        dialogo.exec()
        clicado = dialogo.clickedButton()
        if clicado == salvar:
            self._salvar_dados(ao_concluir=ao_continuar)
        elif clicado == descartar:
            self._dados_sujos = False
            self._atualizar_estado_editor()
            ao_continuar()

    def _atualizar_conjuntos(self, conjunto_selecionado=None):
        caminho = self._caminho_banco
        if not caminho:
            self._combo_conjuntos.clear()
            self._combo_conjuntos_grafico.clear()
            self._tabela_dados.clear()
            self._tabela_dados.setRowCount(0)
            self._tabela_dados.setColumnCount(0)
            self._rotulo_graficos.setText("Abra um banco de dados para carregar gráficos.")
            self._web_graficos.setHtml("")
            self._botao_importar_excel.setEnabled(False)
            self._atualizar_estado_editor()
            return

        def conjuntos_carregados(nomes):
            if caminho != self._caminho_banco:
                return
            self._combo_conjuntos.blockSignals(True)
            self._combo_conjuntos.clear()
            self._combo_conjuntos.addItems(nomes)
            if conjunto_selecionado:
                indice = self._combo_conjuntos.findText(conjunto_selecionado)
                if indice >= 0:
                    self._combo_conjuntos.setCurrentIndex(indice)
            self._combo_conjuntos.blockSignals(False)
            nome_grafico = conjunto_selecionado or self._combo_conjuntos.currentText()
            self._combo_conjuntos_grafico.blockSignals(True)
            self._combo_conjuntos_grafico.clear()
            self._combo_conjuntos_grafico.addItems(nomes)
            indice_grafico = self._combo_conjuntos_grafico.findText(nome_grafico)
            if indice_grafico >= 0:
                self._combo_conjuntos_grafico.setCurrentIndex(indice_grafico)
            self._combo_conjuntos_grafico.blockSignals(False)
            self._botao_importar_excel.setEnabled(
                bool(self._caminho_excel)
                and Path(caminho).suffix.lower() == ".vt"
            )
            self._atualizar_estado_editor()
            if self._combo_conjuntos.currentText():
                self._carregar_conjunto(self._combo_conjuntos.currentText())
            else:
                self._tabela_dados.clear()
                self._tabela_dados.setRowCount(0)
                self._tabela_dados.setColumnCount(0)
                self._rotulo_graficos.setText(
                    "O banco aberto não possui tabelas para analisar."
                )
                self._web_graficos.setHtml("")

        def erro_ao_listar(erro):
            if caminho == self._caminho_banco:
                self._rotulo_estado_dados.setText(f"Não foi possível listar os conjuntos: {erro}")

        self._executar_em_segundo_plano(
            lambda: listar_conjuntos(caminho),
            conjuntos_carregados,
            erro_ao_listar,
            "Carregando conjuntos...",
        )

    def _atualizar_painel_graficos(self, *_args):
        caminho = self._caminho_banco
        nome_conjunto = self._combo_conjuntos_grafico.currentText()
        self._solicitacao_graficos += 1
        solicitacao = self._solicitacao_graficos
        if not caminho or not nome_conjunto:
            self._rotulo_graficos.setText(
                "Abra um banco com tabelas de dados para gerar gráficos."
            )
            self._web_graficos.setHtml("")
            return

        self._rotulo_graficos.setText(f"Carregando gráficos de {nome_conjunto}...")
        self._executar_em_segundo_plano(
            lambda: self._gerar_painel_graficos(caminho, nome_conjunto),
            lambda resultado: self._painel_graficos_pronto(
                solicitacao, caminho, nome_conjunto, resultado
            ),
            lambda erro: self._erro_ao_gerar_graficos(
                solicitacao, caminho, nome_conjunto, erro
            ),
            f"Calculando gráficos de '{nome_conjunto}'...",
        )

    def _gerar_painel_graficos(self, caminho, nome_conjunto):
        import pandas as pd
        from core.charts import construir_figura_plotly
        from core.statistics import calcular_analises_completas

        colunas, linhas = ler_conjunto(caminho, nome_conjunto)
        dataframe = pd.DataFrame(linhas, columns=colunas)
        if dataframe.empty:
            return None, f"O conjunto '{nome_conjunto}' não possui linhas para analisar."

        col_data = next(
            (
                coluna for coluna in colunas
                if any(palavra in str(coluna).casefold() for palavra in ("data", "mês", "mes", "ano"))
            ),
            colunas[0] if colunas else None,
        )
        if col_data is None:
            return None, "Não foi encontrada uma coluna de datas."

        indicadores_customizados = self._carregar_indicadores_customizados()
        indicadores = {**CONFIG_INDICADORES, **indicadores_customizados}
        figuras = []
        sem_colunas = []
        for nome, config in indicadores.items():
            numerador = config.get("num")
            denominador = config.get("den")
            if numerador not in dataframe.columns or denominador not in dataframe.columns:
                continue
            fase_configurada = config.get("fase", "Nenhuma")
            fase = fase_configurada if fase_configurada in dataframe.columns else "Nenhuma"
            try:
                resultado_laney = calcular_analises_completas(
                    dataframe.copy(),
                    col_data,
                    numerador,
                    denominador,
                    fase,
                    config.get("tipo", "U"),
                    config.get("mult", 100),
                )
                if resultado_laney.df.empty:
                    sem_colunas.append(nome)
                    continue
                figuras.append(
                    construir_figura_plotly(
                        resultado_laney.df,
                        config,
                        nome,
                        col_data,
                        resultado_laney.fases,
                        fase,
                        False,
                        nome_conjunto,
                    )
                )
            except Exception as erro:
                raise RuntimeError(f"Falha ao calcular '{nome}': {erro}") from erro

        if not figuras:
            return None, "Nenhum indicador configurado corresponde às colunas deste conjunto."

        aviso = f" · {len(sem_colunas)} indicador(es) sem valores válidos" if sem_colunas else ""
        arquivo_html = self._salvar_figuras_plotly(figuras)
        return arquivo_html, f"{len(figuras)} gráfico(s) gerado(s) para {nome_conjunto}{aviso}."

    def _painel_graficos_pronto(self, solicitacao, caminho, nome_conjunto, resultado):
        if (
            solicitacao != self._solicitacao_graficos
            or caminho != self._caminho_banco
            or nome_conjunto != self._combo_conjuntos_grafico.currentText()
        ):
            return
        arquivo_html, mensagem = resultado
        self._rotulo_graficos.setText(mensagem)
        if arquivo_html:
            self._web_graficos.setUrl(QUrl.fromLocalFile(str(arquivo_html)))
        else:
            self._web_graficos.setHtml("")

    @staticmethod
    def _carregar_indicadores_customizados():
        raiz = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        caminho = raiz / "custom_charts.json"
        try:
            with caminho.open("r", encoding="utf-8") as arquivo:
                resultado = json.load(arquivo)
        except (OSError, json.JSONDecodeError):
            return {}
        return resultado if isinstance(resultado, dict) else {}

    @staticmethod
    def _salvar_figuras_plotly(figuras):
        from plotly.offline import get_plotlyjs
        from plotly.utils import PlotlyJSONEncoder

        diretorio = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        ) / "VinTab" / "web"
        diretorio.mkdir(parents=True, exist_ok=True)
        biblioteca = diretorio / "plotly.min.js"
        if not biblioteca.exists():
            biblioteca.write_text(get_plotlyjs(), encoding="utf-8")

        payload = json.dumps(
            [figura.to_plotly_json() for figura in figuras],
            cls=PlotlyJSONEncoder,
            ensure_ascii=False,
        ).replace("</", "<\\/")
        divs = "".join(
            f'<section><div id="grafico-{indice}" class="grafico"></div></section>'
            for indice in range(len(figuras))
        )
        pagina = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<script src='plotly.min.js'></script>"
            "<style>body{margin:8px;background:#f4f5f6}section{margin:0 0 12px;"
            "background:white;border:1px solid #d8dadd}.grafico{height:480px;width:100%}"
            "</style></head><body>"
            f"{divs}<script>const figuras={payload};"
            "figuras.forEach((figura,indice)=>Plotly.newPlot("
            "`grafico-${indice}`,figura.data,figura.layout,"
            "{responsive:true,displaylogo:false}));"
            "</script></body></html>"
        )
        arquivo_html = diretorio / "graficos.html"
        arquivo_html.write_text(pagina, encoding="utf-8")
        return arquivo_html

    def _erro_ao_gerar_graficos(self, solicitacao, caminho, nome_conjunto, erro):
        if (
            solicitacao == self._solicitacao_graficos
            and caminho == self._caminho_banco
            and nome_conjunto == self._combo_conjuntos_grafico.currentText()
        ):
            if isinstance(erro, ImportError):
                mensagem = (
                    f"Falha ao carregar dependência de gráficos: {erro}. "
                    "Confira requirements.txt e a política do Windows para DLLs Python."
                )
            else:
                mensagem = f"Não foi possível gerar os gráficos: {erro}"
            self._rotulo_graficos.setText(mensagem)
            self._web_graficos.setHtml("")

    def _atualizar_estado_editor(self):
        projeto_editavel = bool(
            self._caminho_banco
            and Path(self._caminho_banco).suffix.lower() == ".vt"
        )
        tem_conjunto = bool(self._combo_conjuntos.currentText())
        gatilhos_edicao = (
            QAbstractItemView.AllEditTriggers
            if projeto_editavel
            else QAbstractItemView.NoEditTriggers
        )
        self._tabela_dados.setEditTriggers(gatilhos_edicao)
        self._botao_criar_conjunto.setEnabled(projeto_editavel)
        self._botao_adicionar_linha.setEnabled(projeto_editavel and tem_conjunto)
        self._botao_excluir_linha.setEnabled(projeto_editavel and tem_conjunto)
        self._botao_salvar_dados.setEnabled(
            projeto_editavel and tem_conjunto and self._dados_sujos and not self._salvando_dados
        )

        if not self._caminho_banco:
            mensagem = "Abra um projeto .vt para criar e editar dados."
        elif not projeto_editavel:
            mensagem = "Banco aberto para consulta. A edição é permitida somente em projetos .vt."
        elif not tem_conjunto:
            mensagem = "Projeto vazio. Clique em Novo conjunto para criar sua primeira tabela."
        elif self._dados_sujos:
            mensagem = "Alterações não salvas. Salve para gravar no projeto."
        else:
            mensagem = "Todas as alterações estão salvas."
        self._rotulo_estado_dados.setText(mensagem)

    def _marcar_dados_alterados(self, _item=None):
        if self._caminho_banco and Path(self._caminho_banco).suffix.lower() == ".vt":
            self._dados_sujos = True
            self._atualizar_estado_editor()

    def _criar_conjunto(self):
        if not self._caminho_banco or Path(self._caminho_banco).suffix.lower() != ".vt":
            self.statusBar().showMessage("Abra um projeto .vt antes de criar conjuntos.", 5000)
            return

        nome, confirmado = QInputDialog.getText(
            self,
            "Novo conjunto de dados",
            "Nome do conjunto:",
        )
        if not confirmado:
            return
        colunas_texto, confirmado = QInputDialog.getText(
            self,
            "Colunas do conjunto",
            "Nomes das colunas separados por vírgula:",
            text=COLUNAS_PADRAO,
        )
        if not confirmado:
            return

        colunas = [coluna.strip() for coluna in colunas_texto.split(",") if coluna.strip()]
        caminho = self._caminho_banco
        self._executar_em_segundo_plano(
            lambda: criar_conjunto(caminho, nome, colunas),
            lambda _resultado: self._conjunto_criado(nome.strip()),
            lambda erro: QMessageBox.warning(self, "Não foi possível criar o conjunto", str(erro)),
            "Criando conjunto...",
        )

    def _conjunto_criado(self, nome):
        self._atualizar_conjuntos(nome)
        registrar_auditoria(f"CREATE_DATASET | projeto={self._caminho_banco} | conjunto={nome}")
        self._atualizar_auditoria()
        self.statusBar().showMessage(f"Conjunto '{nome}' criado")

    def _carregar_conjunto(self, nome_conjunto):
        if not self._caminho_banco or not nome_conjunto:
            self._atualizar_estado_editor()
            return
        caminho = self._caminho_banco

        def conjunto_carregado(resultado):
            if caminho != self._caminho_banco or nome_conjunto != self._combo_conjuntos.currentText():
                return
            colunas, linhas = resultado
            self._tabela_dados.blockSignals(True)
            self._tabela_dados.setColumnCount(len(colunas))
            self._tabela_dados.setHorizontalHeaderLabels(colunas)
            self._tabela_dados.setRowCount(len(linhas))
            for indice_linha, linha in enumerate(linhas):
                for indice_coluna, valor in enumerate(linha):
                    texto = "" if valor is None else str(valor)
                    self._tabela_dados.setItem(
                        indice_linha,
                        indice_coluna,
                        QTableWidgetItem(texto),
                    )
            self._tabela_dados.blockSignals(False)
            self._dados_sujos = False
            self._tabela_dados.resizeColumnsToContents()
            self._atualizar_estado_editor()

        def erro_ao_carregar(erro):
            if caminho == self._caminho_banco and nome_conjunto == self._combo_conjuntos.currentText():
                self._rotulo_estado_dados.setText(f"Não foi possível carregar o conjunto: {erro}")

        self._executar_em_segundo_plano(
            lambda: ler_conjunto(caminho, nome_conjunto),
            conjunto_carregado,
            erro_ao_carregar,
            f"Carregando conjunto '{nome_conjunto}'...",
        )

    def _adicionar_linha(self):
        linha = self._tabela_dados.rowCount()
        self._tabela_dados.insertRow(linha)
        for coluna in range(self._tabela_dados.columnCount()):
            self._tabela_dados.setItem(linha, coluna, QTableWidgetItem(""))
        if self._tabela_dados.columnCount():
            self._tabela_dados.setCurrentCell(linha, 0)
            self._marcar_dados_alterados()

    def _excluir_linha(self):
        linha = self._tabela_dados.currentRow()
        if linha < 0:
            self.statusBar().showMessage("Selecione uma linha para excluir.", 5000)
            return
        self._tabela_dados.removeRow(linha)
        self._marcar_dados_alterados()
        self.statusBar().showMessage("Linha removida da grade; clique em Salvar dados para confirmar.")

    def _salvar_dados(self, ao_concluir=None):
        if not self._dados_sujos:
            if ao_concluir:
                ao_concluir()
            return
        nome_conjunto = self._combo_conjuntos.currentText()
        if not self._caminho_banco or not nome_conjunto:
            return
        colunas = [
            self._tabela_dados.horizontalHeaderItem(indice).text()
            for indice in range(self._tabela_dados.columnCount())
        ]
        linhas = []
        for indice_linha in range(self._tabela_dados.rowCount()):
            linhas.append([
                self._tabela_dados.item(indice_linha, indice_coluna).text()
                if self._tabela_dados.item(indice_linha, indice_coluna)
                else ""
                for indice_coluna in range(self._tabela_dados.columnCount())
            ])
        caminho = self._caminho_banco
        self._salvando_dados = True
        self._executar_em_segundo_plano(
            lambda: salvar_conjunto(caminho, nome_conjunto, colunas, linhas),
            lambda _resultado: self._finalizar_salvamento(
                nome_conjunto, len(linhas), ao_concluir=ao_concluir
            ),
            lambda erro: self._finalizar_salvamento(nome_conjunto, len(linhas), erro),
            "Salvando dados...",
        )
        self._atualizar_estado_editor()

    def _finalizar_salvamento(
        self, nome_conjunto, quantidade_linhas, erro=None, ao_concluir=None
    ):
        self._salvando_dados = False
        if erro is None:
            self._dados_sujos = False
        self._atualizar_estado_editor()
        if erro:
            QMessageBox.critical(self, "Não foi possível salvar os dados", str(erro))
            return
        self.statusBar().showMessage(
            f"Dados salvos em {nome_conjunto}: {quantidade_linhas} linha(s)"
        )
        registrar_auditoria(
            f"SAVE_DATA | projeto={self._caminho_banco} | conjunto={nome_conjunto} | linhas={quantidade_linhas}"
        )
        self._atualizar_auditoria()
        if ao_concluir:
            ao_concluir()

    def _selecionar_excel(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar planilha Excel",
            "",
            "Planilhas Excel (*.xlsx *.xls);;Todos os arquivos (*)",
        )
        if caminho:
            self._caminho_excel = caminho
            self._rotulo_excel.setText(caminho)
            self._botao_importar_excel_novo.setEnabled(True)
            self._botao_importar_excel.setEnabled(
                bool(
                    self._caminho_banco
                    and Path(self._caminho_banco).suffix.lower() == ".vt"
                )
            )
            self._ir_para("excel")
            self.statusBar().showMessage("Planilha pronta para importação.")

    def _importar_excel_para_projeto(self, novo):
        if not self._caminho_excel:
            return

        if novo:
            caminho, _ = QFileDialog.getSaveFileName(
                self,
                "Criar projeto com os dados da planilha",
                "",
                "Projeto VinTab (*.vt)",
            )
            if not caminho:
                return
            if not caminho.lower().endswith(".vt"):
                caminho += ".vt"
            if Path(caminho).exists():
                QMessageBox.warning(
                    self,
                    "Projeto existente",
                    "Escolha um nome que ainda não exista para criar um novo projeto.",
                )
                return
        else:
            caminho = self._caminho_banco
            if not caminho or Path(caminho).suffix.lower() != ".vt":
                QMessageBox.warning(
                    self,
                    "Projeto necessário",
                    "Abra um projeto .vt ou escolha 'Importar criando projeto'.",
                )
                return

        origem = self._caminho_excel
        self._botao_importar_excel.setEnabled(False)
        self._botao_importar_excel_novo.setEnabled(False)

        def importacao_concluida(resumo):
            self._caminho_excel = None
            self._rotulo_excel.setText("Importação concluída: " + origem)
            registrar_auditoria(
                f"IMPORT_EXCEL | arquivo={origem} | projeto={caminho} | abas={len(resumo)}"
            )
            self._atualizar_auditoria()
            detalhes = "\n".join(
                f"{nome}: {linhas} linha(s)" for nome, linhas in resumo
            )
            QMessageBox.information(
                self,
                "Importação concluída",
                f"{len(resumo)} aba(s) importada(s) para:\n{caminho}\n\n{detalhes}",
            )
            self._carregar_banco(caminho)

        def importacao_falhou(erro):
            self._botao_importar_excel_novo.setEnabled(bool(self._caminho_excel))
            self._botao_importar_excel.setEnabled(
                bool(
                    self._caminho_excel
                    and self._caminho_banco
                    and Path(self._caminho_banco).suffix.lower() == ".vt"
                )
            )
            QMessageBox.critical(self, "Falha na importação", str(erro))

        self._executar_em_segundo_plano(
            lambda: importar_planilha_excel(origem, caminho),
            importacao_concluida,
            importacao_falhou,
            "Importando abas do Excel...",
        )

    def _atualizar_auditoria(self):
        registros = ler_registros_auditoria()
        self._tabela_auditoria.setRowCount(len(registros))
        for indice, (horario, acao) in enumerate(registros):
            self._tabela_auditoria.setItem(indice, 0, QTableWidgetItem(horario))
            self._tabela_auditoria.setItem(indice, 1, QTableWidgetItem(acao))
