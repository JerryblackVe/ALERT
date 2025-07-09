"""
Price Alert App – Alpha Vantage + CoinGecko (v4)
------------------------------------------------
Novedades:
• La clave **Alpha Vantage** se guarda como siempre y ahora tiene botón 🗑️ para borrarla rápidamente.
• La acción de borrado la elimina de `config.json` y recarga la app.
• Resto sin cambios.
"""

import os, json, time, threading, smtplib, ssl
from datetime import datetime
from typing import Dict, List

import requests, streamlit as st
from streamlit_autorefresh import st_autorefresh
from email.message import EmailMessage

# ---------- Rutas y defaults ----------
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

# ---------- Cargar config y lista ----------
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
    data = requests.get(url, timeout=10).json()
    return float(data["Global Quote"]["05. price"])


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
        st.error("⚠️ Falta configurar SMTP completo.")
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
        for item in watchlist:
            try:
                price = current_price(item)
                item["last"] = price
                cond = price >= item["target"] if item["direction"] == "above" else price <= item["target"]
                if cond and not item.get("triggered", False):
                    op = "≥" if item["direction"] == "above" else "≤"
                    body = f"Ticker: {item['symbol']}\nPrecio actual: {price:.2f} USD\nHora: {datetime.utcnow()} UTC"
                    send_email(f"Alerta {item['symbol']} {op} {item['target']}", body)
                    item["triggered"] = True
            except Exception as e:
                item["error"] = str(e)
        save_json(WATCHLIST_PATH, watchlist)
        time.sleep(interval)

if "_thr" not in st.session_state:
    threading.Thread(target=check_alerts, daemon=True).start(); st.session_state["_thr"] = True

# ---------- UI ----------
refresh_ms = max(10, int(86400000 / config.get("checks_per_day", 1440)))
st_autorefresh(interval=refresh_ms, key="datarefresh")

st.set_page_config(page_title="⏰ Price Alerts", layout="wide", initial_sidebar_state="expanded")
st.title("⏰ Price Alerts – Acciones & Cripto (en vivo)")

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ Configuración")
    av_col, del_col = st.columns([4, 1])
    with av_col:
        config["av_key"] = st.text_input("Alpha Vantage API Key", value=config["av_key"], type="password")
    with del_col:
        if st.button("🗑️", help="Borrar API Key"):
            config["av_key"] = ""; save_json(CONFIG_PATH, config); st.experimental_rerun()

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
        send_email("Test Price Alerts", "Correo de prueba")

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
    st.columns([1.2, .8, 1.5, 1.2, .8, .6]); [c.markdown(f"**{h}**") for c,h in zip(st.columns([1.2, .8, 1.5, 1.2, .8, .6]), headers)]
    for idx, it in enumerate(watchlist):
        cols = st.columns([1.2, .8, 1.5, 1.2, .8, .6])
        cols[0].markdown(it["symbol"]); cols[1].markdown(it["type"])
        cols[2].markdown(("≥" if it["direction"] == "above" else "≤") + f" {it['target']}")
        cols[3].markdown(f"{it['last']:.2f}" if it.get("last") else "…")
        cols[4].markdown("🟢 Activa" if not it.get("triggered") else "🔔 Disparada")
        if cols[5].button("🗑️", key=f"del_{idx}"):
            del watchlist[idx]; save_json(WATCHLIST_PATH, watchlist); st.experimental_rerun()
    st.caption(f"Última actualización: {datetime.utcnow().strftime('%H:%M:%S')} UTC")
else:
    st.info("Aún no hay activos en seguimiento.")

st.caption("App Streamlit © 2025 – Fantastic Plastik")
