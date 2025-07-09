"""
Price Alert App – Alpha Vantage + CoinGecko (v3‑fix2)
----------------------------------------------------
• Botón 🗑️ para eliminar activos.
• Lista persiste en `watchlist.json`.
• Tabla en vivo con autorefresco.
• Sintaxis corregida (f‑string cerrada).
"""

import os
import json
import time
import threading
from datetime import datetime
from typing import Dict, List

import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from email.message import EmailMessage
import smtplib
import ssl

# ---------- Constantes ----------
CONFIG_PATH = "config.json"
WATCHLIST_PATH = "watchlist.json"
DEFAULT_CONFIG = {
    "av_key": "",
    "checks_per_day": 1440,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "",
    "smtp_pass": "",
    "email_to": "",
}

CRYPTO_SYMBOL_MAP = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "ada": "cardano",
    "sol": "solana",
}

# ---------- Utilidades JSON ----------

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

# ---------- Cargar config y watchlist ----------
config: Dict = load_json(CONFIG_PATH, DEFAULT_CONFIG)
watchlist: List[Dict] = load_json(WATCHLIST_PATH, [])

# ---------- Funciones de precio ----------

def get_stock_price(symbol: str) -> float:
    if not config["av_key"]:
        raise ValueError("Falta Alpha Vantage API key en Configuración.")
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

# ---------- Email ----------

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

# ---------- Hilo de alertas ----------

def check_alerts():
    while True:
        refresh_s = max(10, int(86400 / config.get("checks_per_day", 1440)))
        for item in watchlist:
            try:
                price = current_price(item)
                item["last"] = price
                cond = price >= item["target"] if item["direction"] == "above" else price <= item["target"]
                if cond and not item.get("triggered", False):
                    op = "≥" if item["direction"] == "above" else "≤"
                    subject = f"Alerta {item['symbol'].upper()} {op} {item['target']}"
                    body = (
                        f"Ticker: {item['symbol'].upper()}\n"
                        f"Precio actual: {price:.2f} USD\n"
                        f"Hora: {datetime.utcnow()} UTC"
                    )
                    send_email(subject, body)
                    item["triggered"] = True
            except Exception as e:
                item["error"] = str(e)
        save_json(WATCHLIST_PATH, watchlist)
        time.sleep(refresh_s)

if "alerts_thread" not in st.session_state:
    threading.Thread(target=check_alerts, daemon=True).start()
    st.session_state["alerts_thread"] = True

# ---------- UI ----------
refresh_ms = max(10, int(86400000 / config.get("checks_per_day", 1440)))
st_autorefresh(interval=refresh_ms, key="datarefresh")

st.set_page_config(page_title="⏰ Price Alerts", layout="wide", initial_sidebar_state="expanded")

st.title("⏰ Price Alerts – Acciones & Cripto (en vivo)")

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ Configuración")
    config["av_key"] = st.text_input("Alpha Vantage API Key", value=config["av_key"], type="password")
    config["checks_per_day"] = st.number_input("Chequeos por día (1‑1440)", 1, 1440, int(config["checks_per_day"]))
    st.markdown("---")
    st.subheader("SMTP / Gmail")
    config["smtp_user"] = st.text_input("Usuario Gmail", value=config["smtp_user"])
    config["smtp_pass"] = st.text_input("App Password Gmail", value=config["smtp_pass"], type="password")
    config["email_to"] = st.text_input("Enviar alertas a", value=config["email_to"])
    colA, colB = st.columns(2)
    if colA.button("💾 Guardar configuración"):
        save_json(CONFIG_PATH, config)
        st.success("Configuración guardada ✔️")
    if colB.button("📧 Correo de prueba"):
        if send_email("Test Price Alerts", "Correo de prueba"):
            st.success("Enviado ✔️")

# --- Agregar activo ---
st.markdown("## ➕ Agregar activo a seguimiento")
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1:
    symbol = st.text_input("Ticker / Símbolo", placeholder="AAPL o BTC")
with col2:
    asset_type = st.selectbox("Tipo", ["stock", "crypto"], index=0)
with col3:
    direction = st.selectbox("Condición", ["above", "below"], index=0)
with col4:
    target = st.number_input("Precio USD", min_value=0.01, step=0.01)

if st.button("Agregar a lista"):
    if symbol and target > 0:
        watchlist.append({
            "symbol": symbol.upper(),
            "type": asset_type,
            "direction": direction,
            "target": target,
            "last": None,
            "triggered": False,
        })
        save_json(WATCHLIST_PATH, watchlist)
        st.success(f"{symbol.upper()} agregado")
    else:
        st.error("Completa símbolo y precio válido")

# --- Lista en vivo ---
st.markdown("## 📈 Lista de seguimiento – precios en tiempo real")

if watchlist:
    header_cols = st.columns([1.2, 0.8, 1.5, 1.2, 0.8, 0.6])
    for col, text in zip(header_cols, ["Ticker", "Tipo", "Condición", "Precio actual", "Estado", ""]):
        col.markdown(f"**{text}**")

    for idx, item in enumerate(watchlist):
        row = st.columns([1.2, 0.8, 1.5, 1.2, 0.8, 0.6])
        row[0].markdown(item["symbol"])
        row[1].markdown(item["type"])
        row[2].markdown(("≥" if item["direction"] == "above" else "≤") + f" {item['target']}")
        row[3].markdown(f"{item['last']:.2f}" if item.get("last") else "…")
        row[4].markdown("🟢 Activa" if not item.get("triggered") else "🔔 Disparada")
        if row[5].button("🗑️", key=f"del_{idx}"):
            del watchlist[idx]
            save_json(WATCHLIST_PATH, watchlist)
            st.experimental_rerun()

    st.caption(f"Última actualización: {datetime.utcnow().strftime('%H:%M:%S')} UTC")
else:
    st.info("Aún no hay activos en seguimiento.")

st.caption("App Streamlit © 2025 – Fantastic Plastik")
