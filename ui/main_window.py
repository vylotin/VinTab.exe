import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QInputDialog,
    QLabel,
    QHBoxLayout,
    QMainWindow,
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
    ler_conjunto,
    listar_conjuntos,
    salvar_conjunto,
)


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
        self.setMinimumSize(1400, 900)
        self._caminho_banco = None
        self._botoes_navegacao = {}
        self._pool_tarefas = QThreadPool(self)
        self._tarefas_ativas = 0
        self._salvando_dados = False

        self._criar_menu()
        self._criar_interface()

        raiz_recursos = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        banco_padrao = raiz_recursos / "base_vintab.db"
        if banco_padrao.is_file():
            self._carregar_banco(str(banco_padrao), exibir_erro=False)

    def _executar_em_segundo_plano(self, funcao, ao_concluir, ao_falhar=None, mensagem="Processando..."):
        tarefa = _Tarefa(funcao, ao_concluir, ao_falhar)
        tarefa.sinais.concluida.connect(self._finalizar_tarefa)
        self._tarefas_ativas += 1
        self.statusBar().showMessage(mensagem)
        self._pool_tarefas.start(tarefa)

    @Slot(object, object, object, bool)
    def _finalizar_tarefa(self, ao_concluir, ao_falhar, resultado, falhou):
        self._tarefas_ativas -= 1
        if self._tarefas_ativas == 0:
            self.statusBar().showMessage("Operação concluída", 3000)
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
            ("dados", "Dados"),
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

            QComboBox, QTableWidget {
                background-color: #252526;
                color: #F2F2F2;
                border: 1px solid #3A3A3A;
                gridline-color: #3A3A3A;
                selection-background-color: #464FEB;
            }

            QHeaderView::section {
                background-color: #303030;
                color: #F2F2F2;
                border: 1px solid #3A3A3A;
                padding: 6px;
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
        editar_dados = QPushButton("Abrir dados do projeto")
        editar_dados.clicked.connect(lambda: self._ir_para("dados"))
        projetos.layout().addWidget(editar_dados)
        projetos.layout().addStretch()
        self._adicionar_pagina("projetos", projetos)

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
        self._botao_excluir_linha.clicked.connect(self._excluir_linha)
        self._botao_salvar_dados = QPushButton("Salvar dados")
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
        dados.layout().addWidget(self._tabela_dados, 1)
        self._rotulo_estado_dados = QLabel("Abra um projeto .vt para criar e editar dados.")
        self._rotulo_estado_dados.setObjectName("descricaoPagina")
        dados.layout().addWidget(self._rotulo_estado_dados)
        self._adicionar_pagina("dados", dados)
        self._atualizar_estado_editor()

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

        def criar_arquivo():
            with closing(sqlite3.connect(caminho)):
                pass

        self._executar_em_segundo_plano(
            criar_arquivo,
            lambda _resultado: self._carregar_banco(caminho),
            lambda erro: QMessageBox.critical(self, "Erro ao criar projeto", str(erro)),
            "Criando projeto...",
        )

    def _carregar_banco(self, caminho, exibir_erro=True):
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
            self._rotulo_projeto.setText(
                self._caminho_banco if caminho.lower().endswith(".vt") else "Nenhum projeto .vt aberto"
            )
            self._lista_tabelas.clear()
            for nome_tabela in tabelas:
                self._lista_tabelas.addTopLevelItem(QTreeWidgetItem([nome_tabela]))
            self._atualizar_conjuntos()
            self._ir_para("banco")
            self.statusBar().showMessage(f"Banco aberto: {len(tabelas)} tabela(s)")

        def erro_ao_abrir(erro):
            if exibir_erro:
                QMessageBox.critical(self, "Não foi possível abrir o banco", str(erro))

        self._caminho_banco = str(Path(caminho).resolve())
        self._executar_em_segundo_plano(
            ler_banco,
            banco_carregado,
            erro_ao_abrir,
            "Abrindo banco de dados...",
        )

    def _fechar_banco(self):
        self._caminho_banco = None
        self._rotulo_banco.setText("Nenhum banco aberto")
        self._rotulo_projeto.setText("Nenhum projeto aberto")
        self._lista_tabelas.clear()
        self._atualizar_conjuntos()
        self.statusBar().showMessage("Banco fechado")

    def _atualizar_conjuntos(self, conjunto_selecionado=None):
        caminho = self._caminho_banco
        if not caminho:
            self._combo_conjuntos.clear()
            self._tabela_dados.clear()
            self._tabela_dados.setRowCount(0)
            self._tabela_dados.setColumnCount(0)
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
            self._atualizar_estado_editor()
            if self._combo_conjuntos.currentText():
                self._carregar_conjunto(self._combo_conjuntos.currentText())
            else:
                self._tabela_dados.clear()
                self._tabela_dados.setRowCount(0)
                self._tabela_dados.setColumnCount(0)

        def erro_ao_listar(erro):
            if caminho == self._caminho_banco:
                self._rotulo_estado_dados.setText(f"Não foi possível listar os conjuntos: {erro}")

        self._executar_em_segundo_plano(
            lambda: listar_conjuntos(caminho),
            conjuntos_carregados,
            erro_ao_listar,
            "Carregando conjuntos...",
        )

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
        self._botao_salvar_dados.setEnabled(projeto_editavel and tem_conjunto and not self._salvando_dados)

        if not self._caminho_banco:
            mensagem = "Abra um projeto .vt para criar e editar dados."
        elif not projeto_editavel:
            mensagem = "Banco aberto para consulta. A edição é permitida somente em projetos .vt."
        elif not tem_conjunto:
            mensagem = "Projeto vazio. Clique em Novo conjunto para criar sua primeira tabela."
        else:
            mensagem = "Edite as células e clique em Salvar dados para gravar no projeto."
        self._rotulo_estado_dados.setText(mensagem)

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

    def _excluir_linha(self):
        linha = self._tabela_dados.currentRow()
        if linha < 0:
            self.statusBar().showMessage("Selecione uma linha para excluir.", 5000)
            return
        self._tabela_dados.removeRow(linha)
        self.statusBar().showMessage("Linha removida da grade; clique em Salvar dados para confirmar.")

    def _salvar_dados(self):
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
            lambda _resultado: self._finalizar_salvamento(nome_conjunto, len(linhas)),
            lambda erro: self._finalizar_salvamento(nome_conjunto, len(linhas), erro),
            "Salvando dados...",
        )
        self._atualizar_estado_editor()

    def _finalizar_salvamento(self, nome_conjunto, quantidade_linhas, erro=None):
        self._salvando_dados = False
        self._atualizar_estado_editor()
        if erro:
            QMessageBox.critical(self, "Não foi possível salvar os dados", str(erro))
            return
        self.statusBar().showMessage(
            f"Dados salvos em {nome_conjunto}: {quantidade_linhas} linha(s)"
        )

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