import sqlite3
from contextlib import closing
from pathlib import Path

from PySide6.QtGui import QAction

from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QLabel,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("VinTab Desktop")
        self.setMinimumSize(1400, 900)
        self._caminho_banco = None
        self._botoes_navegacao = {}

        self._criar_menu()
        self._criar_interface()

        banco_padrao = Path(__file__).resolve().parents[1] / "base_vintab.db"
        if banco_padrao.is_file():
            self._carregar_banco(str(banco_padrao), exibir_erro=False)

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

        layout_principal = QVBoxLayout(widget_central)
        layout_principal.setContentsMargins(32, 24, 32, 24)
        layout_principal.setSpacing(20)

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
        corpo.setSpacing(24)
        layout_principal.addLayout(corpo, 1)

        navegacao = QVBoxLayout()
        navegacao.setSpacing(8)
        navegacao.setContentsMargins(0, 0, 0, 0)
        for chave, rotulo in (
            ("inicio", "Início"),
            ("banco", "Banco"),
            ("graficos", "Gráficos"),
            ("projetos", "Projetos .vt"),
            ("auditoria", "Auditoria"),
            ("excel", "Importação Excel"),
        ):
            botao = QPushButton(rotulo)
            botao.setCheckable(True)
            botao.setMinimumHeight(42)
            botao.clicked.connect(lambda checked=False, pagina=chave: self._ir_para(pagina))
            self._botoes_navegacao[chave] = botao
            navegacao.addWidget(botao)
        navegacao.addStretch()
        corpo.addLayout(navegacao, 0)

        self._paginas = QStackedWidget()
        corpo.addWidget(self._paginas, 1)
        self._indice_paginas = {}
        self._criar_paginas()
        self._ir_para("inicio")

        self.statusBar().showMessage("VinTab iniciado")

        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #1E1E1E;
            }

            QMenuBar {
                background-color: #252526;
                color: #F2F2F2;
                padding: 4px;
            }

            QMenuBar::item:selected, QMenu::item:selected {
                background-color: #464FEB;
            }

            QMenu {
                background-color: #252526;
                color: #F2F2F2;
                border: 1px solid #3A3A3A;
            }

            #titulo {
                color: #FFFFFF;
                font-size: 32px;
                font-weight: 700;
            }

            #subtitulo, #mensagemInicial {
                color: #A0A0A0;
                font-size: 15px;
            }

            #separador {
                color: #3A3A3A;
            }

            #rodape {
                color: #777777;
                font-size: 13px;
            }

            QFrame[frameShape="4"] {
                background-color: #2A2A2A;
                border: none;
                min-height: 1px;
                max-height: 1px;
            }

            QPushButton {
                background-color: #252526;
                border: 1px solid #3A3A3A;
                border-radius: 4px;
                color: #F2F2F2;
                padding: 18px;
                text-align: left;
            }

            QPushButton:hover {
                border-color: #464FEB;
            }

            QPushButton:checked {
                background-color: #30313D;
                border-color: #464FEB;
            }

            #tituloPagina {
                color: #FFFFFF;
                font-size: 23px;
                font-weight: 700;
            }

            #descricaoPagina {
                color: #A0A0A0;
                font-size: 14px;
            }

            QStatusBar {
                background-color: #252526;
                color: #F2F2F2;
            }
            """
        )

    def _criar_paginas(self):
        inicio = self._nova_pagina(
            "Visão geral",
            "Escolha uma área na navegação ou use um dos atalhos.",
        )
        acoes_inicio = QHBoxLayout()
        for titulo, pagina in (
            ("Abrir banco", "banco"),
            ("Projetos .vt", "projetos"),
            ("Gráficos", "graficos"),
        ):
            botao = QPushButton(titulo)
            botao.setMinimumHeight(64)
            botao.clicked.connect(lambda checked=False, destino=pagina: self._ir_para(destino))
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
            "Projetos .vt",
            "O projeto .vt é armazenado como um banco SQLite.",
        )
        acoes_projeto = QHBoxLayout()
        novo_projeto = QPushButton("Novo projeto .vt...")
        novo_projeto.clicked.connect(self._novo_projeto)
        abrir_projeto = QPushButton("Abrir projeto .vt...")
        abrir_projeto.clicked.connect(self._selecionar_projeto)
        acoes_projeto.addWidget(novo_projeto)
        acoes_projeto.addWidget(abrir_projeto)
        acoes_projeto.addStretch()
        projetos.layout().addLayout(acoes_projeto)
        self._rotulo_projeto = QLabel("Nenhum projeto aberto")
        self._rotulo_projeto.setWordWrap(True)
        projetos.layout().addWidget(self._rotulo_projeto)
        projetos.layout().addStretch()
        self._adicionar_pagina("projetos", projetos)

        graficos = self._nova_pagina(
            "Gráficos e indicadores",
            "A navegação está pronta. A seleção de indicador, os cálculos e a renderização das cartas serão conectados à lógica existente na etapa de gráficos.",
        )
        ir_banco = QPushButton("Ir para Banco de dados")
        ir_banco.clicked.connect(lambda: self._ir_para("banco"))
        graficos.layout().addWidget(ir_banco)
        graficos.layout().addStretch()
        self._adicionar_pagina("graficos", graficos)

        auditoria = self._nova_pagina(
            "Auditoria",
            "A tela está acessível. A consulta dos registros será ligada ao módulo de auditoria em uma etapa própria.",
        )
        auditoria.layout().addStretch()
        self._adicionar_pagina("auditoria", auditoria)

        excel = self._nova_pagina(
            "Importação Excel",
            "Selecione uma planilha para preparar a importação. A leitura das abas e a gravação no banco ainda não estão conectadas.",
        )
        selecionar_excel = QPushButton("Selecionar arquivo Excel...")
        selecionar_excel.clicked.connect(self._selecionar_excel)
        excel.layout().addWidget(selecionar_excel)
        self._rotulo_excel = QLabel("Nenhuma planilha selecionada")
        self._rotulo_excel.setWordWrap(True)
        excel.layout().addWidget(self._rotulo_excel)
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
            "",
            "Projetos VinTab (*.vt);;Todos os arquivos (*)",
        )
        if caminho:
            self._carregar_banco(caminho)

    def _novo_projeto(self):
        caminho, _ = QFileDialog.getSaveFileName(
            self,
            "Criar projeto VinTab",
            "",
            "Projeto VinTab (*.vt)",
        )
        if not caminho:
            return
        if not caminho.lower().endswith(".vt"):
            caminho += ".vt"
        if Path(caminho).exists():
            QMessageBox.warning(self, "Projeto existente", "Escolha um nome de arquivo que ainda não exista.")
            return

        try:
            with closing(sqlite3.connect(caminho)):
                pass
        except sqlite3.Error as erro:
            QMessageBox.critical(self, "Erro ao criar projeto", str(erro))
            return
        self._carregar_banco(caminho)

    def _carregar_banco(self, caminho, exibir_erro=True):
        try:
            uri = Path(caminho).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as conexao:
                tabelas = conexao.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                ).fetchall()
        except (sqlite3.Error, OSError) as erro:
            if exibir_erro:
                QMessageBox.critical(self, "Não foi possível abrir o banco", str(erro))
            return

        self._caminho_banco = str(Path(caminho).resolve())
        self._rotulo_banco.setText(self._caminho_banco)
        self._rotulo_projeto.setText(
            self._caminho_banco if caminho.lower().endswith(".vt") else "Nenhum projeto .vt aberto"
        )
        self._lista_tabelas.clear()
        for (nome_tabela,) in tabelas:
            self._lista_tabelas.addTopLevelItem(QTreeWidgetItem([nome_tabela]))
        self._ir_para("banco")
        self.statusBar().showMessage(f"Banco aberto: {len(tabelas)} tabela(s)")

    def _fechar_banco(self):
        self._caminho_banco = None
        self._rotulo_banco.setText("Nenhum banco aberto")
        self._rotulo_projeto.setText("Nenhum projeto aberto")
        self._lista_tabelas.clear()
        self.statusBar().showMessage("Banco fechado")

    def _selecionar_excel(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar planilha Excel",
            "",
            "Planilhas Excel (*.xlsx *.xls);;Todos os arquivos (*)",
        )
        if caminho:
            self._rotulo_excel.setText(caminho)
            self._ir_para("excel")
            self.statusBar().showMessage("Planilha selecionada; a importação ainda não foi conectada.")

    def _mostrar_indisponivel(self):
        self.statusBar().showMessage("Esta função será ativada nos próximos blocos do projeto.", 5000)