"""
Price Alert App (acciones USA + criptomonedas)
------------------------------------------------
Aplicación Streamlit de escritorio / web sencilla pensada para usuarios sin experiencia técnica.
Permite:
* Crear una lista de seguimiento (ticker + precio objetivo + condición arriba/abajo + tipo de activo)
* Consultar precios en tiempo real (acciones vía Alpaca, cripto vía CoinGecko)
* Enviar alertas por correo cuando se cumple la condición
* Configurar claves API y credenciales SMTP desde la propia UI
* Guardar configuración y lista de seguimiento en archivos .json locales
Requisitos: Python 3.9+, pip install streamlit requests
Ejecución: streamlit run price_alert_app.py
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

# ------------- Constantes y utilidades -------------
CONFIG_PATH = "config.json"
WATCHLIST_PATH = "watchlist.json"
DEFAULT_CONFIG = {
    "alpaca_key": "",
    "alpaca_secret": "",
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

REFRESH_SECONDS = 60  # frecuencia de chequeo de precios

# ------------- Helpers persistencia -------------

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

# ------------- Configuración -------------
config: Dict = load_json(CONFIG_PATH, DEFAULT_CONFIG)
watchlist: List[Dict] = load_json(WATCHLIST_PATH, [])

# ------------- Precio de mercado -------------

def get_stock_price(symbol: str) -> float:
    """Obtiene último precio vía Alpaca."""
    if not config["alpaca_key"] or not config["alpaca_secret"]:
        raise ValueError("Falta configurar claves de Alpaca en la sección de Configuración.")
    url = f"https://data.alpaca.markets/v2/stocks/{symbol.upper()}/quotes/latest"
    headers = {
        "APCA-API-KEY-ID": config["alpaca_key"],
        "APCA-API-SECRET-KEY": config["alpaca_secret"],
    }
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    return float(data["quote"]["ap"])


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
        st.error("⚠️ Falta configurar credenciales SMTP y destinatario.")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config["smtp_user"]
    msg["To"] = config["email_to"]
    msg.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], context=context) as server:
        server.login(config["smtp_user"], config["smtp_pass"])
        server.send_message(msg)

# ------------- Lógica de alertas -------------

def check_alerts():
    while True:
        triggered = []
        for item in watchlist:
            try:
                price = current_price(item)
                item["last"] = price
                condition_met = (
                    price >= item["target"] if item["direction"] == "above" else price <= item["target"]
                )
                if condition_met and not item.get("triggered", False):
                    subject = f"Alerta {item['symbol'].upper()} @ {price:.2f} USD"
                    cond = "≥" if item["direction"] == "above" else "≤"
                    body = f"Se alcanzó la condición: precio {cond} {item['target']}\nTicker: {item['symbol'].upper()}\nPrecio actual: {price:.2f} USD\nHora: {datetime.utcnow()} UTC"
                    send_email(subject, body)
                    item["triggered"] = True
                    triggered.append(item["symbol"].upper())
            except Exception as e:
                item["error"] = str(e)
        if triggered:
            save_json(WATCHLIST_PATH, watchlist)
        time.sleep(REFRESH_SECONDS)

# Lanzar thread de alertas una sola vez
if "alerts_thread" not in st.session_state:
    thread = threading.Thread(target=check_alerts, daemon=True)
    thread.start()
    st.session_state["alerts_thread"] = True

# ------------- UI con Streamlit -------------
st.set_page_config(page_title="⏰ Price Alerts", layout="wide", initial_sidebar_state="expanded")

st.title("⏰ Price Alerts – Acciones & Cripto")

# --- Sidebar de configuración ---
with st.sidebar:
    st.header("⚙️ Configuración")
    config["alpaca_key"] = st.text_input("Alpaca API Key", value=config["alpaca_key"], type="password")
    config["alpaca_secret"] = st.text_input("Alpaca API Secret", value=config["alpaca_secret"], type="password")
    st.markdown("---")
    st.subheader("SMTP / Gmail")
    config["smtp_user"] = st.text_input("Usuario Gmail", value=config["smtp_user"])
    config["smtp_pass"] = st.text_input("App Password Gmail", value=config["smtp_pass"], type="password")
    config["email_to"] = st.text_input("Enviar alertas a", value=config["email_to"])
    if st.button("💾 Guardar configuración"):
        save_json(CONFIG_PATH, config)
        st.success("Configuración guardada ✔️")

st.markdown("## Agregar activo a seguimiento")
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1:
    symbol = st.text_input("Ticker / Símbolo", placeholder="AAPL o BTC")
with col2:
    asset_type = st.selectbox("Tipo", ["stock", "crypto"])
with col3:
    direction = st.selectbox("Condición", ["above", "below"], index=0, help="above = precio sube por encima; below = baja por debajo")
with col4:
    target = st.number_input("Precio USD", min_value=0.0, step=0.01)

if st.button("➕ Agregar"):
    if symbol and target > 0:
        new_item = {
            "symbol": symbol.upper(),
            "type": asset_type,
            "direction": direction,
            "target": target,
            "last": None,
            "triggered": False,
        }
        watchlist.append(new_item)
        save_json(WATCHLIST_PATH, watchlist)
        st.success(f"{symbol.upper()} agregado a la lista")
    else:
        st.error("Completa símbolo y precio objetivo > 0")

st.markdown("## 📝 Lista de seguimiento")
if watchlist:
    table = []
    for item in watchlist:
        row = {
            "Ticker": item["symbol"],
            "Tipo": item["type"],
            "Condición": ("≥" if item["direction"] == "above" else "≤") + f" {item['target']}",
            "Último": f"{item['last']:.2f}" if item.get("last") else "-",
            "Estado": "🟢 Activa" if not item.get("triggered") else "🔔 Disparada",
            "Error": item.get("error", "")
        }
        table.append(row)
    st.table(table)
else:
    st.info("Aún no hay activos en seguimiento.")

st.caption("App local – Education/DIY — Fantastic Plastik © 2025")
