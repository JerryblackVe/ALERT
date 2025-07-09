"""
Price Alert App – Yahoo Finance + CoinGecko
------------------------------------------
• **Yahoo Finance** para acciones (sin API‑key, vía biblioteca `yfinance`).
• CoinGecko para criptomonedas (sin cambios).
• Funciones de correo y UI idénticas (se quitó el campo Alpha Vantage).
• Autorefresco y persistencia locales.

Instalación extra:
```bash
pip install yfinance
```
"""

import os, json, time, threading, ssl, smtplib
from datetime import datetime
from typing import Dict, List

import requests, yfinance as yf, streamlit as st
from streamlit_autorefresh import st_autorefresh
from email.message import EmailMessage

# ---------- Rutas y defaults ----------
CONFIG_PATH = "config.json"
WATCHLIST_PATH = "watchlist.json"

DEFAULT_CONFIG = {
    "checks_per_day": 1440,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "",
    "smtp_pass": "",
    "email_to": "",
}

CRYPTO_SYMBOL_MAP = {"btc": "bitcoin", "eth": "ethereum", "ada": "cardano", "sol": "solana"}

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

# ---------- Cargar configuración y lista ----------
config: Dict = load_json(CONFIG_PATH, DEFAULT_CONFIG)
watchlist: List[Dict] = load_json(WATCHLIST_PATH, [])

# ---------- Funciones de precio ----------

def get_stock_price(symbol: str) -> float:
    try:
        t = yf.Ticker(symbol.upper())
        if "lastPrice" in t.fast_info:
            return float(t.fast_info["lastPrice"])
        return float(t.info["regularMarketPrice"])
    except Exception as e:
        raise ValueError(f"No se pudo obtener precio para {symbol}: {e}")


def get_crypto_price(symbol: str) -> float:
    cid = CRYPTO_SYMBOL_MAP.get(symbol.lower(), symbol.lower())
    data = requests.get(
        f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd", timeout=10
    ).json()
    return float(data[cid]["usd"])


def current_price(item: Dict) -> float:
    return get_stock_price(item["symbol"]) if item["type"] == "stock" else get_crypto_price(item["symbol"])

# ---------- Email ----------

def send_email(subject: str, body: str):
    if not all([config["smtp_user"], config["smtp_pass"], config["email_to"]]):
        st.error("⚠️ Configurá los campos SMTP en la barra lateral.")
        return False
    msg = EmailMessage(); msg["Subject"], msg["From"], msg["To"] = subject, config["smtp_user"], config["email_to"]
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], context=ssl.create_default_context()) as s:
            s.login(config["smtp_user"], config["smtp_pass"]); s.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Error enviando mail: {e}"); return False

# ---------- Hilo de alertas ----------

def check_alerts():
    while True:
        interval = max(10, int(86400 / config.get("checks_per_day", 1440)))
        for it in watchlist:
            try:
                price = current_price(it); it["last"] = price
                cond = price >= it["target"] if it["direction"] == "above" else price <= it["target"]
                if cond and not it.get("triggered", False):
                    op = "≥" if it["direction"] == "above" else "≤"
                    body = f"Ticker: {it['symbol']}\nPrecio actual: {price:.2f} USD\nHora: {datetime.utcnow()} UTC"
                    send_email(f"Alerta {it['symbol']} {op} {it['target']}", body)
                    it["triggered"] = True
            except Exception as e:
                it["error"] = str(e)
        save_json(WATCHLIST_PATH, watchlist); time.sleep(interval)

if "_thr" not in st.session_state:
    threading.Thread(target=check_alerts, daemon=True).start(); st.session_state["_thr"] = True

# ---------- UI ----------
refresh_ms = max(10, int(86400000 / config.get("checks_per_day", 1440)))
st_autorefresh(interval=refresh_ms, key="datarefresh")

st.set_page_config(page_title="⏰ Price Alerts", layout="wide", initial_sidebar_state="expanded")
st.title("⏰ Price Alerts – Acciones & Cripto (Yahoo Finance)")

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ Configuración")
    config["checks_per_day"] = st.number_input("Chequeos por día (1‑1440)", 1, 1440, int(config["checks_per_day"]))
    st.markdown("---")
    st.subheader("SMTP / Gmail")
    config["smtp_user"] = st.text_input("Usuario Gmail", value=config["smtp_user"])
    config["smtp_pass"] = st.text_input("App Password Gmail", value=config["smtp_pass"], type="password")
    config["email_to"] = st.text_input("Enviar alertas a", value=config["email_to"])
    colA, colB = st.columns(2)
    if colA.button("💾 Guardar configuración"):
        save_json(CONFIG_PATH, config); st.success("Configuración guardada ✔️")
    if colB.button("📧 Correo de prueba"):
        send_email("Test Price Alerts", "Correo de prueba desde tu app Price Alerts.")

# --- Agregar activo ---
st.markdown("## ➕ Agregar activo a seguimiento")
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1: symbol = st.text_input("Ticker / Símbolo", placeholder="AAPL o BTC")
with col2: asset_type = st.selectbox("Tipo", ["stock", "crypto"], index=0)
with col3: direction = st.selectbox("Condición", ["above", "below"], index=0)
with col4: target = st.number_input("Precio USD", min_value=0.01, step=0.01)

if st.button("Agregar a lista"):
    if symbol and target > 0:
        watchlist.append({"symbol": symbol.upper(), "type": asset_type, "direction": direction, "target": target, "last": None, "triggered": False})
        save_json(WATCHLIST_PATH, watchlist); st.success(f"{symbol.upper()} agregado")
    else:
        st.error("Completa símbolo y precio válido")

# --- Lista en vivo ---
st.markdown("## 📈 Lista de seguimiento – precios en tiempo real")
if watchlist:
    headers = ["Ticker", "Tipo", "Condición", "Precio actual", "Estado", ""]
    header_cols = st.columns([1.2, .8, 1.5, 1.2, .8, .6])
    for col, h in zip(header_cols, headers): col.markdown(f"**{h}**")

    for idx, it in enumerate(watchlist):
        cols = st.columns([1.2, .8, 1.5, 1.2, .8, .6])
        cols[0].markdown(it["symbol"]); cols[1].markdown(it["type"])
        op = "≥" if it["direction"] == "above" else "≤"; cols[2].markdown(f"{op} {it['target']}")
        cols[3].markdown(f"{it['last']:.2f}" if it.get("last") else "…")
        cols[4].markdown("🟢 Activa" if not it.get("triggered") else "🔔 Disparada")
        if cols[5].button("🗑️", key=f"del_{idx}"):
            del watchlist[idx]; save_json
