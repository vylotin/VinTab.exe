# audit.py
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _caminho_auditoria():
    diretorio = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    ) / "VinTab"
    diretorio.mkdir(parents=True, exist_ok=True)
    return diretorio / "ccih_audit.log"

def _setup_audit_logger():
    log = logging.getLogger("ccih_audit")
    if not log.handlers:
        # Define limite de 5MB por arquivo, guardando até 5 backups retroativos
        h = RotatingFileHandler(_caminho_auditoria(), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log

def registrar_auditoria(acao: str):
    # Sanitização contra Log Injection
    acao_limpa = acao.replace("\n", " ").replace("\r", " ")
    _setup_audit_logger().info(acao_limpa)


def ler_registros_auditoria(limite=1000):
    caminho = _caminho_auditoria()
    if not caminho.exists():
        return []
    resultado = []
    arquivos = [caminho]
    arquivos.extend(
        sorted(
            (
                arquivo
                for arquivo in caminho.parent.glob(f"{caminho.name}.*")
                if arquivo.suffix[1:].isdigit()
            ),
            key=lambda arquivo: int(arquivo.suffix[1:]),
        )
    )
    for arquivo_log in arquivos:
        with arquivo_log.open("r", encoding="utf-8") as arquivo:
            for registro in reversed(arquivo.readlines()):
                texto = registro.rstrip("\r\n")
                if texto.startswith("[") and "] " in texto:
                    horario, acao = texto[1:].split("] ", 1)
                    resultado.append((horario, acao))
                elif texto:
                    resultado.append(("", texto))
    resultado.sort(key=lambda registro: registro[0], reverse=True)
    return resultado[:limite]