from PySide6.QtCore import Qt
from PySide6.QtGui import QAction

from PySide6.QtWidgets import (
    QFrame,
    QWidget,
    QLabel,
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QPushButton,
    QStatusBar,
)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("VinTab Desktop")

        self.setMinimumSize(1400, 900)

        self._criar_menu()
        self._criar_interface()

    def _criar_menu(self):
        menu_bar = self.menuBar()

        menu_banco = menu_bar.addMenu("Banco")
        self._adicionar_acao(menu_banco, "Abrir banco", self._mostrar_indisponivel)
        self._adicionar_acao(menu_banco, "Fechar banco", self._mostrar_indisponivel)

        menu_graficos = menu_bar.addMenu("Gráficos")
        self._adicionar_acao(menu_graficos, "Cartas de controle", self._mostrar_indisponivel)
        self._adicionar_acao(menu_graficos, "Indicadores", self._mostrar_indisponivel)

        menu_projetos = menu_bar.addMenu("Projetos .vt")
        self._adicionar_acao(menu_projetos, "Novo projeto", self._mostrar_indisponivel)
        self._adicionar_acao(menu_projetos, "Abrir projeto", self._mostrar_indisponivel)
        self._adicionar_acao(menu_projetos, "Salvar projeto", self._mostrar_indisponivel)

        menu_auditoria = menu_bar.addMenu("Auditoria")
        self._adicionar_acao(menu_auditoria, "Visualizar registros", self._mostrar_indisponivel)

        menu_importacao = menu_bar.addMenu("Importação Excel")
        self._adicionar_acao(menu_importacao, "Importar planilha", self._mostrar_indisponivel)

    @staticmethod
    def _adicionar_acao(menu, texto, callback):
        acao = QAction(texto, menu)
        acao.triggered.connect(callback)
        menu.addAction(acao)

    def _criar_interface(self):
        widget_central = QWidget(self)
        widget_central.setObjectName("widgetCentral")

        self.setCentralWidget(widget_central)

        layout = QVBoxLayout(widget_central)
        layout.setContentsMargins(48, 40, 48, 32)
        layout.setSpacing(24)

        cabecalho = QVBoxLayout()
        cabecalho.setSpacing(6)

        titulo = QLabel("VinTab")
        titulo.setObjectName("titulo")
        cabecalho.addWidget(titulo)

        subtitulo = QLabel("Cartas de controle e indicadores assistenciais")
        subtitulo.setObjectName("subtitulo")
        cabecalho.addWidget(subtitulo)
        layout.addLayout(cabecalho)

        separador = QFrame()
        separador.setFrameShape(QFrame.HLine)
        separador.setObjectName("separador")
        layout.addWidget(separador)

        mensagem = QLabel("Selecione uma opção no menu para começar.")
        mensagem.setObjectName("mensagemInicial")
        layout.addWidget(mensagem)

        atalhos = QHBoxLayout()
        atalhos.setSpacing(16)
        atalhos.addWidget(self._criar_atalho("Abrir banco", "Carregar um banco SQLite ou projeto .vt"))
        atalhos.addWidget(self._criar_atalho("Importar Excel", "Adicionar dados de uma planilha"))
        atalhos.addWidget(self._criar_atalho("Visualizar gráficos", "Acessar cartas e indicadores"))
        layout.addLayout(atalhos)
        layout.addStretch()

        rodape = QLabel("Nenhum banco carregado")
        rodape.setObjectName("rodape")
        layout.addWidget(rodape)

        self.setStatusBar(QStatusBar(self))

        self.statusBar().showMessage(
            "VinTab iniciado com sucesso"
        )

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

            QStatusBar {
                background-color: #252526;
                color: #F2F2F2;
            }
            """
        )

    def _criar_atalho(self, titulo, descricao):
        botao = QPushButton(f"{titulo}\n{descricao}")
        botao.setMinimumHeight(72)
        botao.clicked.connect(self._mostrar_indisponivel)
        return botao

    def _mostrar_indisponivel(self):
        self.statusBar().showMessage("Esta função será ativada nos próximos blocos do projeto.", 5000)