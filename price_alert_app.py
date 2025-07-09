"""
PRICE ALERTS PRO – Acciones & Cripto (Yahoo Finance + CoinGecko)
----------------------------------------------------------------
• Cache inteligente y back-off de reintentos  
• Manejo robusto de errores + logging con rotación  
• Cool-down de notificaciones configurable  
• UI con filtros, búsqueda y métricas en tiempo real  
• Sin funciones «experimental» de Streamlit → compatibilidad 1.x-2.x  

Requisitos:
    pip install streamlit streamlit-autorefresh yfinance requests python-dotenv
"""

# ———————————————————————  IMPORTS  ————————————————————————
import os, json, time, threading, ssl, smtplib, logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum, auto
from logging.handlers import RotatingFileHandler

import requests, yfinance as yf, streamlit as st
from streamlit_autorefresh import st_autorefresh
from email.message import EmailMessage
from dotenv import load_dotenv

# ————————————————————  LOGGING SET-UP  ————————————————————
LOG_FILE = "price_alerts.log"
logger = logging.getLogger("PriceAlerts")
logger.setLevel(logging.INFO)
logger.addHandler(RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3))
logger.addHandler(logging.StreamHandler())

# ————————————————————  ENUMS / MODELOS  ————————————————————
class AlertDirection(Enum):
    ABOVE  = "above"
    BELOW  = "below"

class AssetType(Enum):
    STOCK  = "stock"
    CRYPTO = "crypto"

@dataclass
class PriceData:
    price: float
    timestamp: datetime
    change_24h: Optional[float] = None
    volume: Optional[float] = None

# ————————————————————  RUTAS & CONSTANTES  ————————————————————
CONFIG_PATH     = "config.json"
WATCHLIST_PATH  = "watchlist.json"
CACHE_PATH      = "price_cache.json"

DEFAULT_CONFIG: Dict = {
    "checks_per_day":         1440,      # 1 chequeo por minuto
    "smtp_host":              "smtp.gmail.com",
    "smtp_port":              465,
    "smtp_user":              "",
    "smtp_pass":              "",
    "email_to":               "",
    "cache_duration_minutes": 2,
    "max_retries":            3,
    "notification_cooldown":  300        # 5 min
}

CRYPTO_SYMBOL_MAP = {
    "btc":"bitcoin","eth":"ethereum","ada":"cardano","sol":"solana","doge":"dogecoin",
    "matic":"polygon","link":"chainlink","dot":"polkadot","xrp":"ripple","ltc":"litecoin",
    "bch":"bitcoin-cash","xlm":"stellar"
}

# ————————————————————  UTILIDADES JSON  ————————————————————
def _load_json(path:str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error leyendo {path}: {e}")
    return default.copy() if hasattr(default, "copy") else default

def _save_json(path:str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Error guardando {path}: {e}")

# ————————————————————  PRICE CACHE  ————————————————————
class PriceCache:
    def __init__(self, minutes:int):
        self.ttl  = timedelta(minutes=minutes)
        self._raw = _load_json(CACHE_PATH, {})
        # convertir timestamps a datetime
        for k,v in list(self._raw.items()):
            ts = v.get("timestamp")
            try:
                self._raw[k]["timestamp"] = datetime.fromisoformat(ts)
            except Exception:
                self._raw.pop(k, None)

    def get(self, key:str) -> Optional[PriceData]:
        d = self._raw.get(key)
        if d and datetime.now() - d["timestamp"] < self.ttl:
            return PriceData(**d)
        return None

    def set(self, key:str, data:PriceData):
        self._raw[key] = data.__dict__
        _save_json(CACHE_PATH, self._raw)

# ————————————————————  FUNCIONES DE PRECIO  ————————————————————
def _get_stock_price(sym:str, cache:PriceCache, retries:int)->PriceData:
    key=f"stk_{sym.upper()}"
    if (pd:=cache.get(key)): return pd

    for n in range(retries):
        try:
            t = yf.Ticker(sym.upper())
            info = t.fast_info if hasattr(t,"fast_info") else {}
            price = info.get("lastPrice") or info.get("regularMarketPrice") or t.info.get("regularMarketPrice")
            if not price: raise ValueError("precio no disponible")
            out = PriceData(float(price), datetime.now(),
                            change_24h=t.info.get("regularMarketChangePercent"),
                            volume=t.info.get("regularMarketVolume"))
            cache.set(key,out)
            return out
        except Exception as e:
            logger.warning(f"[{sym}] intento {n+1}: {e}")
            time.sleep(2**n)
    raise ValueError(f"No se pudo obtener precio de {sym}")

def _get_crypto_price(sym:str, cache:PriceCache, retries:int)->PriceData:
    key=f"crt_{sym.lower()}"
    if (pd:=cache.get(key)): return pd
    coin_id = CRYPTO_SYMBOL_MAP.get(sym.lower(), sym.lower())

    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids":coin_id,"vs_currencies":"usd","include_24hr_change":"true"}

    for n in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10); r.raise_for_status()
            data = r.json()[coin_id]
            out = PriceData(float(data["usd"]), datetime.now(),
                            change_24h=data.get("usd_24h_change"))
            cache.set(key,out)
            return out
        except Exception as e:
            logger.warning(f"[{sym}] intento {n+1}: {e}")
            time.sleep(2**n)
    raise ValueError(f"No se pudo obtener precio de {sym}")

def validate_symbol(symbol:str, atype:AssetType)->bool:
    try:
        tmp = PriceCache(0)
        (_get_stock_price if atype==AssetType.STOCK else _get_crypto_price)(symbol,tmp,1)
        return True
    except: return False

# ————————————————————  NOTIFICACIONES  ————————————————————
class Notifier:
    def __init__(self, cooldown:int):
        self.last = {}     # symbol -> datetime
        self.cooldown = cooldown

    def _can_send(self,sym:str)->bool:
        t=self.last.get(sym)
        return not t or (datetime.now()-t).total_seconds()>self.cooldown

    def _mark_sent(self,sym:str): self.last[sym]=datetime.now()

    def email(self, subj:str, body:str)->bool:
        fields=["smtp_host","smtp_port","smtp_user","smtp_pass","email_to"]
        if not all(config.get(f) for f in fields): return False
        try:
            msg = EmailMessage()
            msg["Subject"],msg["From"],msg["To"]=subj,config["smtp_user"],config["email_to"]
            msg.set_content(body)
            ctx=ssl.create_default_context()
            with smtplib.SMTP_SSL(config["smtp_host"],config["smtp_port"],context=ctx) as s:
                s.login(config["smtp_user"],config["smtp_pass"])
                s.send_message(msg)
            return True
        except Exception as e:
            logger.error(f"SMTP: {e}")
            return False

    def notify(self, sym:str, subj:str, body:str):
        if self._can_send(sym) and self.email(subj,body):
            self._mark_sent(sym)

# ————————————————————  CARGAR CONFIG & LISTAS  ————————————————————
config: Dict       = _load_json(CONFIG_PATH , DEFAULT_CONFIG)
watchlist: List[Dict] = _load_json(WATCHLIST_PATH, [])
cache               = PriceCache(config["cache_duration_minutes"])
notifier            = Notifier(config["notification_cooldown"])

# ————————————————————  HILO PRINCIPAL DE ALERTAS  ————————————————————
def _alert_loop():
    while True:
        interval = max(10, 86400 // config.get("checks_per_day",1440))
        for item in watchlist:
            try:
                getter = _get_stock_price if item["type"]==AssetType.STOCK.value else _get_crypto_price
                pd = getter(item["symbol"], cache, config["max_retries"])
                item.update({"last_price":pd.price,"change_24h":pd.change_24h,"last_update":datetime.now().isoformat()})
                cond = (pd.price>=item["target"]) if item["direction"]==AlertDirection.ABOVE.value else (pd.price<=item["target"])
                if cond and not item.get("triggered",False):
                    subj = f"🚨 {item['symbol']} {'≥' if item['direction']=='above' else '≤'} {item['target']}"
                    body = f"{item['symbol']}: {pd.price:.2f} USD\nCambio 24h: {pd.change_24h:.2f}%\n{datetime.now():%Y-%m-%d %H:%M:%S}"
                    notifier.notify(item["symbol"],subj,body)
                    item["triggered"]=True
            except Exception as e:
                item["error"]=str(e); logger.error(e)
        _save_json(WATCHLIST_PATH,watchlist)
        time.sleep(interval)

if "_thr" not in st.session_state:
    threading.Thread(target=_alert_loop,daemon=True).start()
    st.session_state["_thr"]=True

# ————————————————————  STREAMLIT UI  ————————————————————
st.set_page_config("⏰ Price Alerts Pro", layout="wide", initial_sidebar_state="expanded")
st.title("⏰ Price Alerts Pro – Acciones & Cripto")

# auto-refresh
ms = max(30_000, 86400000 // config.get("checks_per_day",1440))
st_autorefresh(interval=ms, key="refresh")

# métricas generales
m1,m2,m3,m4 = st.columns(4)
m1.metric("Activos",len(watchlist))
m2.metric("Alertas activas",len([x for x in watchlist if not x.get("triggered")]))
m3.metric("Disparadas",len([x for x in watchlist if x.get("triggered")]))
m4.metric("Con error",len([x for x in watchlist if x.get("error")]))

# ——— SIDEBAR ———
with st.sidebar:
    st.header("⚙️ Configuración")
    config["checks_per_day"] = st.number_input("Chequeos/día",1,1440,int(config["checks_per_day"]))
    config["cache_duration_minutes"] = st.number_input("Cache (min)",1,30,int(config["cache_duration_minutes"]))
    config["notification_cooldown"] = st.number_input("Cooldown notifs (seg)",60,3600,int(config["notification_cooldown"]))

    st.subheader("📧 SMTP")
    config["smtp_host"]=st.text_input("Host",config["smtp_host"])
    config["smtp_port"]=st.number_input("Puerto",1,65535,int(config["smtp_port"]))
    config["smtp_user"]=st.text_input("Usuario",config["smtp_user"])
    config["smtp_pass"]=st.text_input("Password",config["smtp_pass"],type="password")
    config["email_to"] = st.text_input("Enviar a",config["email_to"])

    b1,b2 = st.columns(2)
    if b1.button("💾 Guardar"):
        _save_json(CONFIG_PATH,config); st.success("Guardado")
    if b2.button("📧 Test"):
        ok = notifier.email("Test PriceAlerts","Todo Ok")
        st.success("Enviado") if ok else st.error("Error")

# ——— FORMULARIO NUEVO ACTIVO ———
st.subheader("➕ Nuevo activo")
with st.form("form_add"):
    c1,c2,c3,c4 = st.columns([2,1,1,1])
    sym = c1.text_input("Símbolo")
    typ = c2.selectbox("Tipo",[AssetType.STOCK.value,AssetType.CRYPTO.value])
    dir_ = c3.selectbox("Condición",[AlertDirection.ABOVE.value,AlertDirection.BELOW.value])
    tgt = c4.number_input("Precio USD",min_value=0.01,step=0.01)
    add = st.form_submit_button("Agregar")

if add and sym and tgt>0:
    with st.spinner("Validando…"):
        if validate_symbol(sym,AssetType(typ)):
            watchlist.append({"symbol":sym.upper(),"type":typ,"direction":dir_,"target":tgt,
                              "triggered":False,"added_at":datetime.now().isoformat()})
            _save_json(WATCHLIST_PATH,watchlist)
            getattr(st,"rerun",st.experimental_rerun)()
        else:
            st.error("Símbolo no válido")

# ——— LISTA CON FILTROS ———
st.subheader("📊 Lista de seguimiento")
if watchlist:
    f1,f2,f3 = st.columns([2,1,1])
    q       = f1.text_input("Buscar símbolo")
    tfilt   = f2.selectbox("Tipo",["Todos"]+[e.value for e in AssetType])
    sfilt   = f3.selectbox("Estado",["Todos","Activas","Disparadas","Errores"])

    lst = watchlist
    if q: lst=[i for i in lst if q.upper() in i["symbol"]]
    if tfilt!="Todos": lst=[i for i in lst if i["type"]==tfilt]
    if sfilt=="Activas":   lst=[i for i in lst if not i.get("triggered") and not i.get("error")]
    if sfilt=="Disparadas":lst=[i for i in lst if i.get("triggered")]
    if sfilt=="Errores":   lst=[i for i in lst if i.get("error")]

    if lst:
        heads = ["Símb","Tipo","Cond.","Precio","24h %","Estado","Acción"]
        st.write("|".join(heads))
        for idx,item in enumerate(lst):
            col1,col2,col3,col4,col5,col6,col7 = st.columns([1,0.8,1.2,1,1,1,1])
            col1.write(f"**{item['symbol']}**")
            col2.write(item["type"])
            col3.write(("≥" if item["direction"]=="above" else "≤")+f" {item['target']}")
            col4.write(f"{item.get('last_price','–')}")
            col5.write(f"{item.get('change_24h','–')}")
            if item.get("error"):
                col6.write("❌")
            elif item.get("triggered"):
                col6.write("🔔")
            else:
                col6.write("🟢")
            idx_real = watchlist.index(item)
            if col7.button("🗑️",key=f"del{idx_real}"):
                watchlist.pop(idx_real); _save_json(WATCHLIST_PATH,watchlist); getattr(st,"rerun",st.experimental_rerun)()
    else:
        st.info("Sin resultados.")
else:
    st.info("Aún no hay activos – agrega el primero.")
