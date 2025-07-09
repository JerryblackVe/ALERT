"""
Price Alert App – Alpha Vantage + CoinGecko
------------------------------------------------
Aplicación Streamlit orientada a usuarios sin experiencia técnica.
Ahora usa **Alpha Vantage** para acciones (plan free) y permite:
* Definir cuántas veces por día se chequea el precio (1‑1440).
* Enviar un **correo de prueba** para verificar la configuración.
Requisitos: Python 3.9+, `pip install streamlit requests`.
Ejecución: `streamlit run price_alert_app.py`
"""

import os
import json
import time
import threading
from datetime import datetime
from typing import Dict, List

import requests
import streamlit as st
from email.message import EmailMessage
import smtplib
import ssl

# ------------- Rutas y defaults -------------
CONFIG_PATH = "config.json"
WATCHLIST_PATH = "watchlist.json"
DEFAULT_CONFIG = {
    "av_key": "",          # Alpha Vantage API key
    "checks_per_day": 1440,  # 1440 = cada 60 s
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "",
    "smtp_pass": "",
    "email_to": ""
}

CRYPTO_SYMBOL_MAP = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "ada": "cardano",
    "sol": "solana",
}

# ------------- Helpers de persistencia -------------

def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default.copy()

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ------------- Cargar configuración y lista -------------
config: Dict = load_json(CONFIG_PATH, DEFAULT_CONFIG)
watchlist: List[Dict] = load_json(WATCHLIST_PATH, [])

# ------------- Funciones de precio -------------

def get_stock_price(symbol: str) -> float:
    """Último precio usando Alpha Vantage (GLOBAL_QUOTE)."""
    if not config["av_key"]:
        raise ValueError("Falta Alpha Vantage API key en Configuración.")
    url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={symbol.upper()}&apikey={config['av_key']}"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    try:
        return float(data["Global Quote"]["05. price"])
    except (KeyError, ValueError):
        raise ValueError(f"No se pudo obtener el precio para {symbol}")


def get_crypto_price(symbol: str) -> float:
    cid = CRYPTO_SYMBOL_MAP.get(symbol.lower(), symbol.lower())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()
    if cid not in data or "usd" not in data[cid]:
        raise ValueError(f"No se pudo obtener el precio para {symbol}")
    return float(data[cid]["usd"])


def current_price(item: Dict) -> float:
    return get_stock_price(item["symbol"]) if item["type"] == "stock" else get_crypto_price(item["symbol"])

# ------------- Envío de email -------------

def send_email(subject: str, body: str):
    if not all([config["smtp_user"], config["smtp_pass"], config["email_to"]]):
        st.error("⚠️ Falta configurar SMTP completo.")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config["smtp_user"]
    msg["To"] = config["email_to"]
    msg.set_content(body)
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], context=context) as server:
            server.login(config["smtp_user"], config["smtp_pass"])
            server.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Error enviando mail: {e}")
        return False

# ------------- Hilo de alertas -------------

def check_alerts():
    while True:
        refresh_seconds = max(10, int(86400 / config.get("checks_per_day", 1440)))
        triggered = []
        for item in watchlist:
            try:
                price = current_price(item)
                item["last"] = price
                cond_met = price >= item["target"] if item["direction"] == "above" else price <= item["target"]
                if cond_met and not item.get("triggered", False):
                    cond = "≥" if item["direction"] == "above" else "≤"
                    subject = f"Alerta {item['symbol'].upper()} {cond} {item['target']}"
                    body = (
                        f"Ticker: {item['symbol'].upper()}\n"
                        f"Precio actual: {price:.2f} USD\n"
                        f"Hora: {datetime.utcnow()} UTC"
                    )
                    send_email(subject, body)
                    item["triggered"] = True
                    triggered.append(item["symbol"])
            except Exception as e:
                item["error"] = str(e)
        if triggered:
            save_json(WATCHLIST_PATH, watchlist)
        time.sleep(refresh_seconds)

# Lanzar la thread una sola vez
if "alerts_thread" not in st.session_state:
    threading.Thread(target=check_alerts, daemon=True).start()
    st.session_state["alerts_thread"] = True

# ------------- UI (Streamlit) -------------
st.set_page_config(page_title="⏰ Price Alerts", layout="wide", initial_sidebar_state="expanded")

st.title("⏰ Price Alerts – Acciones & Cripto")

# --- Sidebar Configuración ---
with st.sidebar:
    st.header("⚙️ Configuración")
    config["av_key"] = st.text_input("Alpha Vantage API Key", value=config["av_key"], type="password")
    config["checks_per_day"] = st.number_input(
        "Chequeos por día (1‑1440)", min_value=1, max_value=1440, value=int(config["checks_per_day"]), step=1
    )
    st.markdown("---")
    st.subheader("SMTP / Gmail")
    config["smtp_user"] = st.text_input("Usuario Gmail", value=config["smtp_user"])
    config["smtp_pass"] = st.text_input("App Password Gmail", value=config["smtp_pass"], type="password")
    config["email_to"] = st.text_input("Enviar alertas a", value=config["email_to"])
    colA, colB = st.columns(2)
    with colA:
        if st.button("💾 Guardar configuración"):
            save_json(CONFIG_PATH, config)
            st.success("Configuración guardada ✔️")
    with colB:
        if st.button("📧 Enviar correo de prueba"):
            ok = send_email("Test Price Alerts", "Este es un correo de prueba desde tu app Price Alerts.")
            if ok:
                st.success("Correo de prueba enviado ✔️")

# ------------- Sección: agregar activo -------------
st.markdown("## Agregar activo a seguimiento")
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1:
    symbol = st.text_input("Ticker / Símbolo", placeholder="AAPL o BTC")
with col2:
    asset_type = st.selectbox("Tipo", ["stock", "crypto"])
with col3:
    direction = st.selectbox("Condición", ["above", "below"], index=0)
with col4:
    target = st.number_input("Precio USD", min_value=0.0, step=0.01)

if st.button("➕ Agregar"):
    if symbol and target > 0:
        watchlist.append(
            {
                "symbol": symbol.upper(),
                "type": asset_type,
                "direction": direction,
                "target": target,
                "last": None,
                "triggered": False,
            }
        )
        save_json(WATCHLIST_PATH, watchlist)
        st.success(f"{symbol.upper()} agregado a la lista")
    else:
        st.error("Completa símbolo y precio objetivo > 0")

# ------------- Tabla de seguimiento -------------
st.markdown("## 📝 Lista de seguimiento")
if watchlist:
    table = []
    for item in watchlist:
        table.append(
            {
                "Ticker": item["symbol"],
                "Tipo": item["type"],
                "Condición": ("≥" if item["direction"] == "above" else "≤") + f" {item['target']}",
                "Último": f"{item['last']:.2f}" if item.get("last") else "-",
                "Estado": "🟢 Activa" if not item.get("triggered") else "🔔 Disparada",
                "Error": item.get("error", ""),
            }
        )
    st.table(table)
else:
    st.info("Aún no hay activos en seguimiento.")

st.caption("App Streamlit © 2025 – Fantastic Plastik")
