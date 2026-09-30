import sqlite3
import sys
import json
import os
import hashlib
from contextlib import closing
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QAction, QFont
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QRunnable,
    QSettings,
    QThreadPool,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QScrollArea,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from storage.project_store import (
    criar_conjunto,
    criar_projeto,
    excluir_grafico,
    ler_conjunto,
    listar_graficos,
    listar_conjuntos,
    listar_colunas_conjunto,
    obter_formato_conjunto,
    obter_formato_projeto,
    registrar_formato_conjunto,
    salvar_grafico,
    salvar_conjunto,
)
from core.audit import registrar_auditoria
from core.charts import construir_grafico_mapeado
from core.config import carregar_configuracao_formato


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


class _LegacyProjectWindow(QMainWindow):

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
        self._formato_projeto = None
        self._graficos_projeto = []

        self._criar_menu()
        self._criar_interface()

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
        menu_projetos = menu_bar.addMenu("Projeto")
        self._adicionar_acao(
            menu_projetos, "Criar Novo Projeto (.vt)", self._novo_projeto
        )
        self._adicionar_acao(
            menu_projetos, "Abrir Projeto (.vt)", self._selecionar_projeto
        )

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

        layout_principal.setSpacing(0)
        self._paginas = QStackedWidget()
        layout_principal.addWidget(self._paginas, 1)
        self._indice_paginas = {}
        self._criar_paginas()
        self._ir_para("inicio")

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

            QLineEdit, QComboBox, QTableWidget, QTreeWidget {
                background-color: #FFFFFF;
                color: #20342F;
                border: 1px solid #CBD9D2;
                border-radius: 4px;
                selection-background-color: #D8ECE4;
                selection-color: #174B40;
            }

            #barraLateral QTreeWidget {
                background-color: transparent;
                color: #DDEAE5;
                border: none;
                outline: 0;
            }

            #barraLateral QTreeWidget::item {
                min-height: 27px;
                padding: 3px 4px;
            }

            #barraLateral QTreeWidget::item:selected {
                background-color: #D9ECE5;
                border-radius: 3px;
                color: #174B40;
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

    def _criar_paginas_legado(self):
        inicio = self._nova_pagina(
            "Área de trabalho",
            "Abra um projeto recente ou comece um arquivo de análise.",
        )
        acoes_inicio = QHBoxLayout()
        for titulo, callback, papel in (
            ("Criar Novo Projeto (.vt)", self._novo_projeto, "primary"),
            ("Abrir Projeto (.vt)", self._selecionar_projeto, ""),
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

    def _criar_paginas(self):
        inicio = self._nova_pagina(
            "VinTab",
            "Projetos de vigilância e análise de indicadores.",
        )
        acoes_inicio = QHBoxLayout()
        criar_projeto = QPushButton("Criar Novo Projeto (.vt)")
        criar_projeto.setProperty("role", "primary")
        criar_projeto.clicked.connect(self._novo_projeto)
        abrir_projeto = QPushButton("Abrir Projeto (.vt)")
        abrir_projeto.clicked.connect(self._selecionar_projeto)
        acoes_inicio.addWidget(criar_projeto)
        acoes_inicio.addWidget(abrir_projeto)
        acoes_inicio.addStretch()
        inicio.layout().addLayout(acoes_inicio)
        inicio.layout().addStretch()
        self._adicionar_pagina("inicio", inicio)

        workspace = QWidget()
        layout_workspace = QHBoxLayout(workspace)
        layout_workspace.setContentsMargins(0, 0, 0, 0)
        layout_workspace.setSpacing(0)

        self._painel_arvore = QFrame()
        self._painel_arvore.setObjectName("barraLateral")
        self._painel_arvore.setMinimumWidth(240)
        self._painel_arvore.setMaximumWidth(320)
        layout_arvore = QVBoxLayout(self._painel_arvore)
        layout_arvore.setContentsMargins(12, 14, 12, 12)
        layout_arvore.setSpacing(8)
        self._rotulo_projeto = QLabel("Nenhum projeto aberto")
        self._rotulo_projeto.setObjectName("marca")
        self._rotulo_projeto.setWordWrap(True)
        layout_arvore.addWidget(self._rotulo_projeto)
        rotulo_conteudo = QLabel("CONTEÚDO DO PROJETO")
        rotulo_conteudo.setObjectName("contextoNavegacao")
        layout_arvore.addWidget(rotulo_conteudo)

        self._arvore_projeto = QTreeWidget()
        self._arvore_projeto.setHeaderHidden(True)
        self._arvore_projeto.setIndentation(16)
        self._arvore_projeto.currentItemChanged.connect(self._selecionar_item_arvore)
        layout_arvore.addWidget(self._arvore_projeto, 1)

        self._botao_criar_conjunto = QPushButton("Nova aba / conjunto")
        self._botao_criar_conjunto.clicked.connect(self._criar_conjunto)
        self._botao_novo_grafico = QPushButton("Novo gráfico")
        self._botao_novo_grafico.setProperty("role", "primary")
        self._botao_novo_grafico.clicked.connect(self._novo_grafico)
        layout_arvore.addWidget(self._botao_criar_conjunto)
        layout_arvore.addWidget(self._botao_novo_grafico)
        layout_workspace.addWidget(self._painel_arvore)

        painel_principal = QWidget()
        layout_principal = QVBoxLayout(painel_principal)
        layout_principal.setContentsMargins(20, 16, 16, 12)
        layout_principal.setSpacing(10)
        self._rotulo_visualizacao = QLabel("Abra ou crie um projeto .vt")
        self._rotulo_visualizacao.setObjectName("tituloPagina")
        layout_principal.addWidget(self._rotulo_visualizacao)
        self._rotulo_subvisualizacao = QLabel(
            "Selecione um conjunto ou gráfico na árvore do projeto."
        )
        self._rotulo_subvisualizacao.setObjectName("descricaoPagina")
        layout_principal.addWidget(self._rotulo_subvisualizacao)

        self._area_visualizacao = QStackedWidget()
        self._pagina_visualizacao_vazia = QLabel(
            "O conteúdo selecionado será exibido aqui."
        )
        self._pagina_visualizacao_vazia.setObjectName("emptyState")
        self._pagina_visualizacao_vazia.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._area_visualizacao.addWidget(self._pagina_visualizacao_vazia)

        pagina_dados = QWidget()
        layout_dados = QVBoxLayout(pagina_dados)
        layout_dados.setContentsMargins(0, 0, 0, 0)
        controles_dados = QHBoxLayout()
        self._rotulo_conjunto = QLabel("Nenhum conjunto selecionado")
        self._rotulo_conjunto.setStyleSheet("font-weight: 600; color: #49665D;")
        self._botao_adicionar_linha = QPushButton("Adicionar linha")
        self._botao_adicionar_linha.clicked.connect(self._adicionar_linha)
        self._botao_excluir_linha = QPushButton("Excluir linha")
        self._botao_excluir_linha.setProperty("role", "danger")
        self._botao_excluir_linha.clicked.connect(self._excluir_linha)
        self._botao_salvar_dados = QPushButton("Salvar alterações")
        self._botao_salvar_dados.setProperty("role", "primary")
        self._botao_salvar_dados.clicked.connect(self._salvar_dados)
        for botao in (
            self._botao_adicionar_linha,
            self._botao_excluir_linha,
            self._botao_salvar_dados,
        ):
            controles_dados.addWidget(botao)
        controles_dados.insertWidget(0, self._rotulo_conjunto)
        controles_dados.addStretch()
        layout_dados.addLayout(controles_dados)
        self._tabela_dados = QTableWidget()
        self._tabela_dados.setAlternatingRowColors(True)
        self._tabela_dados.itemChanged.connect(self._marcar_dados_alterados)
        layout_dados.addWidget(self._tabela_dados, 1)
        self._rotulo_estado_dados = QLabel("Abra um projeto para editar seus conjuntos.")
        self._rotulo_estado_dados.setObjectName("descricaoPagina")
        layout_dados.addWidget(self._rotulo_estado_dados)
        self._area_visualizacao.addWidget(pagina_dados)

        pagina_grafico = QWidget()
        layout_grafico = QVBoxLayout(pagina_grafico)
        layout_grafico.setContentsMargins(0, 0, 0, 0)
        acoes_grafico = QHBoxLayout()
        self._rotulo_grafico_selecionado = QLabel("")
        self._rotulo_grafico_selecionado.setStyleSheet(
            "font-weight: 600; color: #49665D;"
        )
        self._botao_excluir_grafico = QPushButton("Excluir gráfico")
        self._botao_excluir_grafico.setProperty("role", "danger")
        self._botao_excluir_grafico.clicked.connect(self._excluir_grafico_selecionado)
        acoes_grafico.addWidget(self._rotulo_grafico_selecionado)
        acoes_grafico.addStretch()
        acoes_grafico.addWidget(self._botao_excluir_grafico)
        layout_grafico.addLayout(acoes_grafico)
        self._rotulo_graficos = QLabel("")
        self._rotulo_graficos.setObjectName("descricaoPagina")
        layout_grafico.addWidget(self._rotulo_graficos)
        self._web_graficos = QWebEngineView()
        self._web_graficos.setMinimumHeight(500)
        layout_grafico.addWidget(self._web_graficos, 1)
        self._area_visualizacao.addWidget(pagina_grafico)

        layout_principal.addWidget(self._area_visualizacao, 1)
        layout_workspace.addWidget(painel_principal, 1)
        self._adicionar_pagina("workspace", workspace)
        self._atualizar_estado_editor()

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
        rotulos = {"inicio": "Início", "workspace": "Projeto"}
        self.statusBar().showMessage(f"Área: {rotulos.get(chave, chave)}")

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
            self._carregar_projeto(caminho)

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
        formato = self._dialogo_selecionar_formato()
        if not formato:
            return
        configuracao_formato = carregar_configuracao_formato(formato)
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
            return criar_projeto(
                caminho,
                formato,
                configuracao_formato["dataset_name"],
                configuracao_formato["columns"],
            )

        self._executar_em_segundo_plano(
            criar_arquivo,
            lambda _resultado: self._projeto_criado(caminho),
            lambda erro: QMessageBox.critical(self, "Erro ao criar projeto", str(erro)),
            "Criando projeto...",
        )

    def _dialogo_selecionar_formato(self):
        dialogo = QDialog(self)
        dialogo.setWindowTitle("Formato do projeto")
        dialogo.setMinimumWidth(380)
        layout = QVBoxLayout(dialogo)
        titulo = QLabel("Escolha o formato de análise")
        titulo.setObjectName("tituloPagina")
        layout.addWidget(titulo)
        descricao = QLabel(
            "O formato define as colunas iniciais e os gráficos automáticos deste conjunto."
        )
        descricao.setWordWrap(True)
        descricao.setObjectName("descricaoPagina")
        layout.addWidget(descricao)
        seletor = QComboBox()
        seletor.addItem("IRAS · infecções relacionadas à assistência", "IRAS")
        seletor.addItem("ISC · infecção de sítio cirúrgico", "ISC")
        layout.addWidget(seletor)
        botoes = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        botoes.accepted.connect(dialogo.accept)
        botoes.rejected.connect(dialogo.reject)
        layout.addWidget(botoes)
        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return None
        return seletor.currentData()

    def _projeto_criado(self, caminho):
        registrar_auditoria(f"CREATE_PROJECT | caminho={caminho}")
        self._carregar_projeto(caminho)

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

    def _carregar_projeto(self, caminho):
        caminho_resolvido = str(Path(caminho).resolve())
        if self._dados_sujos and caminho_resolvido != self._caminho_banco:
            self._confirmar_alteracoes_nao_salvas(
                lambda: self._carregar_projeto(caminho_resolvido)
            )
            return

        caminho_anterior = self._caminho_banco

        def ler_projeto():
            formato = obter_formato_projeto(caminho_resolvido)
            conjuntos = listar_conjuntos(caminho_resolvido)
            formatos = {
                nome: obter_formato_conjunto(caminho_resolvido, nome)
                for nome in conjuntos
            }
            configuracoes = {
                nome: carregar_configuracao_formato(formato_dataset)
                for nome, formato_dataset in formatos.items()
            }
            return formato, conjuntos, formatos, configuracoes, listar_graficos(caminho_resolvido)

        def projeto_carregado(resultado):
            if self._caminho_banco != caminho_resolvido:
                return
            (
                self._formato_projeto,
                self._conjuntos_projeto,
                self._formatos_conjuntos,
                self._configuracoes_conjuntos,
                self._graficos_projeto,
            ) = resultado
            self._registrar_projeto_recente(caminho_resolvido)
            self._rotulo_projeto.setText(
                f"{Path(caminho_resolvido).name}  ·  {self._formato_projeto}"
            )
            self._atualizar_arvore_projeto()
            self._paginas.setCurrentIndex(self._indice_paginas["workspace"])
            self.statusBar().showMessage(
                f"Projeto aberto · {len(self._conjuntos_projeto)} conjunto(s)", 5000
            )

        def projeto_falhou(erro):
            if self._caminho_banco == caminho_resolvido:
                self._caminho_banco = caminho_anterior
            QMessageBox.critical(self, "Não foi possível abrir o projeto", str(erro))

        self._caminho_banco = caminho_resolvido
        self._executar_em_segundo_plano(
            ler_projeto,
            projeto_carregado,
            projeto_falhou,
            f"Abrindo projeto {Path(caminho_resolvido).name}...",
        )

    def _atualizar_arvore_projeto(
        self, selecionar_conjunto=None, selecionar_grafico_id=None
    ):
        self._arvore_projeto.clear()
        if not self._caminho_banco:
            self._rotulo_projeto.setText("Nenhum projeto aberto")
            self._area_visualizacao.setCurrentWidget(self._pagina_visualizacao_vazia)
            self._atualizar_acoes_projeto()
            return

        raiz = QTreeWidgetItem([Path(self._caminho_banco).name])
        raiz.setData(0, Qt.ItemDataRole.UserRole, "project")
        raiz.setToolTip(0, self._caminho_banco)
        self._arvore_projeto.addTopLevelItem(raiz)
        for nome_conjunto in self._conjuntos_projeto:
            item_conjunto = QTreeWidgetItem([nome_conjunto])
            item_conjunto.setData(0, Qt.ItemDataRole.UserRole, "dataset")
            item_conjunto.setData(0, Qt.ItemDataRole.UserRole + 1, nome_conjunto)
            raiz.addChild(item_conjunto)

            formato = self._formatos_conjuntos.get(nome_conjunto, self._formato_projeto)
            config_formato = self._configuracoes_conjuntos.get(
                nome_conjunto, carregar_configuracao_formato(formato)
            )
            colunas = listar_colunas_conjunto(self._caminho_banco, nome_conjunto)
            graficos_automaticos = config_formato.get("automatic_charts", {})
            if graficos_automaticos:
                grupo_auto = QTreeWidgetItem(["Gráficos automáticos"])
                grupo_auto.setData(0, Qt.ItemDataRole.UserRole, "group")
                item_conjunto.addChild(grupo_auto)
                for nome, config in graficos_automaticos.items():
                    if config.get("num") not in colunas or config.get("den") not in colunas:
                        continue
                    item_grafico = QTreeWidgetItem([nome])
                    item_grafico.setData(0, Qt.ItemDataRole.UserRole, "automatic_chart")
                    item_grafico.setData(0, Qt.ItemDataRole.UserRole + 1, nome_conjunto)
                    config_grafico = dict(config)
                    config_grafico["_date_column"] = config_formato.get("date_column")
                    item_grafico.setData(0, Qt.ItemDataRole.UserRole + 2, config_grafico)
                    grupo_auto.addChild(item_grafico)

            graficos_salvos = [
                grafico for grafico in self._graficos_projeto
                if grafico["dataset"] == nome_conjunto
            ]
            if graficos_salvos:
                grupo_salvos = QTreeWidgetItem(["Gráficos do projeto"])
                grupo_salvos.setData(0, Qt.ItemDataRole.UserRole, "group")
                item_conjunto.addChild(grupo_salvos)
                for grafico in graficos_salvos:
                    item_grafico = QTreeWidgetItem([grafico["name"]])
                    item_grafico.setData(0, Qt.ItemDataRole.UserRole, "saved_chart")
                    item_grafico.setData(0, Qt.ItemDataRole.UserRole + 1, grafico)
                    item_grafico.setData(0, Qt.ItemDataRole.UserRole + 3, grafico["id"])
                    grupo_salvos.addChild(item_grafico)
            item_conjunto.setExpanded(True)
            if selecionar_conjunto == nome_conjunto:
                self._arvore_projeto.setCurrentItem(item_conjunto)
        raiz.setExpanded(True)
        if selecionar_grafico_id is not None:
            for indice in range(self._arvore_projeto.topLevelItemCount()):
                selecionado = self._buscar_grafico_na_arvore(
                    self._arvore_projeto.topLevelItem(indice), selecionar_grafico_id
                )
                if selecionado:
                    self._arvore_projeto.setCurrentItem(selecionado)
                    break
        if not self._arvore_projeto.currentItem() and raiz.childCount():
            self._arvore_projeto.setCurrentItem(raiz.child(0))
        self._atualizar_acoes_projeto()

    def _buscar_grafico_na_arvore(self, item, identificador):
        if item.data(0, Qt.ItemDataRole.UserRole + 3) == identificador:
            return item
        for indice in range(item.childCount()):
            encontrado = self._buscar_grafico_na_arvore(item.child(indice), identificador)
            if encontrado:
                return encontrado
        return None

    def _selecionar_item_arvore(self, atual, _anterior=None):
        if not atual:
            return
        tipo_item = atual.data(0, Qt.ItemDataRole.UserRole)
        if tipo_item == "dataset":
            nome_conjunto = atual.data(0, Qt.ItemDataRole.UserRole + 1)
            self._conjunto_selecionado = nome_conjunto
            self._carregar_conjunto(nome_conjunto)
        elif tipo_item in {"automatic_chart", "saved_chart"}:
            nome_conjunto = atual.data(0, Qt.ItemDataRole.UserRole + 1)
            config = atual.data(0, Qt.ItemDataRole.UserRole + 2)
            if tipo_item == "saved_chart":
                grafico = atual.data(0, Qt.ItemDataRole.UserRole + 1)
                nome_conjunto = grafico["dataset"]
                config = grafico["config"]
                self._grafico_selecionado_id = grafico["id"]
            else:
                self._grafico_selecionado_id = None
            self._conjunto_selecionado = nome_conjunto
            self._mostrar_grafico(nome_conjunto, atual.text(0), config)
        else:
            self._area_visualizacao.setCurrentWidget(self._pagina_visualizacao_vazia)
            self._rotulo_visualizacao.setText(atual.text(0))
            self._rotulo_subvisualizacao.setText(self._caminho_banco or "")
        self._atualizar_acoes_projeto()

    def _mostrar_grafico(self, nome_conjunto, nome_grafico, configuracao):
        self._solicitacao_graficos += 1
        solicitacao = self._solicitacao_graficos
        caminho = self._caminho_banco
        self._rotulo_visualizacao.setText(nome_grafico)
        self._rotulo_subvisualizacao.setText(f"{nome_conjunto}  ·  gerando gráfico...")
        self._rotulo_graficos.setText("Calculando um gráfico...")
        self._area_visualizacao.setCurrentIndex(2)
        self._executar_em_segundo_plano(
            lambda: self._gerar_grafico_projeto(
                caminho, nome_conjunto, nome_grafico, configuracao
            ),
            lambda pagina: self._grafico_pronto(solicitacao, caminho, pagina),
            lambda erro: self._erro_ao_gerar_grafico(solicitacao, erro),
            f"Gerando {nome_grafico}...",
        )

    def _gerar_grafico_projeto(
        self, caminho, nome_conjunto, nome_grafico, configuracao
    ):
        import pandas as pd

        colunas, linhas = ler_conjunto(caminho, nome_conjunto)
        dados = pd.DataFrame(linhas, columns=colunas)
        if dados.empty:
            raise ValueError(f"O conjunto '{nome_conjunto}' não tem linhas.")
        if configuracao.get("kind") == "mapped":
            figura = construir_grafico_mapeado(dados, configuracao)
        else:
            from core.charts import construir_figura_plotly
            from core.statistics import calcular_analises_completas

            coluna_data = configuracao.get("_date_column")
            if coluna_data not in dados.columns:
                coluna_data = next(
                    (
                        coluna for coluna in colunas
                        if any(token in str(coluna).casefold() for token in ("data", "mês", "mes", "ano"))
                    ),
                    colunas[0],
                )
            fase_configurada = configuracao.get("fase", "Nenhuma")
            fase = fase_configurada if fase_configurada in dados.columns else "Nenhuma"
            resultado = calcular_analises_completas(
                dados,
                coluna_data,
                configuracao["num"],
                configuracao["den"],
                fase,
                configuracao.get("tipo", "U"),
                configuracao.get("mult", 100),
            )
            if resultado.df.empty:
                raise ValueError("Não há linhas válidas para calcular o gráfico.")
            figura = construir_figura_plotly(
                resultado.df,
                configuracao,
                nome_grafico,
                coluna_data,
                resultado.fases,
                fase,
                False,
                nome_conjunto,
            )
        return self._salvar_figuras_plotly([figura])

    def _grafico_pronto(self, solicitacao, caminho, pagina):
        if solicitacao != self._solicitacao_graficos or caminho != self._caminho_banco:
            return
        self._web_graficos.setUrl(QUrl.fromLocalFile(str(pagina)))
        self._rotulo_graficos.setText("Gráfico pronto.")
        self._rotulo_subvisualizacao.setText(
            f"{self._conjunto_selecionado}  ·  visualização individual"
        )

    def _erro_ao_gerar_grafico(self, solicitacao, erro):
        if solicitacao != self._solicitacao_graficos:
            return
        self._web_graficos.setHtml("")
        self._rotulo_graficos.setText(f"Não foi possível gerar o gráfico: {erro}")

    def _excluir_grafico_selecionado(self):
        identificador = getattr(self, "_grafico_selecionado_id", None)
        if identificador is None or not self._caminho_banco:
            return
        resposta = QMessageBox.question(
            self,
            "Excluir gráfico",
            "Excluir este gráfico do projeto? Os dados da aba serão preservados.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resposta != QMessageBox.StandardButton.Yes:
            return
        caminho = self._caminho_banco
        self._executar_em_segundo_plano(
            lambda: excluir_grafico(caminho, identificador),
            lambda _resultado: self._grafico_excluido(),
            lambda erro: QMessageBox.critical(self, "Erro ao excluir gráfico", str(erro)),
            "Excluindo gráfico...",
        )

    def _grafico_excluido(self):
        self._graficos_projeto = listar_graficos(self._caminho_banco)
        self._grafico_selecionado_id = None
        self._atualizar_arvore_projeto(selecionar_conjunto=self._conjunto_selecionado)
        self.statusBar().showMessage("Gráfico removido do projeto.", 5000)

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
        if not hasattr(self, "_botao_novo_grafico"):
            return
        projeto_aberto = bool(
            self._caminho_banco
            and Path(self._caminho_banco).suffix.lower() == ".vt"
            and Path(self._caminho_banco).is_file()
        )
        ocupado = self._tarefas_ativas > 0
        self._botao_criar_conjunto.setEnabled(projeto_aberto and not ocupado)
        self._botao_novo_grafico.setEnabled(
            projeto_aberto and bool(getattr(self, "_conjuntos_projeto", [])) and not ocupado
        )

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
        tem_conjunto = bool(getattr(self, "_conjunto_selecionado", None))
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
            self.statusBar().showMessage("Abra um projeto .vt antes de criar uma aba.", 5000)
            return

        formato = self._dialogo_selecionar_formato()
        if not formato:
            return
        configuracao_formato = carregar_configuracao_formato(formato)
        nome, confirmado = QInputDialog.getText(
            self,
            "Nova aba do projeto",
            f"Nome da aba {formato}:",
            text=configuracao_formato["dataset_name"],
        )
        if not confirmado:
            return
        nome = nome.strip()
        if not nome:
            QMessageBox.warning(self, "Nome obrigatório", "Informe um nome para a aba.")
            return
        caminho = self._caminho_banco

        def criar_aba():
            criar_conjunto(caminho, nome, configuracao_formato["columns"])
            registrar_formato_conjunto(caminho, nome, formato)

        self._executar_em_segundo_plano(
            criar_aba,
            lambda _resultado: self._conjunto_criado(nome, formato),
            lambda erro: QMessageBox.warning(self, "Não foi possível criar o conjunto", str(erro)),
            f"Criando aba {formato}...",
        )

    def _conjunto_criado(self, nome, formato):
        self._conjuntos_projeto.append(nome)
        self._formatos_conjuntos[nome] = formato
        self._configuracoes_conjuntos[nome] = carregar_configuracao_formato(formato)
        registrar_auditoria(f"CREATE_DATASET | projeto={self._caminho_banco} | conjunto={nome}")
        self._atualizar_arvore_projeto(selecionar_conjunto=nome)
        self.statusBar().showMessage(f"Aba '{nome}' criada no formato {formato}.", 5000)

    def _novo_grafico(self):
        if not self._caminho_banco or not getattr(self, "_conjuntos_projeto", None):
            QMessageBox.information(self, "Projeto necessário", "Abra um projeto com ao menos uma aba.")
            return

        dialogo = QDialog(self)
        dialogo.setWindowTitle("Novo gráfico")
        dialogo.setMinimumWidth(470)
        layout = QVBoxLayout(dialogo)
        titulo = QLabel("Mapeie as variáveis do gráfico")
        titulo.setObjectName("tituloPagina")
        layout.addWidget(titulo)

        formulario = QFormLayout()
        nome = QLineEdit("Gráfico sem título")
        conjunto = QComboBox()
        conjunto.addItems(self._conjuntos_projeto)
        if getattr(self, "_conjunto_selecionado", None):
            conjunto.setCurrentText(self._conjunto_selecionado)
        campo_x = QComboBox()
        campo_y = QComboBox()
        agrupamento = QComboBox()
        visualizacao = QComboBox()
        visualizacao.addItems(["Linha", "Dispersão", "Barras"])

        def atualizar_campos(nome_conjunto):
            colunas = listar_colunas_conjunto(self._caminho_banco, nome_conjunto)
            valor_x, valor_y = campo_x.currentText(), campo_y.currentText()
            for seletor in (campo_x, campo_y):
                seletor.clear()
                seletor.addItems(colunas)
            if valor_x in colunas:
                campo_x.setCurrentText(valor_x)
            if valor_y in colunas:
                campo_y.setCurrentText(valor_y)
            selecionado = agrupamento.currentData()
            agrupamento.clear()
            agrupamento.addItem("Sem agrupamento", None)
            for coluna in colunas:
                agrupamento.addItem(coluna, coluna)
            indice = agrupamento.findData(selecionado)
            agrupamento.setCurrentIndex(max(indice, 0))

        conjunto.currentTextChanged.connect(atualizar_campos)
        atualizar_campos(conjunto.currentText())
        formulario.addRow("Nome", nome)
        formulario.addRow("Aba de dados", conjunto)
        formulario.addRow("Eixo X", campo_x)
        formulario.addRow("Eixo Y", campo_y)
        formulario.addRow("Agrupamento", agrupamento)
        formulario.addRow("Visualização", visualizacao)
        layout.addLayout(formulario)

        botoes = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        botoes.accepted.connect(dialogo.accept)
        botoes.rejected.connect(dialogo.reject)
        layout.addWidget(botoes)
        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return
        nome_grafico = nome.text().strip()
        if not nome_grafico:
            QMessageBox.warning(self, "Nome obrigatório", "Informe um nome para o gráfico.")
            return

        definicao = {
            "kind": "mapped",
            "nome": nome_grafico,
            "x": campo_x.currentText(),
            "y": campo_y.currentText(),
            "agrupamento": agrupamento.currentData(),
            "visualizacao": visualizacao.currentText(),
        }
        caminho = self._caminho_banco
        nome_conjunto = conjunto.currentText()
        self._executar_em_segundo_plano(
            lambda: salvar_grafico(caminho, nome_grafico, nome_conjunto, definicao),
            lambda identificador: self._grafico_criado(identificador),
            lambda erro: QMessageBox.critical(self, "Não foi possível criar o gráfico", str(erro)),
            "Salvando gráfico no projeto...",
        )

    def _grafico_criado(self, identificador):
        self._graficos_projeto = listar_graficos(self._caminho_banco)
        self._atualizar_arvore_projeto(selecionar_grafico_id=identificador)
        self.statusBar().showMessage("Gráfico salvo no projeto.", 5000)

    def _carregar_conjunto(self, nome_conjunto):
        if not self._caminho_banco or not nome_conjunto:
            self._atualizar_estado_editor()
            return
        caminho = self._caminho_banco

        def conjunto_carregado(resultado):
            if caminho != self._caminho_banco or nome_conjunto != self._conjunto_selecionado:
                return
            colunas, linhas = resultado
            self._colunas_conjunto = colunas
            self._rotulo_conjunto.setText(nome_conjunto)
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
            self._rotulo_visualizacao.setText(nome_conjunto)
            self._rotulo_subvisualizacao.setText(
                f"{len(linhas)} linha(s)  ·  formato {self._formatos_conjuntos.get(nome_conjunto, self._formato_projeto)}"
            )
            self._area_visualizacao.setCurrentIndex(1)
            self._atualizar_estado_editor()

        def erro_ao_carregar(erro):
            if caminho == self._caminho_banco and nome_conjunto == self._conjunto_selecionado:
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
        nome_conjunto = getattr(self, "_conjunto_selecionado", None)
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


class DataFrameTableModel(QAbstractTableModel):
    dadosAlterados = Signal()

    def __init__(self, dataframe=None, parent=None):
        super().__init__(parent)
        self._dataframe = dataframe.copy() if dataframe is not None else None

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() or self._dataframe is None:
            return 0
        return len(self._dataframe.index)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid() or self._dataframe is None:
            return 0
        return len(self._dataframe.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._dataframe is None:
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        value = self._dataframe.iat[index.row(), index.column()]
        if value is None:
            return ""
        try:
            if value != value:
                return ""
        except (TypeError, ValueError):
            pass
        return str(value)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if self._dataframe is None or section >= len(self._dataframe.columns):
                return None
            return str(self._dataframe.columns[section])
        return str(section + 1)

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if (
            not index.isValid()
            or role != Qt.ItemDataRole.EditRole
            or self._dataframe is None
        ):
            return False
        coluna = self._dataframe.columns[index.column()]
        tipo = str(self._dataframe[coluna].dtype)
        convertido = value
        try:
            if value == "":
                convertido = None
            elif tipo.startswith("int"):
                convertido = int(value)
            elif tipo.startswith("float"):
                convertido = float(value)
            elif tipo.startswith("datetime"):
                import pandas as pd

                convertido = pd.to_datetime(value, errors="raise")
        except (TypeError, ValueError):
            self._dataframe[coluna] = self._dataframe[coluna].astype(object)
            convertido = value
        try:
            self._dataframe.iat[index.row(), index.column()] = convertido
        except (TypeError, ValueError):
            self._dataframe[coluna] = self._dataframe[coluna].astype(object)
            self._dataframe.iat[index.row(), index.column()] = value
        self.dataChanged.emit(
            index,
            index,
            [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole],
        )
        self.dadosAlterados.emit()
        return True

    def set_dataframe(self, dataframe):
        self.beginResetModel()
        self._dataframe = dataframe.copy()
        self.endResetModel()

    def dataframe(self):
        return self._dataframe.copy() if self._dataframe is not None else None

    def add_column(self, nome):
        if self._dataframe is None:
            return False
        coluna = len(self._dataframe.columns)
        self.beginInsertColumns(QModelIndex(), coluna, coluna)
        self._dataframe[nome] = None
        self.endInsertColumns()
        self.dadosAlterados.emit()
        return True

    def add_row(self):
        if self._dataframe is None:
            return False
        linha = len(self._dataframe.index)
        self.beginInsertRows(QModelIndex(), linha, linha)
        self._dataframe.loc[linha] = [None] * len(self._dataframe.columns)
        self.endInsertRows()
        self.dadosAlterados.emit()
        return True

    def remove_row(self, linha):
        if self._dataframe is None or linha < 0 or linha >= len(self._dataframe.index):
            return False
        self.beginRemoveRows(QModelIndex(), linha, linha)
        self._dataframe = self._dataframe.drop(self._dataframe.index[linha]).reset_index(drop=True)
        self.endRemoveRows()
        self.dadosAlterados.emit()
        return True


class CollapsibleWidget(QFrame):
    def __init__(self, titulo, expanded=False, parent=None):
        super().__init__(parent)
        self.setObjectName("collapsibleWidget")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._botao = QPushButton()
        self._botao.setFlat(True)
        self._botao.setProperty("class", "expanderHeader")
        self._titulo = titulo
        self._botao.clicked.connect(self.toggle)
        layout.addWidget(self._botao)

        self._conteudo = QFrame()
        self._conteudo.setObjectName("expanderContent")
        self._layout_conteudo = QVBoxLayout(self._conteudo)
        self._layout_conteudo.setContentsMargins(12, 10, 12, 12)
        layout.addWidget(self._conteudo)
        self._expanded = bool(expanded)
        self._sync_state()

    def content_layout(self):
        return self._layout_conteudo

    def toggle(self):
        self._expanded = not self._expanded
        self._sync_state()

    def _sync_state(self):
        self._botao.setText(("▾  " if self._expanded else "▸  ") + self._titulo)
        self._conteudo.setVisible(self._expanded)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VinTab | Painel de Monitoramento")
        self.setMinimumSize(1100, 720)
        self._datasets = {}
        self._source_path = None
        self._source_kind = None
        self._active_dataset = None
        self._figures = {}
        self._dirty = False
        self._saving = False
        self._close_after_save = False
        self._custom_charts = self._load_custom_charts()
        self._auth_failures = 0
        self._auth_blocked_until = None
        self._task_pool = QThreadPool(self)

        self._build_streamlit_layout()
        self._connect_actions()
        self._load_initial_source()

    def closeEvent(self, event):
        if not self._dirty:
            event.accept()
            return
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Alterações não salvas")
        dialog.setText("A tabela tem alterações que ainda não foram gravadas.")
        save = dialog.addButton("Salvar e sair", QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("Descartar e sair", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("Continuar editando", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked == save:
            event.ignore()
            self._close_after_save = True
            self._save_dataframe()
        elif clicked == discard:
            event.accept()
        else:
            event.ignore()

    def _build_streamlit_layout(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = QFrame()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(300)
        sidebar = QVBoxLayout(self._sidebar)
        sidebar.setContentsMargins(20, 22, 20, 20)
        sidebar.setSpacing(12)

        brand = QLabel("VinTab")
        brand.setObjectName("brand")
        sidebar.addWidget(brand)
        description = QLabel("Cartas de controle e indicadores assistenciais")
        description.setObjectName("mutedText")
        description.setWordWrap(True)
        sidebar.addWidget(description)

        self._botao_base = QPushButton("Carregar base_vintab.db")
        self._botao_upload = QPushButton("Upload Excel / SQLite")
        sidebar.addWidget(self._botao_base)
        sidebar.addWidget(self._botao_upload)

        dataset_label = QLabel("📑 Aba (Dataset)")
        dataset_label.setObjectName("fieldLabel")
        sidebar.addWidget(dataset_label)
        self._combo_datasets = QComboBox()
        self._combo_datasets.setMinimumHeight(38)
        sidebar.addWidget(self._combo_datasets)
        self._rotulo_fonte = QLabel("Nenhuma fonte carregada")
        self._rotulo_fonte.setObjectName("mutedText")
        self._rotulo_fonte.setWordWrap(True)
        sidebar.addWidget(self._rotulo_fonte)

        self._expander_exportar = CollapsibleWidget("📥 Exportar gráficos")
        export_layout = self._expander_exportar.content_layout()
        export_layout.addWidget(QLabel("Seleciona os gráficos a incluir no pacote."))
        self._lista_exportacao = QListWidget()
        self._lista_exportacao.setMaximumHeight(190)
        export_layout.addWidget(self._lista_exportacao)
        self._combo_exportacao = QComboBox()
        self._combo_exportacao.addItems(["HTML interativo", "JSON Plotly"])
        export_layout.addWidget(self._combo_exportacao)
        self._botao_exportar = QPushButton("Gerar ZIP")
        self._botao_exportar.setProperty("role", "primary")
        export_layout.addWidget(self._botao_exportar)
        sidebar.addWidget(self._expander_exportar)
        sidebar.addStretch(1)

        self._rotulo_estado = QLabel("Pronto")
        self._rotulo_estado.setObjectName("mutedText")
        self._rotulo_estado.setWordWrap(True)
        sidebar.addWidget(self._rotulo_estado)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setMaximumHeight(8)
        self._progress.hide()
        sidebar.addWidget(self._progress)
        root.addWidget(self._sidebar)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("mainScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._content.setObjectName("mainContent")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(36, 28, 36, 40)
        self._content_layout.setSpacing(18)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._page_title = QLabel("CCIH | Painel de Monitoramento")
        self._page_title.setObjectName("pageTitle")
        self._content_layout.addWidget(self._page_title)

        self._expander_edicao = CollapsibleWidget(
            "✏️ Edição de Dados em Tempo Real", expanded=False
        )
        editor = self._expander_edicao.content_layout()
        editor_actions = QHBoxLayout()
        self._botao_adicionar_coluna = QPushButton("Adicionar coluna")
        self._botao_adicionar_linha = QPushButton("Adicionar linha")
        self._botao_excluir_linha = QPushButton("Excluir linha")
        self._botao_salvar = QPushButton("💾 Salvar Alterações na Tabela")
        self._botao_salvar.setProperty("role", "primary")
        for button in (
            self._botao_adicionar_coluna,
            self._botao_adicionar_linha,
            self._botao_excluir_linha,
            self._botao_salvar,
        ):
            editor_actions.addWidget(button)
        editor_actions.addStretch(1)
        editor.addLayout(editor_actions)

        self._modelo_dados = DataFrameTableModel()
        self._tabela = QTableView()
        self._tabela.setObjectName("dataEditor")
        self._tabela.setModel(self._modelo_dados)
        self._tabela.setAlternatingRowColors(True)
        self._tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tabela.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._tabela.verticalHeader().setDefaultSectionSize(25)
        self._tabela.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._tabela.horizontalHeader().setDefaultSectionSize(118)
        editor.addWidget(self._tabela)
        self._content_layout.addWidget(self._expander_edicao)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("dashboardTabs")
        self._tab_iras = QWidget()
        self._tab_iras_layout = QVBoxLayout(self._tab_iras)
        self._tab_iras_layout.setContentsMargins(0, 14, 0, 8)
        self._tab_iras_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._tab_lab = QWidget()
        self._tab_lab_layout = QVBoxLayout(self._tab_lab)
        self._tab_lab_layout.setContentsMargins(0, 14, 0, 8)
        self._tab_lab_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._create_custom_chart_form()
        self._tab_lab_layout.addWidget(self._expander_novo_grafico)
        self._lab_chart_container = QWidget()
        self._lab_charts_layout = QVBoxLayout(self._lab_chart_container)
        self._lab_charts_layout.setContentsMargins(0, 8, 0, 8)
        self._lab_charts_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._tab_lab_layout.addWidget(self._lab_chart_container)
        self._tabs.addTab(self._tab_iras, "Controle de Infecção")
        self._tabs.addTab(self._tab_lab, "Laboratório Livre")
        self._content_layout.addWidget(self._tabs)
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)

        self.setFont(QFont("Segoe UI", 10))
        self._apply_streamlit_stylesheet()

    def _connect_actions(self):
        self._botao_base.clicked.connect(self._load_default_database)
        self._botao_upload.clicked.connect(self._choose_source)
        self._combo_datasets.currentIndexChanged.connect(self.update_dashboard)
        self._botao_adicionar_coluna.clicked.connect(self._add_column)
        self._botao_adicionar_linha.clicked.connect(self._add_row)
        self._botao_excluir_linha.clicked.connect(self._remove_row)
        self._botao_salvar.clicked.connect(self._save_dataframe)
        self._botao_exportar.clicked.connect(self._export_selected_charts)
        self._modelo_dados.dadosAlterados.connect(self._dataframe_edited)
        self._tabs.currentChanged.connect(self._tab_changed)

    def _apply_streamlit_stylesheet(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget#mainContent { background: #FFFFFF; color: #262730; }
            QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
            QFrame#sidebar { background: #F0F2F6; border: none; }
            QLabel#brand { color: #262730; font-size: 17pt; font-weight: 700; }
            QLabel#pageTitle { color: #262730; font-size: 20pt; font-weight: 650; }
            QLabel#mutedText { color: #647078; font-size: 9pt; }
            QLabel#fieldLabel { color: #374151; font-weight: 600; padding-top: 6px; }
            QPushButton {
                background: #FFFFFF; color: #27313A; border: 1px solid #D6DCE2;
                border-radius: 6px; padding: 9px 12px; text-align: left;
            }
            QPushButton:hover { background: #F7F9FB; border-color: #9AA9B8; }
            QPushButton:pressed { background: #E8EEF4; }
            QPushButton:disabled { color: #9AA2AA; background: #F3F4F6; }
            QPushButton[role="primary"] { background: #FF4B4B; color: #FFFFFF; border: none; }
            QPushButton[role="primary"]:hover { background: #E63E3E; }
            QComboBox, QLineEdit, QSpinBox {
                background: #FFFFFF; border: 1px solid #C9D1D9; border-radius: 6px;
                padding: 8px 10px; color: #262730; min-height: 20px;
            }
            QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border: 1px solid #FF4B4B; }
            QScrollArea#mainScroll { background: #FFFFFF; border: none; }
            QScrollArea#mainScroll QWidget#mainContent { background: #FFFFFF; }
            QTabWidget#dashboardTabs::pane { background: #FFFFFF; border: none; }
            QTabWidget#dashboardTabs QTabBar::tab {
                background: transparent; border: none; border-bottom: 2px solid transparent;
                color: #647078; padding: 11px 17px; margin-right: 5px;
            }
            QTabWidget#dashboardTabs QTabBar::tab:selected {
                color: #262730; border-bottom: 2px solid #FF4B4B; font-weight: 600;
            }
            QTabWidget#dashboardTabs QTabBar::tab:hover { color: #262730; }
            QFrame#collapsibleWidget { background: #FFFFFF; border: 1px solid #E5E8EB; border-radius: 6px; }
            QPushButton[class="expanderHeader"] {
                background: #FFFFFF; border: none; border-radius: 6px;
                padding: 11px 13px; color: #27313A; font-weight: 600; text-align: left;
            }
            QPushButton[class="expanderHeader"]:hover { background: #F8F9FA; }
            QFrame#expanderContent { background: #FFFFFF; border: none; }
            QTableView#dataEditor {
                background: #FFFFFF; alternate-background-color: #FAFBFC;
                border: none; gridline-color: #E5E7EB; selection-background-color: #E9EEF4;
                selection-color: #1F2937; outline: none;
            }
            QTableView#dataEditor::item { border: none; padding: 3px 6px; }
            QHeaderView::section {
                background: #FFFFFF; color: #4B5563; border: none;
                border-bottom: 1px solid #DCE1E6; padding: 6px 8px; font-weight: 600;
            }
            QListWidget { background: transparent; border: none; }
            QListWidget::item { padding: 4px 2px; }
            QProgressBar { background: #E7EAF0; border: none; border-radius: 4px; min-height: 7px; }
            QProgressBar::chunk { background: #FF4B4B; border-radius: 4px; }
            """
        )

    @staticmethod
    def _limpar_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
            elif child_layout:
                MainWindow._limpar_layout(child_layout)

    def _resource_root(self):
        return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))

    def _load_initial_source(self):
        from core.config import ARQUIVO_SALVO

        saved = Path(ARQUIVO_SALVO).resolve()
        if saved.is_file():
            self._load_excel_path(saved)
            return
        database = self._resource_root() / "base_vintab.db"
        if database.is_file():
            self._load_database_path(database)
        else:
            self._set_status("Carregue um banco ou uma planilha para começar.")

    def _load_default_database(self):
        path = self._resource_root() / "base_vintab.db"
        if not path.is_file():
            QMessageBox.warning(self, "Base não encontrada", f"Não foi encontrado: {path}")
            return
        self._load_database_path(path)

    def _choose_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Carregar dados",
            str(Path.home()),
            "Dados (*.xlsx *.xls *.db *.sqlite *.sqlite3 *.vt);;Todos os arquivos (*)",
        )
        if not path:
            return
        if Path(path).suffix.lower() in {".xlsx", ".xls"}:
            self._load_excel_path(Path(path))
        else:
            self._load_database_path(Path(path))

    def _load_excel_path(self, path):
        from storage.data_layer import ler_arquivo

        path = Path(path).resolve()
        self._run_background(
            lambda: ler_arquivo(str(path)),
            lambda result: self._excel_loaded(path, result),
            lambda error: QMessageBox.critical(self, "Falha ao carregar Excel", str(error)),
            f"A carregar {path.name}...",
        )

    def _excel_loaded(self, path, result):
        datasets, raw = result
        self._source_path = path
        self._source_kind = "excel"
        self._source_bytes = raw
        self._accept_datasets(datasets, str(path))

    def _load_database_path(self, path):
        path = Path(path).resolve()

        def read_database():
            from storage.project_store import ler_conjunto, listar_conjuntos

            names = listar_conjuntos(path)
            datasets = {}
            for name in names:
                columns, rows = ler_conjunto(path, name)
                datasets[name] = self._pandas().DataFrame(rows, columns=columns)
            return datasets

        self._run_background(
            read_database,
            lambda datasets: self._database_loaded(path, datasets),
            lambda error: QMessageBox.critical(self, "Falha ao carregar base", str(error)),
            f"A carregar {path.name}...",
        )

    def _database_loaded(self, path, datasets):
        self._source_path = path
        self._source_kind = "sqlite"
        self._source_bytes = None
        self._accept_datasets(datasets, str(path))

    @staticmethod
    def _pandas():
        import pandas as pd

        return pd

    def _accept_datasets(self, datasets, source_label):
        self._datasets = {str(name): frame.copy() for name, frame in datasets.items()}
        self._combo_datasets.blockSignals(True)
        self._combo_datasets.clear()
        self._combo_datasets.addItems(list(self._datasets))
        self._combo_datasets.blockSignals(False)
        self._rotulo_fonte.setText(source_label)
        self._set_status(f"{len(self._datasets)} aba(s) carregada(s).")
        self.update_dashboard()

    def update_dashboard(self, *_args):
        dataset = self._combo_datasets.currentText()
        if not dataset or dataset not in self._datasets:
            self._active_dataset = None
            self._modelo_dados.set_dataframe(self._pandas().DataFrame())
            self._page_title.setText("CCIH | Painel de Monitoramento")
            self._render_chart_group(self._tab_iras_layout, {})
            self._render_chart_group(self._lab_charts_layout, {})
            self._refresh_export_choices()
            return

        self._active_dataset = dataset
        dataframe = self._datasets[dataset].copy()
        self._modelo_dados.set_dataframe(dataframe)
        self._page_title.setText(f"CCIH | {dataset}")
        self._update_chart_fields(dataframe)

        from core.config import CONFIG_INDICADORES

        self._figures = {}
        self._render_chart_group(self._tab_iras_layout, CONFIG_INDICADORES, dataframe, dataset)
        self._lab_charts_layout = self._find_lab_chart_layout()
        self._render_chart_group(
            self._lab_charts_layout, self._custom_charts, dataframe, dataset
        )
        self._refresh_export_choices()
        self._set_status(f"Aba ativa: {dataset} · {len(dataframe)} linha(s)")

    def _find_lab_chart_layout(self):
        return self._lab_chart_container.layout()

    def _update_chart_fields(self, dataframe):
        columns = [str(column) for column in dataframe.columns]
        mathematical = columns or [""]
        date_column = next(
            (column for column in columns if any(key in column.lower() for key in ("data", "mês", "mes"))),
            columns[0] if columns else "",
        )
        for combo, selected in (
            (self._combo_numerador, None),
            (self._combo_denominador, None),
            (self._combo_fase, "Nenhuma"),
        ):
            combo.blockSignals(True)
            combo.clear()
            if combo is self._combo_fase:
                combo.addItem("Nenhuma")
            combo.addItems(mathematical)
            combo.blockSignals(False)
        self._combo_numerador.setCurrentIndex(0)
        self._combo_denominador.setCurrentIndex(1 if len(columns) > 1 else 0)
        if date_column in columns:
            self._combo_fase.setCurrentText(date_column if "fase" in date_column.casefold() else "Nenhuma")

    def _render_chart_group(self, layout, chart_definitions, dataframe=None, dataset=None):
        self._limpar_layout(layout)
        if dataframe is None or not dataset:
            return
        available = set(str(column) for column in dataframe.columns)
        compatible = {
            name: config for name, config in chart_definitions.items()
            if config.get("num") in available and config.get("den") in available
        }
        if not compatible:
            info = QLabel("Nenhum gráfico configurado corresponde às colunas desta aba.")
            info.setObjectName("mutedText")
            layout.addWidget(info)
            layout.addStretch(1)
            return
        for name, config in compatible.items():
            card = QFrame()
            card.setObjectName("chartCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            title_row = QHBoxLayout()
            title = QLabel(str(name))
            title.setObjectName("chartTitle")
            title_row.addWidget(title)
            title_row.addStretch(1)
            if layout is self._lab_chart_container.layout():
                delete = QPushButton("Eliminar")
                delete.setProperty("role", "danger")
                delete.clicked.connect(lambda _checked=False, chart_name=name: self._delete_custom_chart(chart_name))
                title_row.addWidget(delete)
            card_layout.addLayout(title_row)
            try:
                figure = self._make_figure(name, config, dataframe, dataset)
                self._figures[str(name)] = figure
                view = QWebEngineView(card)
                view.setMinimumHeight(440)
                html_path = self._figure_html(str(name), figure)
                view.setUrl(QUrl.fromLocalFile(str(html_path)))
                card_layout.addWidget(view)
            except Exception as error:
                message = QLabel(f"Não foi possível gerar este gráfico: {error}")
                message.setWordWrap(True)
                message.setObjectName("errorText")
                card_layout.addWidget(message)
            layout.addWidget(card)
        layout.addStretch(1)

    def _make_figure(self, name, config, dataframe, dataset):
        from core.charts import construir_figura_plotly
        from core.statistics import calcular_analises_completas

        columns = [str(column) for column in dataframe.columns]
        date_column = next(
            (column for column in columns if any(key in column.lower() for key in ("data", "mês", "mes"))),
            columns[0],
        )
        phase_configured = config.get("fase", "Nenhuma")
        phase = phase_configured if phase_configured in dataframe.columns else "Nenhuma"
        result = calcular_analises_completas(
            dataframe.copy(), date_column, config["num"], config["den"], phase,
            config.get("tipo", "U"), config.get("mult", 100),
        )
        if result.df.empty:
            raise ValueError("Não há linhas válidas para calcular o indicador.")
        figure = construir_figura_plotly(
            result.df, config, str(name), date_column, result.fases, phase,
            False, str(dataset),
        )
        return figure

    def _web_directory(self):
        directory = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "VinTab" / "web"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _figure_html(self, name, figure):
        from plotly.offline import get_plotlyjs

        directory = self._web_directory()
        plotly_js = directory / "plotly.min.js"
        if not plotly_js.exists():
            plotly_js.write_text(get_plotlyjs(), encoding="utf-8")
        slug = "".join(char if char.isalnum() else "_" for char in name)[:50]
        identity = f"{self._active_dataset or ''}|{name}".encode("utf-8")
        token = hashlib.sha1(identity).hexdigest()[:10]
        path = directory / f"{slug}_{token}.html"
        html = figure.to_html(full_html=True, include_plotlyjs=False, config={"responsive": True, "displaylogo": False})
        html = html.replace("</head>", "<script src='plotly.min.js'></script></head>", 1)
        path.write_text(html, encoding="utf-8")
        return path

    def _refresh_export_choices(self):
        checked = {
            self._lista_exportacao.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self._lista_exportacao.count())
            if self._lista_exportacao.item(index).checkState() == Qt.CheckState.Checked
        }
        self._lista_exportacao.clear()
        for name in self._figures:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if name in checked else Qt.CheckState.Unchecked
            )
            self._lista_exportacao.addItem(item)
        self._botao_exportar.setEnabled(bool(self._figures))

    def _export_selected_charts(self):
        from io import BytesIO
        import zipfile
        from plotly.utils import PlotlyJSONEncoder

        names = [
            self._lista_exportacao.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self._lista_exportacao.count())
            if self._lista_exportacao.item(index).checkState() == Qt.CheckState.Checked
        ]
        if not names:
            QMessageBox.information(self, "Exportação", "Seleciona pelo menos um gráfico.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar gráficos", "graficos_vintab.zip", "Arquivo ZIP (*.zip)"
        )
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        formato = self._combo_exportacao.currentText()
        buffer = BytesIO()
        try:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                if formato == "HTML interativo":
                    archive.write(self._web_directory() / "plotly.min.js", "plotly.min.js")
                    for name in names:
                        html = self._figure_html(name, self._figures[name])
                        slug = "".join(char if char.isalnum() else "_" for char in name)[:50]
                        archive.write(html, f"{slug}.html")
                else:
                    for name in names:
                        slug = "".join(char if char.isalnum() else "_" for char in name)[:50]
                        archive.writestr(
                            f"{slug}.json",
                            json.dumps(self._figures[name].to_plotly_json(), cls=PlotlyJSONEncoder, ensure_ascii=False),
                        )
            Path(path).write_bytes(buffer.getvalue())
        except Exception as error:
            QMessageBox.critical(self, "Falha na exportação", str(error))
            return
        self._set_status(f"{len(names)} gráfico(s) exportado(s).")

    def _create_custom_chart_form(self):
        self._expander_novo_grafico = CollapsibleWidget("➕ Adicionar Novo Gráfico", expanded=True)
        form = self._expander_novo_grafico.content_layout()
        fields = QFormLayout()
        self._custom_name = QLineEdit()
        self._custom_name.setPlaceholderText("Ex.: Taxa ISC-CL Ortopedia")
        self._custom_type = QComboBox()
        self._custom_type.addItems(["U (Densidade/Taxas)", "P (Proporções/Porcentagem)"])
        self._combo_numerador = QComboBox()
        self._combo_denominador = QComboBox()
        self._combo_fase = QComboBox()
        self._combo_fase.addItem("Nenhuma")
        self._custom_multiplier = QSpinBox()
        self._custom_multiplier.setRange(1, 1000000)
        self._custom_multiplier.setValue(1000)
        self._custom_title = QLineEdit()
        self._custom_title.setPlaceholderText("Ex.: TDI, Taxa de Eventos")
        self._custom_suffix = QLineEdit()
        self._custom_suffix.setPlaceholderText("Ex.: %, por 10k")
        for label, widget in (
            ("Nome do gráfico", self._custom_name),
            ("Comportamento", self._custom_type),
            ("Numerador", self._combo_numerador),
            ("Denominador", self._combo_denominador),
            ("Agrupamento de fase", self._combo_fase),
            ("Multiplicador", self._custom_multiplier),
            ("Título secundário", self._custom_title),
            ("Sufixo", self._custom_suffix),
        ):
            fields.addRow(label, widget)
        form.addLayout(fields)
        self._botao_criar_grafico = QPushButton("Criar Gráfico e Salvar")
        self._botao_criar_grafico.setProperty("role", "primary")
        self._botao_criar_grafico.clicked.connect(self._create_custom_chart)
        form.addWidget(self._botao_criar_grafico)

    def _create_custom_chart(self):
        name = self._custom_name.text().strip()
        numerator = self._combo_numerador.currentText()
        denominator = self._combo_denominador.currentText()
        if not name:
            QMessageBox.warning(self, "Nome obrigatório", "Dá um nome ao gráfico antes de salvar.")
            return
        if not numerator or not denominator or numerator == denominator:
            QMessageBox.warning(self, "Variáveis inválidas", "Escolhe numerador e denominador diferentes.")
            return
        self._custom_charts[name] = {
            "num": numerator,
            "den": denominator,
            "fase": self._combo_fase.currentText(),
            "tipo": self._custom_type.currentText(),
            "mult": self._custom_multiplier.value(),
            "titulo_grafico": self._custom_title.text(),
            "sufixo_valor": self._custom_suffix.text(),
        }
        try:
            self._custom_charts_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._custom_charts_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self._custom_charts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._custom_charts_path)
        except OSError as error:
            QMessageBox.critical(self, "Falha ao salvar gráficos", str(error))
            return
        self._custom_name.clear()
        already_in_lab = self._tabs.currentWidget() is self._tab_lab
        self._tabs.setCurrentWidget(self._tab_lab)
        if already_in_lab:
            self.update_dashboard()
        self._set_status(f"Gráfico '{name}' criado e adicionado ao Laboratório Livre.")

    def _delete_custom_chart(self, name):
        if name not in self._custom_charts:
            return
        answer = QMessageBox.question(
            self,
            "Eliminar gráfico",
            f"Eliminar '{name}' de custom_charts.json?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._custom_charts.pop(name, None)
        self._custom_charts_path.write_text(
            json.dumps(self._custom_charts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.update_dashboard()

    def _load_custom_charts(self):
        if getattr(sys, "frozen", False):
            path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VinTab" / "custom_charts.json"
            packaged = self._resource_root() / "custom_charts.json"
            if not path.exists() and packaged.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(packaged.read_bytes())
        else:
            path = self._resource_root() / "custom_charts.json"
        self._custom_charts_path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _dataframe_edited(self):
        if self._active_dataset:
            self._datasets[self._active_dataset] = self._modelo_dados.dataframe()
            self._dirty = True
            self._botao_salvar.setEnabled(not self._saving)
            self._set_status("Alterações pendentes. Salva para atualizar a fonte de dados e os gráficos.")

    def _add_column(self):
        if not self._active_dataset:
            return
        name, accepted = QInputDialog.getText(self, "Adicionar coluna", "Nome da coluna:")
        name = name.strip()
        if not accepted or not name:
            return
        if name in self._modelo_dados.dataframe().columns:
            QMessageBox.warning(self, "Coluna existente", f"A coluna '{name}' já existe.")
            return
        self._modelo_dados.add_column(name)
        self.update_dashboard()

    def _add_row(self):
        self._modelo_dados.add_row()

    def _remove_row(self):
        row = self._tabela.currentIndex().row()
        if not self._modelo_dados.remove_row(row):
            QMessageBox.information(self, "Excluir linha", "Seleciona primeiro uma linha.")

    def _authenticate_admin(self):
        from core.auth import verificar_senha

        now = datetime.now()
        if self._auth_blocked_until and now < self._auth_blocked_until:
            seconds = int((self._auth_blocked_until - now).total_seconds())
            QMessageBox.warning(self, "Acesso bloqueado", f"Tenta novamente em {seconds}s.")
            return False
        password, accepted = QInputDialog.getText(
            self, "Autenticação", "Senha de administrador:", QLineEdit.EchoMode.Password
        )
        if not accepted:
            return False
        if verificar_senha(password):
            self._auth_failures = 0
            registrar_auditoria("AUTH | LOGIN_SUCCESS")
            return True
        self._auth_failures += 1
        registrar_auditoria(f"AUTH | LOGIN_FAIL | tentativa={self._auth_failures}")
        if self._auth_failures >= 5:
            self._auth_blocked_until = datetime.now() + timedelta(minutes=5)
            self._auth_failures = 0
            QMessageBox.critical(self, "Acesso bloqueado", "Muitas tentativas. Aguarda cinco minutos.")
        else:
            QMessageBox.warning(self, "Senha incorreta", f"Tentativa {self._auth_failures}/5.")
        return False

    def _save_dataframe(self):
        if not self._active_dataset or not self._source_path:
            QMessageBox.information(self, "Salvar dados", "Carrega uma fonte de dados editável.")
            return
        if not self._authenticate_admin():
            return
        frame = self._modelo_dados.dataframe()
        source = self._source_path
        dataset = self._active_dataset
        source_kind = self._source_kind
        if source_kind == "excel" and source.suffix.lower() != ".xlsx":
            target, _ = QFileDialog.getSaveFileName(
                self, "Salvar como Excel", str(source.with_suffix(".xlsx")), "Excel (*.xlsx)"
            )
            if not target:
                return
            if not target.lower().endswith(".xlsx"):
                target += ".xlsx"
            source = Path(target).resolve()

        self._combo_datasets.setEnabled(False)
        self._botao_upload.setEnabled(False)
        self._botao_base.setEnabled(False)
        self._tabela.setEnabled(False)
        self._botao_adicionar_coluna.setEnabled(False)
        self._botao_adicionar_linha.setEnabled(False)
        self._botao_excluir_linha.setEnabled(False)
        self._botao_salvar.setEnabled(False)
        self._saving = True

        def persist():
            if source_kind == "excel":
                from storage.data_layer import salvar_excel_multiaba

                salvar_excel_multiaba(str(source), frame, dataset, self._datasets)
            else:
                self._save_sqlite_dataset(frame, source, dataset)

        def saved(_result):
            self._source_path = source
            self._datasets[dataset] = frame.copy()
            self._dirty = False
            self._saving = False
            self._combo_datasets.setEnabled(True)
            self._botao_upload.setEnabled(True)
            self._botao_base.setEnabled(True)
            self._tabela.setEnabled(True)
            self._botao_adicionar_coluna.setEnabled(True)
            self._botao_adicionar_linha.setEnabled(True)
            self._botao_excluir_linha.setEnabled(True)
            self._botao_salvar.setEnabled(False)
            self.update_dashboard()
            self._set_status(f"{dataset} salvo com sucesso.")
            if self._close_after_save:
                self._close_after_save = False
                self.close()

        def save_failed(error):
            self._saving = False
            self._close_after_save = False
            self._combo_datasets.setEnabled(True)
            self._botao_upload.setEnabled(True)
            self._botao_base.setEnabled(True)
            self._tabela.setEnabled(True)
            self._botao_adicionar_coluna.setEnabled(True)
            self._botao_adicionar_linha.setEnabled(True)
            self._botao_excluir_linha.setEnabled(True)
            self._botao_salvar.setEnabled(self._dirty)
            QMessageBox.critical(self, "Falha ao salvar", str(error))

        self._run_background(
            persist, saved, save_failed, f"A salvar {dataset}..."
        )

    @staticmethod
    def _save_sqlite_dataset(dataframe, source_path, dataset):
        if not source_path or source_path.suffix.lower() not in {".db", ".sqlite", ".sqlite3", ".vt"}:
            raise ValueError("A fonte SQLite atual não pode ser editada.")
        import sqlite3

        def quote(name):
            return '"' + str(name).replace('"', '""') + '"'

        columns = [str(column) for column in dataframe.columns]
        table = quote(dataset)
        names = ", ".join(quote(column) for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        rows = []
        for row in dataframe.itertuples(index=False, name=None):
            values = []
            for value in row:
                if value is None or str(value) in {"nan", "NaT", "<NA>"}:
                    values.append(None)
                elif hasattr(value, "item"):
                    values.append(value.item())
                else:
                    values.append(value)
            if any(value is not None for value in values):
                rows.append(values)
        with closing(sqlite3.connect(source_path)) as connection:
            with connection:
                current = [
                    record[1]
                    for record in connection.execute(f"PRAGMA table_info({table})")
                ]
                if not set(current).issubset(columns):
                    raise ValueError("Remover colunas de uma fonte SQLite não é suportado.")
                for column in columns:
                    if column not in current:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {quote(column)} NUMERIC"
                        )
                connection.execute(f"DELETE FROM {table}")
                if rows:
                    connection.executemany(
                        f"INSERT INTO {table} ({names}) VALUES ({placeholders})", rows
                    )

    def _tab_changed(self, _index):
        if self._active_dataset:
            self.update_dashboard()

    def _set_status(self, message):
        self._rotulo_estado.setText(message)

    def _resource_root(self):
        return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))

    def _run_background(self, function, success, failure, message):
        self._set_status(message)
        self._progress.show()
        task = _Tarefa(function, success, failure)
        task.sinais.concluida.connect(self._task_finished)
        self._task_pool.start(task)

    @Slot(object, object, object, bool)
    def _task_finished(self, success, failure, result, failed):
        self._progress.hide()
        if failed:
            failure(result)
        else:
            success(result)
