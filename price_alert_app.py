"""
PRICE ALERTS PRO – Acciones & Cripto
Requisitos:
    pip install streamlit streamlit-autorefresh yfinance requests python-dotenv
"""

# —————————————————— IMPORTS ——————————————————
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

# —————————————————— LOGGING ——————————————————
logger = logging.getLogger("PriceAlerts")
logger.setLevel(logging.INFO)
logger.addHandler(RotatingFileHandler("price_alerts.log", maxBytes=1_000_000, backupCount=3))
logger.addHandler(logging.StreamHandler())

# —————————————————— ENUMS / MODELOS ——————————————————
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

# —————————————————— CONFIG ——————————————————
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

# ——— utilidades json ———
def jload(path:str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return default

def jsave(path:str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

# ——— cache de precios ———
class PriceCache:
    def __init__(self, minu
