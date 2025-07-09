"""
Price Alert – Yahoo Finance & CoinGecko (batch-fetch, SQLite, env-vars)
Autor: ChatGPT (jul-2025) – listo para copiar/pegar.
"""

import os, json, time, sqlite3, threading, schedule, ssl, smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List

import requests, yfinance as yf, streamlit as st
from streamlit_autorefresh import st_autorefresh
from dotenv import load_dotenv

# ---------- Carga de variables de entorno ----------
load_dotenv()  # lee .env si existe

SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_TO  = os.getenv("EMAIL_TO", "")

# ---------- BBDD ----------
DB_PATH = "watchlist.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur  = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS watchlist(
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    type   TEXT,
    direction TEXT,
    target REAL,
    last REAL,
    triggered INTEGER,
    last_alert_utc TEXT
)""")
conn.commit()
DB_LOCK = threading.Lock()

def db_all() -> List[Dict]:
    cur.execute("SELECT * FROM watchlist")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def db_add(item: Dict):
    with DB_LOCK:
        cur.execute("""INSERT INTO watchlist(symbol,type,direction,target,last,triggered,last_alert_utc)
                       VALUES(?,?,?,?,?,?,?)""",
                    (item["symbol"], item["type"], item["direction"],
                     item["target"], None, 0, None))
        conn.commit()

def db_delete(item_id: int):
    with DB_LOCK:
        cur.execute("DELETE FROM watchlist WHERE id=?", (item_id,))
        conn.commit()

def db_update(id_: int, **kwargs):
    ks, vs = zip(*kwargs.items())
    set_clause = ", ".join(f"{k}=?" for k in ks)
    with DB_LOCK:
        cur.execute(f"UPDATE watchlist SET {set_clause} WHERE id=?", (*vs, id_))
        conn.commit()

# ---------- Precios ----------
CRYPTO_MAP = {"btc":"bitcoin","eth":"ethereum","ada":"cardano","sol":"solana"}

@st.cache_data(ttl=15)
def batch_stock_prices(symbols: List[str]) -> Dict[str, float]:
    data = yf.download(tickers=" ".join(symbols), period="1d", interval="1m", group_by='ticker', progress=False)
    prices = {}
    for sym in symbols:
        try:
            ser = data[sym]["Close"].dropna()
            prices[sym] = float(ser.iloc[-1])
        except Exception:
            t = yf.Ticker(sym)
            prices[sym] = float(t.fast_info.get("lastPrice") or t.info["regularMarketPrice"])
    return prices

@st.cache_data(ttl=15)
def batch_crypto_prices(symbols: List[str]) -> Dict[str, float]:
    ids = [CRYPTO_MAP.get(s.lower(), s.lower()) for s in symbols]
    url = f"https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": ",".join(ids), "vs_currencies": "usd"}
    r = requests.get(url, params=params, timeout=10).json()
    return {s.upper(): float(r[CRYPTO_MAP.get(s.lower(), s.lower())]["usd"]) for s in symbols}

def current_prices(rows: List[Dict]) -> Dict[int, float]:
    stocks  = [r["symbol"] for r in rows if r["type"]=="stock"]
    cryptos = [r["symbol"] for r in rows if r["type"]=="crypto"]
    out = {}
    if stocks:
        sp = batch_stock_prices(stocks)
        out.update({sym:sp[sym] for sym in stocks})
    if cryptos:
        cp = batch_crypto_prices(cryptos)
        out.update({sym.upper():cp[sym.upper()] for sym in cryptos})
    return out

# ---------- Email ----------
def send_email(subj: str, body: str) -> bool:
    if not (SMTP_USER and SMTP_PASS and EMAIL_TO):
        st.toast("⚠️ Configurá SMTP en variables de entorno", icon="⚙️")
        return False
    msg = EmailMessage(); msg["Subject"], msg["From"], msg["To"] = subj, SMTP_USER, EMAIL_TO
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
            s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)
        return True
    except Exception as e:
        st.toast(f"❌ Error SMTP: {e}")
        return False

# ---------- Motor de alertas ----------
COOLDOWN = timedelta(minutes=15)

def job_check():
    rows = db_all()
    if not rows: return
    prices = current_prices(rows)
    now = datetime.utcnow()
    for r in rows:
        price = prices.get(r["symbol"], None)
        if price is None: continue
        db_update(r["id"], last=price)
        cond = price >= r["target"] if r["direction"]=="above" else price <= r["target"]
        if cond:
            last_alert = datetime.fromisoformat(r["last_alert_utc"]) if r["last_alert_utc"] else None
            if not last_alert or now-last_alert > COOLDOWN:
                op = "≥" if r["direction"]=="above" else "≤"
                if send_email(f"Alerta {r['symbol']} {op} {r['target']}",
                              f"Ticker: {r['symbol']}\nPrecio actual: {price:.2f} USD\nUTC: {now:%Y-%m-%d %H:%M:%S}"):
                    db_update(r["id"], triggered=1, last_alert_utc=now.isoformat())

# Planificador (1-1440 chequeos/día)
CHECKS_PER_DAY = int(st.secrets.get("checks_per_day", 1440))
schedule.every(86400//CHECKS_PER_DAY).seconds.do(job_check)

def scheduler_loop():
    while True:
        schedule.run_pending()
        time.sleep(1)

if "thr" not in st.session_state:
    threading.Thread(target=scheduler_loop, daemon=True).start()
    st.session_state["thr"] = True

# ---------- UI ----------
st.set_page_config("⏰ Price Alerts", layout="wide")
st.title("⏰ Price Alerts – Yahoo Finance + CoinGecko")

st_autorefresh(interval=max(10,1000*86400//CHECKS_PER_DAY), key="refresh")

# --- Agregar activo ---
st.subheader("➕ Nuevo activo")
c1,c2,c3,c4 = st.columns([2,1,1,1])
symbol  = c1.text_input("Ticker / Símbolo")
atype   = c2.selectbox("Tipo",["stock","crypto"])
direct  = c3.selectbox("Condición",["above","below"])
target  = c4.number_input("Precio USD",min_value=0.01,step=0.01)

if st.button("Agregar"):
    if symbol and target>0:
        db_add({"symbol":symbol.upper(),"type":atype,"direction":direct,"target":target})
        st.experimental_rerun()
    else:
        st.warning("Completa símbolo y precio válido")

# --- Tabla en vivo ---
st.subheader("📈 Lista de seguimiento")
rows = db_all()
if rows:
    prices = {r["symbol"]:r["last"] for r in rows}
    for r in rows:
        col = st.columns([1.2,.8,1.5,1.2,.8,.6])
        op = "≥" if r["direction"]=="above" else "≤"
        col[0].markdown(r["symbol"])
        col[1].markdown(r["type"])
        col[2].markdown(f"{op} {r['target']}")
        col[3].markdown(f"{prices.get(r['symbol'],0):.2f}" if prices.get(r["symbol"]) else "–")
        state = "🟢 Activa" if not r["triggered"] else "🔔"
        col[4].markdown(state)
        if col[5].button("🗑️", key=f"del{r['id']}"):
            db_delete(r["id"]); st.experimental_rerun()
else:
    st.info("Aún no hay activos cargados.")

st.caption("Próximo chequeo en ~{} seg".format(86400//CHECKS_PER_DAY))
