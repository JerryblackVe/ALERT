"""
PRICE ALERTS PRO – Acciones & Cripto (Yahoo Finance + CoinGecko)
Requisitos:
    pip install streamlit streamlit-autorefresh yfinance requests python-dotenv plotly pandas
"""

# ———————————— IMPORTS ————————————
import os, json, time, threading, ssl, smtplib, logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from logging.handlers import RotatingFileHandler
from collections import deque

import requests, yfinance as yf, streamlit as st
import pandas as pd
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh
from email.message import EmailMessage
from dotenv import load_dotenv

# ———————————— CONFIGURACIÓN DE PÁGINA ————————————
st.set_page_config(
    page_title="⏰ Price Alerts Pro",
    page_icon="⏰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ———————————— ESTILOS CSS ————————————
st.markdown("""
<style>
    .stMetric {
        background-color: #f0f2f6;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .alert-triggered {
        background-color: #ffebee;
        padding: 10px;
        border-radius: 5px;
        border-left: 4px solid #f44336;
    }
    .alert-pending {
        background-color: #e3f2fd;
        padding: 10px;
        border-radius: 5px;
        border-left: 4px solid #2196f3;
    }
    .price-up {
        color: #4caf50;
        font-weight: bold;
    }
    .price-down {
        color: #f44336;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# ———————————— LOGGING ————————————
logger = logging.getLogger("PriceAlerts")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler("price_alerts.log", maxBytes=1_000_000, backupCount=3)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)

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
    volume: Optional[float] = None
    market_cap: Optional[float] = None

@dataclass
class PriceHistory:
    symbol: str
    prices: deque
    timestamps: deque
    max_points: int = 100
    
    def add_point(self, price: float, timestamp: datetime):
        self.prices.append(price)
        self.timestamps.append(timestamp)
        if len(self.prices) > self.max_points:
            self.prices.popleft()
            self.timestamps.popleft()

# ———————————— RUTAS & DEFAULTS ————————————
CONFIG_PATH = "config.json"
WATCHLIST_PATH = "watchlist.json"
CACHE_PATH = "price_cache.json"
HISTORY_PATH = "price_history.json"

DEFAULT_CONFIG: Dict = {
    "checks_per_day": 1440,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "",
    "smtp_pass": "",
    "email_to": "",
    "cache_duration_minutes": 2,
    "max_retries": 3,
    "notification_cooldown": 300,
    "theme": "light",
    "show_notifications": True,
    "sound_alerts": False
}

CRYPTO_SYMBOL_MAP = {
    "btc": "bitcoin", "eth": "ethereum", "ada": "cardano", "sol": "solana",
    "doge": "dogecoin", "matic": "polygon", "link": "chainlink", "dot": "polkadot",
    "xrp": "ripple", "ltc": "litecoin", "bch": "bitcoin-cash", "xlm": "stellar",
    "avax": "avalanche-2", "uni": "uniswap", "atom": "cosmos", "algo": "algorand"
}

POPULAR_STOCKS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "META", "NVDA", "AMD"]
POPULAR_CRYPTOS = ["BTC", "ETH", "SOL", "ADA", "MATIC", "LINK", "DOT", "AVAX"]

# ———————————— UTILIDADES ————————————
def jload(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def jsave(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

def format_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.6f}"

def format_change(change: Optional[float]) -> str:
    if change is None:
        return "—"
    arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
    color = "price-up" if change > 0 else "price-down" if change < 0 else ""
    return f'<span class="{color}">{arrow} {abs(change):.2f}%</span>'

# ———————————— CACHE DE PRECIOS ————————————
class PriceCache:
    def __init__(self, minutes: int):
        self.ttl = timedelta(minutes=minutes)
        raw = jload(CACHE_PATH, {})
        self.cache = {
            k: {**v, "timestamp": datetime.fromisoformat(v["timestamp"])}
            for k, v in raw.items()
            if "timestamp" in v
        }

    def get(self, key: str) -> Optional[PriceData]:
        d = self.cache.get(key)
        if d and datetime.now() - d["timestamp"] < self.ttl:
            return PriceData(**d)
        return None

    def set(self, key: str, pdata: PriceData):
        self.cache[key] = pdata.__dict__
        jsave(
            CACHE_PATH,
            {k: {**v, "timestamp": v["timestamp"].isoformat()} for k, v in self.cache.items()},
        )

# ———————————— FUNCIONES DE PRECIO ————————————
def _price_stock(sym: str, cache: PriceCache, retries: int) -> PriceData:
    key = f"stk_{sym.upper()}"
    if (pd := cache.get(key)):
        return pd
    
    for i in range(retries):
        try:
            t = yf.Ticker(sym.upper())
            info = t.info
            fast_info = t.fast_info
            
            price = fast_info.get("lastPrice") or info.get("regularMarketPrice")
            if price is None:
                raise ValueError("precio no disponible")
            
            pd = PriceData(
                price=float(price),
                timestamp=datetime.now(),
                change_24h=info.get("regularMarketChangePercent"),
                volume=info.get("volume"),
                market_cap=info.get("marketCap")
            )
            cache.set(key, pd)
            return pd
        except Exception as e:
            logger.warning(f"{sym} retry {i+1}: {e}")
            time.sleep(2 ** i)
    raise ValueError(f"Sin precio para {sym}")

def _price_crypto(sym: str, cache: PriceCache, retries: int) -> PriceData:
    key = f"cry_{sym.lower()}"
    if (pd := cache.get(key)):
        return pd
    
    cid = CRYPTO_SYMBOL_MAP.get(sym.lower(), sym.lower())
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": cid,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
        "include_market_cap": "true"
    }
    
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            d = r.json()[cid]
            pd = PriceData(
                price=float(d["usd"]),
                timestamp=datetime.now(),
                change_24h=d.get("usd_24h_change"),
                volume=d.get("usd_24h_vol"),
                market_cap=d.get("usd_market_cap")
            )
            cache.set(key, pd)
            return pd
        except Exception as e:
            logger.warning(f"{sym} retry {i+1}: {e}")
            time.sleep(2 ** i)
    raise ValueError(f"Sin precio para {sym}")

def validate_symbol(sym: str, atype: AssetType) -> Tuple[bool, str]:
    try:
        tmp = PriceCache(0)
        (_price_stock if atype == AssetType.STOCK else _price_crypto)(sym, tmp, 1)
        return True, "OK"
    except Exception as e:
        return False, str(e)

# ———————————— HISTORIAL DE PRECIOS ————————————
class PriceHistoryManager:
    def __init__(self):
        self.history: Dict[str, PriceHistory] = {}
        self.load_history()
    
    def load_history(self):
        raw = jload(HISTORY_PATH, {})
        for symbol, data in raw.items():
            self.history[symbol] = PriceHistory(
                symbol=symbol,
                prices=deque(data.get("prices", [])[-100:]),
                timestamps=deque([datetime.fromisoformat(ts) for ts in data.get("timestamps", [])][-100:])
            )
    
    def save_history(self):
        data = {}
        for symbol, hist in self.history.items():
            data[symbol] = {
                "prices": list(hist.prices),
                "timestamps": [ts.isoformat() for ts in hist.timestamps]
            }
        jsave(HISTORY_PATH, data)
    
    def add_price(self, symbol: str, price: float, timestamp: datetime):
        if symbol not in self.history:
            self.history[symbol] = PriceHistory(symbol, deque(), deque())
        self.history[symbol].add_point(price, timestamp)
        self.save_history()
    
    def get_chart_data(self, symbol: str) -> Optional[go.Figure]:
        if symbol not in self.history or len(self.history[symbol].prices) < 2:
            return None
        
        hist = self.history[symbol]
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=list(hist.timestamps),
            y=list(hist.prices),
            mode='lines+markers',
            name=symbol,
            line=dict(width=2),
            marker=dict(size=4)
        ))
        
        fig.update_layout(
            title=f"Historial de {symbol}",
            xaxis_title="Tiempo",
            yaxis_title="Precio (USD)",
            hovermode='x unified',
            template="plotly_white",
            height=300
        )
        
        return fig

# ———————————— NOTIFICADOR ————————————
class Notifier:
    def __init__(self, cooldown: int, cfg: Dict):
        self.last: Dict[str, datetime] = {}
        self.cool = cooldown
        self.cfg = cfg
        self.notification_queue = deque(maxlen=50)

    def _can(self, sym: str) -> bool:
        return sym not in self.last or (datetime.now() - self.last[sym]).total_seconds() > self.cool

    def _mark(self, sym: str):
        self.last[sym] = datetime.now()

    def email(self, subj: str, body: str) -> bool:
        if not all(self.cfg[k] for k in ("smtp_user", "smtp_pass", "email_to")):
            return False
        try:
            msg = EmailMessage()
            msg["Subject"] = subj
            msg["From"] = self.cfg["smtp_user"]
            msg["To"] = self.cfg["email_to"]
            msg.set_content(body)
            
            with smtplib.SMTP_SSL(
                self.cfg["smtp_host"], 
                self.cfg["smtp_port"], 
                context=ssl.create_default_context()
            ) as s:
                s.login(self.cfg["smtp_user"], self.cfg["smtp_pass"])
                s.send_message(msg)
            return True
        except Exception as e:
            logger.error(f"SMTP error: {e}")
            return False

    def notify(self, sym: str, subj: str, body: str, alert_type: str = "price"):
        notification = {
            "symbol": sym,
            "subject": subj,
            "body": body,
            "timestamp": datetime.now(),
            "type": alert_type
        }
        
        self.notification_queue.append(notification)
        
        if self._can(sym) and self.cfg.get("show_notifications", True):
            if self.email(subj, body):
                self._mark(sym)
                logger.info(f"Notificación enviada: {subj}")

# ———————————— CARGAR CONFIGURACIÓN ————————————
load_dotenv()

user_cfg = jload(CONFIG_PATH, {})
config = {**DEFAULT_CONFIG, **user_cfg}

for env_var, key in (("SMTP_USER", "smtp_user"), ("SMTP_PASS", "smtp_pass"), ("EMAIL_TO", "email_to")):
    val = os.getenv(env_var)
    if val:
        config[key] = val

watchlist: List[Dict] = jload(WATCHLIST_PATH, [])
cache = PriceCache(config["cache_duration_minutes"])
notifier = Notifier(config["notification_cooldown"], config)
history_manager = PriceHistoryManager()

# ———————————— HILO DE ALERTAS ————————————
def worker():
    while True:
        interval = max(10, 86400 // config["checks_per_day"])
        
        for item in watchlist:
            try:
                getter = _price_stock if item["type"] == "stock" else _price_crypto
                pd = getter(item["symbol"], cache, config["max_retries"])
                
                # Actualizar datos
                item.update({
                    "last_price": pd.price,
                    "change_24h": pd.change_24h,
                    "last_update": datetime.now().isoformat(),
                
