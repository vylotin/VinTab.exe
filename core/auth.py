# auth.py
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from .audit import registrar_auditoria

# Procura as chaves seguras nas variáveis de ambiente ou no secrets.toml do Streamlit
HASH_HEX = os.environ.get("CCIH_ADMIN_HASH", "")
SALT_HEX = os.environ.get("CCIH_ADMIN_SALT", "")

def verificar_senha(digitada: str) -> bool:
    # SE CONFIGUROU AS CHAVES NO SECRETS.TOML: Usa a criptografia segura de produção
    if HASH_HEX and SALT_HEX:
        salt = bytes.fromhex(SALT_HEX)
        h = hashlib.pbkdf2_hmac("sha256", digitada.encode(), salt, 310_000)
        return secrets.compare_digest(h.hex(), HASH_HEX)
    
    # MODO DE DESENVOLVIMENTO LOCAL: Se não houver chaves configuradas no ambiente,
    # valida diretamente a senha antiga para que possa testar sem bloqueios.
    return digitada == "ccih2026"

def render_login_widget() -> bool:
    """Renderiza a caixa de login e gerencia tentativas contra ataques automatizados."""
    import streamlit as st

    if st.session_state.get("admin_ok", False):
        return True

    if "tentativas" not in st.session_state:
        st.session_state.tentativas = 0
        st.session_state.bloqueado_ate = None

    agora = datetime.now()
    if st.session_state.bloqueado_ate and agora < st.session_state.bloqueado_ate:
        restante = int((st.session_state.bloqueado_ate - agora).total_seconds())
        st.error(f"🔒 Acesso bloqueado por excesso de tentativas. Aguarde {restante}s.")
        return False

    senha = st.text_input("Senha de administrador:", type="password", key="_admin_pwd_field")
    if st.button("🔓 Autenticar Privilégios", use_container_width=True):
        if verificar_senha(senha):
            st.session_state.admin_ok = True
            st.session_state.tentativas = 0
            registrar_auditoria("AUTH | LOGIN_SUCCESS")
            st.rerun()
        else:
            st.session_state.tentativas += 1
            registrar_auditoria(f"AUTH | LOGIN_FAIL | tentativa={st.session_state.tentativas}")
            if st.session_state.tentativas >= 5:
                st.session_state.bloqueado_ate = agora + timedelta(seconds=300)
                registrar_auditoria("AUTH | LOCKOUT_TRIGGERED")
                st.error("🔒 Credenciais incorretas repetidas vezes. Bloqueado por 5 minutos.")
            else:
                st.error(f"❌ Senha incorreta ({st.session_state.tentativas}/5).")
    return False