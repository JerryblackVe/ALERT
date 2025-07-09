"""
PRICE ALERTS PRO – Acciones & Cripto (Yahoo Finance + CoinGecko)
Requisitos:
    pip install streamlit streamlit-autorefresh yfinance requests python-dotenv
"""

# ———————————— IMPORTS ————————————
import os, json, time, threading, ssl, smtplib, logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from logging.handlers import RotatingFileHandler

import requests, yfinance as yf, streamlit as st
from streamlit_autorefresh import st_autorefresh
from email.message import EmailMessage
from dotenv import load_dotenv

# ———————————— LOGGING ————————————
logger = logging.getLogger("PriceAlerts")
logger.setLevel(logging.INFO)
logger.addHandler(RotatingFileHandler("price_alerts.log", maxBytes=1_000_000, backupCount=3))
logger.addHandler(logging.StreamHandler())

# ———————————— ENUMS / MODELOS ————————————
class AlertDirection(Enum):
    ABOVE = "above"
    BELOW = "below"

class AssetType(Enum):
    STOCK  = "stock"
    CRYPTO = "crypto"

@dataclass
class PriceData:
    price: float
    timestamp: datetime
    change_24h: Optional[float] = None

# ———————————— RUTAS & DEFAULTS ————————————
CONFIG_PATH, WATCHLIST_PATH, CACHE_PATH = "config.json", "watchlist.json", "price_cache.json"

DEFAULT_CONFIG: Dict = {
    "checks_per_day":         1440,
    "smtp_host":              "smtp.gmail.com",
    "smtp_port":              465,
    "smtp_user":              "",
    "smtp_pass":              "",
    "email_to":               "",
    "cache_duration_minutes": 2,
    "max_retries":            3,
    "notification_cooldown":  300
}

CRYPTO_SYMBOL_MAP = {
    "btc":"bitcoin","eth":"ethereum","ada":"cardano","sol":"solana","doge":"dogecoin",
    "matic":"polygon","link":"chainlink","dot":"polkadot","xrp":"ripple","ltc":"litecoin",
    "bch":"bitcoin-cash","xlm":"stellar"
}

# ———————————— UTILIDADES JSON ————————————
def jload(path:str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except: 
        return default

def jsave(path:str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

# ———————————— CACHE DE PRECIOS ————————————
class PriceCache:
    def __init__(self, minutes:int):
        self.ttl = timedelta(minutes=minutes)
        raw = jload(CACHE_PATH, {})
        self.cache = {
            k: {**v, "timestamp": datetime.fromisoformat(v["timestamp"])}
            for k, v in raw.items()
            if "timestamp" in v
        }

    def get(self, key:str) -> Optional[PriceData]:
        d = self.cache.get(key)
        if d and datetime.now() - d["timestamp"] < self.ttl:
            return PriceData(**d)
        return None

    def set(self, key:str, pdata:PriceData):
        self.cache[key] = pdata.__dict__
        jsave(
            CACHE_PATH,
            {k: {**v, "timestamp": v["timestamp"].isoformat()} for k, v in self.cache.items()},
        )

# ———————————— FUNCIONES DE PRECIO ————————————
def _price_stock(sym:str, cache:PriceCache, retries:int) -> PriceData:
    key = f"stk_{sym.upper()}"
    if (pd := cache.get(key)):
        return pd
    for i in range(retries):
        try:
            t = yf.Ticker(sym.upper())
            p = t.fast_info.get("lastPrice") or t.info.get("regularMarketPrice")
            if p is None:
                raise ValueError("precio no disponible")
            pd = PriceData(float(p), datetime.now(), t.info.get("regularMarketChangePercent"))
            cache.set(key, pd)
            return pd
        except Exception as e:
            logger.warning(f"{sym} retry {i+1}: {e}")
            time.sleep(2 ** i)
    raise ValueError(f"Sin precio para {sym}")

def _price_crypto(sym:str, cache:PriceCache, retries:int) -> PriceData:
    key = f"cry_{sym.lower()}"
    if (pd := cache.get(key)):
        return pd
    cid = CRYPTO_SYMBOL_MAP.get(sym.lower(), sym.lower())
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": cid, "vs_currencies": "usd", "include_24hr_change": "true"}
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            d = r.json()[cid]
            pd = PriceData(float(d["usd"]), datetime.now(), d.get("usd_24h_change"))
            cache.set(key, pd)
            return pd
        except Exception as e:
            logger.warning(f"{sym} retry {i+1}: {e}")
            time.sleep(2 ** i)
    raise ValueError(f"Sin precio para {sym}")

def validate_symbol(sym:str, atype:AssetType) -> bool:
    try:
        tmp = PriceCache(0)
        (_price_stock if atype == AssetType.STOCK else _price_crypto)(sym, tmp, 1)
        return True
    except:
        return False

# ———————————— NOTIFICADOR ————————————
class Notifier:
    def __init__(self, cooldown:int, cfg:Dict):
        self.last: Dict[str, datetime] = {}
        self.cool = cooldown
        self.cfg  = cfg

    def _can(self, sym:str) -> bool:
        return sym not in self.last or (datetime.now() - self.last[sym]).total_seconds() > self.cool

    def _mark(self, sym:str):
        self.last[sym] = datetime.now()

    def email(self, subj:str, body:str) -> bool:
        if not all(self.cfg[k] for k in ("smtp_user", "smtp_pass", "email_to")):
            return False
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = subj, self.cfg["smtp_user"], self.cfg["email_to"]
            msg.set_content(body)
            with smtplib.SMTP_SSL(
                self.cfg["smtp_host"], self.cfg["smtp_port"], context=ssl.create_default_context()
            ) as s:
                s.login(self.cfg["smtp_user"], self.cfg["smtp_pass"])
                s.send_message(msg)
            return True
        except Exception as e:
            logger.error(f"SMTP {e}")
            return False

    def notify(self, sym:str, subj:str, body:str):
        if self._can(sym) and self.email(subj, body):
            self._mark(sym)

# ———————————— CARGAR CONFIG & LISTAS ————————————
load_dotenv()  # lee .env si existe

user_cfg = jload(CONFIG_PATH, {})
config   = {**DEFAULT_CONFIG, **user_cfg}

# .env sobreescribe si existe
for env_var, key in (("SMTP_USER","smtp_user"), ("SMTP_PASS","smtp_pass"), ("EMAIL_TO","email_to")):
    val = os.getenv(env_var)
    if val:
        config[key] = val

watchlist: List[Dict] = jload(WATCHLIST_PATH, [])
cache     = PriceCache(config["cache_duration_minutes"])
notifier  = Notifier(config["notification_cooldown"], config)

# ———————————— HILO DE ALERTAS ————————————
def worker():
    while True:
        interval = max(10, 86400 // config["checks_per_day"])
        for it in watchlist:
            try:
                getter = _price_stock if it["type"] == "stock" else _price_crypto
                pd = getter(it["symbol"], cache, config["max_retries"])
                it.update({"last_price": pd.price, "change_24h": pd.change_24h})
                hit = (pd.price >= it["target"]) if it["direction"] == "above" else (pd.price <= it["target"])
                if hit and not it.get("triggered"):
                    op = "≥" if it["direction"] == "above" else "≤"
                    subj = f"🚨 {it['symbol']} {op} {it['target']}"
                    body = f"{it['symbol']} {pd.price:.2f} USD\n{datetime.now():%Y-%m-%d %H:%M:%S}"
                    notifier.notify(it["symbol"], subj, body)
                    it["triggered"] = True
            except Exception as e:
                it["error"] = str(e)
        jsave(WATCHLIST_PATH, watchlist)
        time.sleep(interval)

if "_thr" not in st.session_state:
    threading.Thread(target=worker, daemon=True).start()
    st.session_state["_thr"] = True

# ———————————— STREAMLIT UI ————————————
st.set_page_config("⏰ Price Alerts", layout="wide")
st.title("⏰ Price Alerts – Acciones & Cripto")

st_autorefresh(interval=max(30000, 86400000 // config["checks_per_day"]), key="refresh")

st.metric("Activos", len(watchlist))
st.metric("Disparadas", len([x for x in watchlist if x.get("triggered")]))

# ——— Sidebar configuración ———
with st.sidebar:
    st.header("⚙️ Configuración")
    for k, label in [
        ("checks_per_day", "Checks/día"),
        ("cache_duration_minutes", "Cache (min)"),
        ("notification_cooldown", "Cooldown (seg)"),
    ]:
        config[k] = st.number_input(label, 1, 3600, int(config[k]))
    st.subheader("📧 SMTP")
    for k in ("smtp_host", "smtp_port", "smtp_user", "smtp_pass", "email_to"):
        val = st.text_input(k, value=str(config[k]), type="password" if "pass" in k else "default")
        config[k] = int(val) if k == "smtp_port" else val
    if st.button("Guardar"):
        jsave(CONFIG_PATH, config)
        st.success("Guardado")

# ——— Formulario para añadir activo ———
st.subheader("➕ Añadir activo")
with st.form("add"):
    s = st.text_input("Símbolo")
    t = st.selectbox("Tipo", ["stock", "crypto"])
    d = st.selectbox("Condición", ["above", "below"])
    p = st.number_input("Precio", 0.01)
    ok = st.form_submit_button("Agregar")

if ok and s and p > 0:
    if validate_symbol(s, AssetType(t)):
        watchlist.append({"symbol": s.upper(), "type": t, "direction": d, "target": p, "triggered": False})
        jsave(WATCHLIST_PATH, watchlist)
        getattr(st, "rerun", st.experimental_rerun)()
    else:
        st.error("Símbolo no válido")

# ——— Tabla de seguimiento ———
st.subheader("📊 Watchlist")
for i, it in enumerate(watchlist):
    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 0.5])
    c1.write(f"**{it['symbol']}**")
    c2.write(it["type"])
    c3.write(("≥" if it["direction"] == "above" else "≤") + f" {it['target']}")
    c4.write(it.get("last_price", "–"))
    if c5.button("❌", key=f"del{i}"):
        watchlist.pop(i)
        jsave(WATCHLIST_PATH, watchlist)
        getattr(st, "rerun", st.experimental_rerun)()
