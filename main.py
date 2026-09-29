import sys

# Inicializa o import hook do six antes do hook instalado pelo PySide6.
import dateutil.tz

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("VinTab")
    app.setOrganizationName("VinTab")

    janela = MainWindow()
    janela.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())