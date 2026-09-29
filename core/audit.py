# audit.py
import logging
from logging.handlers import RotatingFileHandler

def _setup_audit_logger():
    log = logging.getLogger("ccih_audit")
    if not log.handlers:
        # Define limite de 5MB por arquivo, guardando até 5 backups retroativos
        h = RotatingFileHandler("ccih_audit.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log

def registrar_auditoria(acao: str):
    # Sanitização contra Log Injection
    acao_limpa = acao.replace("\n", " ").replace("\r", " ")
    _setup_audit_logger().info(acao_limpa)