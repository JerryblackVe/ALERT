"""
PRICE ALERTS PRO – Acciones & Cripto (Yahoo Finance + CoinGecko)
Requisitos:
    pip install streamlit streamlit-autorefresh yfinance requests python-dotenv
"""

# ———————————— IMPORTS ————————————
import os, json, time, threading, ssl, smtplib, logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from logging.handlers import RotatingFileHandler

import requests, yfinance as yf, streamlit as st
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
    .main {
        padding-top: 2rem;
    }
    .stButton>button {
        width: 100%;
        border-radius: 20px;
        height: 3em;
        font-weight: bold;
    }
    .delete-button>button {
        background-color: #ff4b4b;
        color: white;
    }
    .add-button>button {
        background-color: #00cc88;
        color: white;
    }
    .price-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        margin: 10px 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .alert-triggered {
        background-color: #ffebee;
        border-left: 4px solid #f44336;
    }
    .alert-pending {
        background-color: #e8f5e9;
        border-left: 4px solid #4caf50;
    }
    .price-up {
        color: #4caf50;
        font-weight: bold;
    }
    .price-down {
        color: #f44336;
        font-weight: bold;
    }
    .metric-container {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 20px;
        border-radius: 15px;
        text-align: center;
        margin: 10px 0;
    }
    .metric-value {
        font-size: 2.5em;
        font-weight: bold;
    }
    .metric-label {
        font-size: 1.1em;
        opacity: 0.9;
    }
    div[data-testid="stSidebar"] {
        background-color: #f8f9fa;
    }
    .config-section {
        background-color: white;
        padding: 15px;
        border-radius: 10px;
        margin: 10px 0;
    }
</style>
""", unsafe_allow_html=True)

# ———————————— LOGGING ————————————
logger = logging.getLogger("PriceAlerts")
logger.setLevel(logging.INFO)

if not logger.handlers:
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

# ———————————— CONFIGURACIÓN ————————————
CONFIG_PATH = "config.json"
WATCHLIST_PATH = "watchlist.json"
CACHE_PATH = "price_cache.json"

DEFAULT_CONFIG = {
    "checks_per_day": 1440,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_user": "",
    "smtp_pass": "",
    "email_to": "",
    "cache_duration_minutes": 2,
    "max_retries": 3,
    "notification_cooldown": 300
}

CRYPTO_SYMBOL_MAP = {
    "btc": "bitcoin", "eth": "ethereum", "ada": "cardano", "sol": "solana",
    "doge": "dogecoin", "matic": "polygon", "link": "chainlink", "dot": "polkadot",
    "xrp": "ripple", "ltc": "litecoin", "bch": "bitcoin-cash", "xlm": "stellar",
    "avax": "avalanche-2", "uni": "uniswap", "atom": "cosmos", "algo": "algorand",
    "bnb": "binancecoin", "ftm": "fantom", "near": "near", "sand": "the-sandbox"
}

POPULAR_STOCKS = {
    "Tech Giants": ["AAPL", "GOOGL", "MSFT", "AMZN", "META"],
    "EVs & Energy": ["TSLA", "RIVN", "NIO", "F", "GM"],
    "Finance": ["JPM", "BAC", "GS", "MS", "WFC"],
    "Semiconductors": ["NVDA", "AMD", "INTC", "TSM", "QCOM"]
}

POPULAR_CRYPTOS = {
    "Top Coins": ["BTC", "ETH", "BNB", "SOL", "XRP"],
    "DeFi": ["UNI", "LINK", "AAVE", "SUSHI", "CRV"],
    "Layer 2": ["MATIC", "ARB", "OP", "IMX"],
    "Gaming": ["SAND", "MANA", "AXS", "GALA"]
}

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
    arrow = "▲" if change > 0 else "▼" if change < 0 else "—"
    color = "price-up" if change > 0 else "price-down" if change < 0 else ""
    return f'<span class="{color}">{arrow} {abs(change):.2f}%</span>'

def format_number(num: Optional[float]) -> str:
    if num is None:
        return "—"
    if num >= 1e9:
        return f"${num/1e9:.2f}B"
    elif num >= 1e6:
        return f"${num/1e6:.2f}M"
    elif num >= 1e3:
        return f"${num/1e3:.2f}K"
    else:
        return f"${num:.2f}"

# ———————————— CACHE DE PRECIOS ————————————
class PriceCache:
    def __init__(self, minutes: int):
        self.ttl = timedelta(minutes=minutes)
        raw = jload(CACHE_PATH, {})
        self.cache = {}
        
        for k, v in raw.items():
            if "timestamp" in v:
                try:
                    v["timestamp"] = datetime.fromisoformat(v["timestamp"])
                    self.cache[k] = v
                except:
                    pass

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
    cached = cache.get(key)
    if cached:
        return cached
    
    for i in range(retries):
        try:
            ticker = yf.Ticker(sym.upper())
            info = ticker.info
            
            # Intentar obtener el precio de varias fuentes
            price = None
            for price_key in ['currentPrice', 'regularMarketPrice', 'previousClose']:
                if price_key in info and info[price_key]:
                    price = info[price_key]
                    break
            
            if price is None:
                # Intentar con fast_info
                try:
                    fast_info = ticker.fast_info
                    price = fast_info.get('lastPrice') or fast_info.get('regularMarketPrice')
                except:
                    pass
            
            if price is None:
                raise ValueError("No se pudo obtener el precio")
            
            pd = PriceData(
                price=float(price),
                timestamp=datetime.now(),
                change_24h=info.get('regularMarketChangePercent'),
                volume=info.get('volume')
            )
            cache.set(key, pd)
            return pd
            
        except Exception as e:
            logger.warning(f"Intento {i+1} para {sym}: {e}")
            if i < retries - 1:
                time.sleep(2 ** i)
    
    raise ValueError(f"No se pudo obtener precio para {sym}")

def _price_crypto(sym: str, cache: PriceCache, retries: int) -> PriceData:
    key = f"cry_{sym.lower()}"
    cached = cache.get(key)
    if cached:
        return cached
    
    cid = CRYPTO_SYMBOL_MAP.get(sym.lower(), sym.lower())
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": cid,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_24hr_vol": "true"
    }
    
    for i in range(retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if cid not in data:
                raise ValueError(f"Símbolo no encontrado: {sym}")
            
            coin_data = data[cid]
            pd = PriceData(
                price=float(coin_data["usd"]),
                timestamp=datetime.now(),
                change_24h=coin_data.get("usd_24h_change"),
                volume=coin_data.get("usd_24h_vol")
            )
            cache.set(key, pd)
            return pd
            
        except Exception as e:
            logger.warning(f"Intento {i+1} para {sym}: {e}")
            if i < retries - 1:
                time.sleep(2 ** i)
    
    raise ValueError(f"No se pudo obtener precio para {sym}")

def validate_symbol(sym: str, atype: AssetType) -> Tuple[bool, str]:
    try:
        tmp_cache = PriceCache(0)
        if atype == AssetType.STOCK:
            _price_stock(sym, tmp_cache, 1)
        else:
            _price_crypto(sym, tmp_cache, 1)
        return True, "Símbolo válido"
    except Exception as e:
        return False, str(e)

# ———————————— NOTIFICADOR ————————————
class Notifier:
    def __init__(self, cooldown: int, cfg: Dict):
        self.last: Dict[str, datetime] = {}
        self.cooldown = cooldown
        self.config = cfg

    def can_notify(self, symbol: str) -> bool:
        if symbol not in self.last:
            return True
        return (datetime.now() - self.last[symbol]).total_seconds() > self.cooldown

    def mark_notified(self, symbol: str):
        self.last[symbol] = datetime.now()

    def send_email(self, subject: str, body: str) -> bool:
        if not all(self.config.get(k) for k in ["smtp_user", "smtp_pass", "email_to"]):
            logger.warning("Configuración de email incompleta")
            return False
        
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.config["smtp_user"]
            msg["To"] = self.config["email_to"]
            msg.set_content(body)
            
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                self.config["smtp_host"], 
                self.config["smtp_port"], 
                context=context
            ) as server:
                server.login(self.config["smtp_user"], self.config["smtp_pass"])
                server.send_message(msg)
            
            logger.info(f"Email enviado: {subject}")
            return True
            
        except Exception as e:
            logger.error(f"Error enviando email: {e}")
            return False

    def notify(self, symbol: str, subject: str, body: str) -> bool:
        if self.can_notify(symbol):
            if self.send_email(subject, body):
                self.mark_notified(symbol)
                return True
        return False

# ———————————— CARGAR CONFIGURACIÓN ————————————
load_dotenv()

user_config = jload(CONFIG_PATH, {})
config = {**DEFAULT_CONFIG, **user_config}

# Variables de entorno tienen prioridad
for env_var, config_key in [
    ("SMTP_USER", "smtp_user"),
    ("SMTP_PASS", "smtp_pass"),
    ("EMAIL_TO", "email_to")
]:
    if env_value := os.getenv(env_var):
        config[config_key] = env_value

watchlist = jload(WATCHLIST_PATH, [])
cache = PriceCache(config["cache_duration_minutes"])
notifier = Notifier(config["notification_cooldown"], config)

# ———————————— WORKER THREAD ————————————
def price_monitor_worker():
    while True:
        try:
            interval = max(10, 86400 // config.get("checks_per_day", 1440))
            
            for item in watchlist:
                try:
                    # Obt
