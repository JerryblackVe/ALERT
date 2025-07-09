"""
Price Alert App – Yahoo Finance + CoinGecko (Versión Mejorada)
============================================================

Mejoras implementadas:
• Cache inteligente para reducir API calls
• Manejo robusto de errores
• UI más intuitiva con métricas
• Validación mejorada de símbolos
• Logging para debugging
• Filtros y búsqueda en watchlist
• Notificaciones múltiples
• Gestión mejorada de estado
• Configuración de intervalos personalizados
• Soporte para múltiples tipos de alertas

Instalación:
```bash
pip install yfinance requests streamlit streamlit-autorefresh
```
"""

import os, json, time, threading, ssl, smtplib, logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

import requests, yfinance as yf, streamlit as st
from streamlit_autorefresh import st_autorefresh
from email.message import EmailMessage

# ---------- Configuración de logging ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('price_alerts.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---------- Enums y clases ----------
class AlertDirection(Enum):
    ABOVE = "above"
    BELOW = "below"
    PERCENTAGE_CHANGE = "percentage_change"

class AssetType(Enum):
    STOCK = "stock"
    CRYPTO = "crypto"

@dataclass
class PriceData:
    price: float
    timestamp: datetime
    change_24h: Optional[float] = None
    volume: Optional[float] = None

# ---------- Rutas y configuración ----------
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
    "retry_delay": 5,
    "enable_logging": True,
    "notification_cooldown": 300,  # 5 minutos entre notificaciones del mismo activo
}

CRYPTO_SYMBOL_MAP = {
    "btc": "bitcoin", "eth": "ethereum", "ada": "cardano", "sol": "solana",
    "doge": "dogecoin", "matic": "polygon", "link": "chainlink", "dot": "polkadot",
    "xrp": "ripple", "ltc": "litecoin", "bch": "bitcoin-cash", "xlm": "stellar"
}

# ---------- Utilidades JSON ----------
def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {path}: {e}")
    return default.copy() if hasattr(default, 'copy') else default

def save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Error saving {path}: {e}")

# ---------- Cache de precios ----------
class PriceCache:
    def __init__(self, cache_duration_minutes: int = 2):
        self.cache_duration = timedelta(minutes=cache_duration_minutes)
        self.cache = load_json(CACHE_PATH, {})
        self._convert_timestamps()
    
    def _convert_timestamps(self):
        """Convierte timestamps string a datetime"""
        for key, value in self.cache.items():
            if isinstance(value.get('timestamp'), str):
                try:
                    value['timestamp'] = datetime.fromisoformat(value['timestamp'])
                except:
                    del self.cache[key]
    
    def get(self, key: str) -> Optional[PriceData]:
        if key in self.cache:
            data = self.cache[key]
            timestamp = data.get('timestamp')
            if isinstance(timestamp, datetime) and datetime.now() - timestamp < self.cache_duration:
                return PriceData(
                    price=data['price'],
                    timestamp=timestamp,
                    change_24h=data.get('change_24h'),
                    volume=data.get('volume')
                )
        return None
    
    def set(self, key: str, price_data: PriceData):
        self.cache[key] = {
            'price': price_data.price,
            'timestamp': price_data.timestamp,
            'change_24h': price_data.change_24h,
            'volume': price_data.volume
        }
        save_json(CACHE_PATH, self.cache)

# ---------- Funciones de precio mejoradas ----------
def get_stock_price(symbol: str, cache: PriceCache, max_retries: int = 3) -> PriceData:
    """Obtiene precio de acciones con retry y cache"""
    cache_key = f"stock_{symbol.upper()}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data
    
    for attempt in range(max_retries):
        try:
            ticker = yf.Ticker(symbol.upper())
            info = ticker.info
            
            # Intentar obtener precio actual
            current_price = None
            if hasattr(ticker, 'fast_info') and 'lastPrice' in ticker.fast_info:
                current_price = float(ticker.fast_info['lastPrice'])
            elif 'regularMarketPrice' in info:
                current_price = float(info['regularMarketPrice'])
            elif 'currentPrice' in info:
                current_price = float(info['currentPrice'])
            
            if current_price is None:
                raise ValueError("No se pudo obtener precio actual")
            
            # Datos adicionales
            change_24h = info.get('regularMarketChangePercent')
            volume = info.get('regularMarketVolume')
            
            price_data = PriceData(
                price=current_price,
                timestamp=datetime.now(),
                change_24h=change_24h,
                volume=volume
            )
            
            cache.set(cache_key, price_data)
            return price_data
            
        except Exception as e:
            logger.warning(f"Intento {attempt + 1} fallido para {symbol}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Backoff exponencial
            else:
                raise ValueError(f"No se pudo obtener precio para {symbol} después de {max_retries} intentos: {e}")

def get_crypto_price(symbol: str, cache: PriceCache, max_retries: int = 3) -> PriceData:
    """Obtiene precio de criptomonedas con retry y cache"""
    cache_key = f"crypto_{symbol.lower()}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data
    
    coin_id = CRYPTO_SYMBOL_MAP.get(symbol.lower(), symbol.lower())
    
    for attempt in range(max_retries):
        try:
            response = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd&include_24hr_change=true",
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if coin_id not in data:
                raise ValueError(f"Criptomoneda {symbol} no encontrada")
            
            coin_data = data[coin_id]
            price_data = PriceData(
                price=float(coin_data['usd']),
                timestamp=datetime.now(),
                change_24h=coin_data.get('usd_24h_change')
            )
            
            cache.set(cache_key, price_data)
            return price_data
            
        except Exception as e:
            logger.warning(f"Intento {attempt + 1} fallido para {symbol}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise ValueError(f"No se pudo obtener precio para {symbol} después de {max_retries} intentos: {e}")

def validate_symbol(symbol: str, asset_type: AssetType) -> bool:
    """Valida si un símbolo existe"""
    try:
        cache = PriceCache(cache_duration_minutes=0)  # No usar cache para validación
        if asset_type == AssetType.STOCK:
            get_stock_price(symbol, cache)
        else:
            get_crypto_price(symbol, cache)
        return True
    except:
        return False

# ---------- Sistema de notificaciones mejorado ----------
class NotificationManager:
    def __init__(self):
        self.last_notification = {}
    
    def should_notify(self, symbol: str) -> bool:
        """Verifica si puede enviar notificación (cooldown)"""
        last_time = self.last_notification.get(symbol)
        if last_time is None:
            return True
        cooldown_period = config.get("notification_cooldown", 300)
        return (datetime.now() - last_time).total_seconds() > cooldown_period
    
    def mark_notification_sent(self, symbol: str):
        """Marca que se envió una notificación"""
        self.last_notification[symbol] = datetime.now()

# Inicializar después de cargar la configuración
notification_manager = None

def send_email(subject: str, body: str) -> bool:
    """Envía email con manejo de errores mejorado"""
    required_fields = ["smtp_user", "smtp_pass", "email_to"]
    if not all(config.get(field) for field in required_fields):
        logger.error("Configuración SMTP incompleta")
        return False
    
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config["smtp_user"]
        msg["To"] = config["email_to"]
        msg.set_content(body)
        
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], context=context) as server:
            server.login(config["smtp_user"], config["smtp_pass"])
            server.send_message(msg)
        
        logger.info(f"Email enviado: {subject}")
        return True
        
    except Exception as e:
        logger.error(f"Error enviando email: {e}")
        return False

# ---------- Hilo de alertas mejorado ----------
def check_alerts():
    """Hilo principal para verificar alertas"""
    cache = PriceCache(config.get("cache_duration_minutes", 2))
    
    while True:
        try:
            interval = max(10, int(86400 / config.get("checks_per_day", 1440)))
            
            for item in watchlist:
                try:
                    # Obtener precio actual
                    if item["type"] == AssetType.STOCK.value:
                        price_data = get_stock_price(item["symbol"], cache, config.get("max_retries", 3))
                    else:
                        price_data = get_crypto_price(item["symbol"], cache, config.get("max_retries", 3))
                    
                    item["last_price"] = price_data.price
                    item["last_update"] = datetime.now().isoformat()
                    item["change_24h"] = price_data.change_24h
                    item.pop("error", None)  # Limpiar errores previos
                    
                    # Verificar condición de alerta
                    should_alert = False
                    alert_msg = ""
                    
                    if item["direction"] == AlertDirection.ABOVE.value:
                        should_alert = price_data.price >= item["target"]
                        alert_msg = f"≥ {item['target']:.2f}"
                    elif item["direction"] == AlertDirection.BELOW.value:
                        should_alert = price_data.price <= item["target"]
                        alert_msg = f"≤ {item['target']:.2f}"
                    
                    # Enviar alerta si es necesario
                    if (should_alert and 
                        not item.get("triggered", False) and 
                        notification_manager.should_notify(item["symbol"])):
                        
                        change_text = f" (Cambio 24h: {price_data.change_24h:.2f}%)" if price_data.change_24h else ""
                        body = f"""
Alerta activada para {item['symbol']} ({item['type']})
Condición: {alert_msg}
Precio actual: ${price_data.price:.2f}{change_text}
Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                        """.strip()
                        
                        if send_email(f"🚨 Alerta {item['symbol']} {alert_msg}", body):
                            item["triggered"] = True
                            item["triggered_at"] = datetime.now().isoformat()
                            notification_manager.mark_notification_sent(item["symbol"])
                
                except Exception as e:
                    logger.error(f"Error procesando {item.get('symbol', 'unknown')}: {e}")
                    item["error"] = str(e)
                    item["last_update"] = datetime.now().isoformat()
            
            # Guardar cambios
            save_json(WATCHLIST_PATH, watchlist)
            
        except Exception as e:
            logger.error(f"Error en check_alerts: {e}")
        
        time.sleep(interval)

# ---------- Cargar configuración ----------
config: Dict = load_json(CONFIG_PATH, DEFAULT_CONFIG)
watchlist: List[Dict] = load_json(WATCHLIST_PATH, [])

# Inicializar notification manager después de cargar config
notification_manager = NotificationManager()

# ---------- Inicializar hilo de alertas ----------
if "_alert_thread" not in st.session_state:
    alert_thread = threading.Thread(target=check_alerts, daemon=True)
    alert_thread.start()
    st.session_state["_alert_thread"] = True

# ---------- UI Principal ----------
# Configuración de página
st.set_page_config(
    page_title="⏰ Price Alerts Pro",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Auto-refresh
refresh_ms = max(30000, int(86400000 / config.get("checks_per_day", 1440)))
st_autorefresh(interval=refresh_ms, key="datarefresh")

# Título y métricas
st.title("⏰ Price Alerts Pro – Acciones & Cripto")

# Métricas principales
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Activos", len(watchlist))
with col2:
    active_alerts = len([item for item in watchlist if not item.get("triggered", False)])
    st.metric("Alertas Activas", active_alerts)
with col3:
    triggered_alerts = len([item for item in watchlist if item.get("triggered", False)])
    st.metric("Alertas Disparadas", triggered_alerts)
with col4:
    error_count = len([item for item in watchlist if item.get("error")])
    st.metric("Errores", error_count)

# ---------- Sidebar ----------
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # Configuración de alertas
    st.subheader("Alertas")
    config["checks_per_day"] = st.number_input(
        "Chequeos por día", 
        min_value=1, 
        max_value=1440, 
        value=int(config["checks_per_day"])
    )
    
    config["cache_duration_minutes"] = st.number_input(
        "Cache (minutos)", 
        min_value=1, 
        max_value=60, 
        value=int(config.get("cache_duration_minutes", 2))
    )
    
    config["notification_cooldown"] = st.number_input(
        "Cooldown notificaciones (seg)", 
        min_value=60, 
        max_value=3600, 
        value=int(config.get("notification_cooldown", 300))
    )
    
    st.markdown("---")
    
    # Configuración SMTP
    st.subheader("📧 Configuración Email")
    config["smtp_host"] = st.text_input("Host SMTP", value=config.get("smtp_host", "smtp.gmail.com"))
    config["smtp_port"] = st.number_input("Puerto SMTP", value=int(config.get("smtp_port", 465)))
    config["smtp_user"] = st.text_input("Usuario", value=config.get("smtp_user", ""))
    config["smtp_pass"] = st.text_input("Contraseña", value=config.get("smtp_pass", ""), type="password")
    config["email_to"] = st.text_input("Destinatario", value=config.get("email_to", ""))
    
    # Botones de acción
    col1, col2 = st.columns(2)
    with col1:
        if st.button("💾 Guardar"):
            save_json(CONFIG_PATH, config)
            st.success("✅ Guardado")
    
    with col2:
        if st.button("📧 Test Email"):
            if send_email("Test Price Alerts Pro", "Email de prueba funcionando correctamente."):
                st.success("✅ Email enviado")
            else:
                st.error("❌ Error enviando email")

# ---------- Agregar nuevo activo ----------
st.markdown("## ➕ Agregar Nuevo Activo")

with st.form("add_asset_form"):
    col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
    
    with col1:
        symbol = st.text_input("Símbolo", placeholder="Ej: AAPL, BTC, ETH")
    
    with col2:
        asset_type = st.selectbox("Tipo", [AssetType.STOCK.value, AssetType.CRYPTO.value])
    
    with col3:
        direction = st.selectbox("Condición", [AlertDirection.ABOVE.value, AlertDirection.BELOW.value])
    
    with col4:
        target = st.number_input("Precio USD", min_value=0.01, step=0.01)
    
    col1, col2 = st.columns([1, 4])
    with col1:
        submit = st.form_submit_button("Agregar")
    
    with col2:
        validate_button = st.form_submit_button("Validar Símbolo")

# Procesamiento del formulario
if submit and symbol and target > 0:
    # Validar símbolo
    with st.spinner("Validando símbolo..."):
        if validate_symbol(symbol, AssetType(asset_type)):
            new_item = {
                "symbol": symbol.upper(),
                "type": asset_type,
                "direction": direction,
                "target": target,
                "last_price": None,
                "triggered": False,
                "added_at": datetime.now().isoformat(),
                "last_update": None
            }
            watchlist.append(new_item)
            save_json(WATCHLIST_PATH, watchlist)
            st.success(f"✅ {symbol.upper()} agregado correctamente")
            st.rerun()
        else:
            st.error(f"❌ Símbolo {symbol} no válido o no encontrado")

elif validate_button and symbol:
    with st.spinner("Validando símbolo..."):
        if validate_symbol(symbol, AssetType(asset_type)):
            st.success(f"✅ {symbol.upper()} es válido")
        else:
            st.error(f"❌ {symbol.upper()} no válido")

# ---------- Filtros y búsqueda ----------
st.markdown("## 📊 Lista de Seguimiento")

if watchlist:
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        search = st.text_input("🔍 Buscar símbolo", placeholder="Filtrar por símbolo...")
    
    with col2:
        type_filter = st.selectbox("Filtrar por tipo", ["Todos", AssetType.STOCK.value, AssetType.CRYPTO.value])
    
    with col3:
        status_filter = st.selectbox("Filtrar por estado", ["Todos", "Activas", "Disparadas", "Con errores"])
    
    # Aplicar filtros
    filtered_watchlist = watchlist.copy()
    
    if search:
        filtered_watchlist = [item for item in filtered_watchlist if search.upper() in item["symbol"].upper()]
    
    if type_filter != "Todos":
        filtered_watchlist = [item for item in filtered_watchlist if item["type"] == type_filter]
    
    if status_filter == "Activas":
        filtered_watchlist = [item for item in filtered_watchlist if not item.get("triggered", False) and not item.get("error")]
    elif status_filter == "Disparadas":
        filtered_watchlist = [item for item in filtered_watchlist if item.get("triggered", False)]
    elif status_filter == "Con errores":
        filtered_watchlist = [item for item in filtered_watchlist if item.get("error")]
    
    # Mostrar tabla
    if filtered_watchlist:
        # Headers
        headers = ["Símbolo", "Tipo", "Condición", "Precio", "Cambio 24h", "Estado", "Última Act.", "Acciones"]
        cols = st.columns([1, 0.8, 1.2, 1, 1, 1, 1.2, 1])
        
        for col, header in zip(cols, headers):
            col.markdown(f"**{header}**")
        
        # Datos
        for idx, item in enumerate(filtered_watchlist):
            cols = st.columns([1, 0.8, 1.2, 1, 1, 1, 1.2, 1])
            
            # Símbolo
            cols[0].markdown(f"**{item['symbol']}**")
            
            # Tipo
            type_emoji = "📈" if item["type"] == AssetType.STOCK.value else "₿"
            cols[1].markdown(f"{type_emoji} {item['type']}")
            
            # Condición
            direction_emoji = "↗️" if item["direction"] == AlertDirection.ABOVE.value else "↘️"
            cols[2].markdown(f"{direction_emoji} ${item['target']:.2f}")
            
            # Precio actual
            if item.get("last_price") is not None:
                cols[3].markdown(f"${item['last_price']:.2f}")
            else:
                cols[3].markdown("⏳ Cargando...")
            
            # Cambio 24h
            if item.get("change_24h") is not None:
                change = item["change_24h"]
                color = "🟢" if change >= 0 else "🔴"
                cols[4].markdown(f"{color} {change:.2f}%")
            else:
                cols[4].markdown("─")
            
            # Estado
            if item.get("error"):
                cols[5].markdown("❌ Error")
            elif item.get("triggered", False):
                cols[5].markdown("🔔 Disparada")
            else:
                cols[5].markdown("🟢 Activa")
            
            # Última actualización
            if item.get("last_update"):
                try:
                    last_update = datetime.fromisoformat(item["last_update"])
                    cols[6].markdown(last_update.strftime("%H:%M:%S"))
                except:
                    cols[6].markdown("─")
            else:
                cols[6].markdown("─")
            
            # Acciones
            col_reset, col_delete = cols[7].columns(2)
            
            # Encontrar índice real en watchlist original
            real_idx = next(i for i, orig_item in enumerate(watchlist) if orig_item["symbol"] == item["symbol"])
            
            if item.get("triggered", False) and col_reset.button("🔄", key=f"reset_{real_idx}", help="Reactivar alerta"):
                watchlist[real_idx]["triggered"] = False
                watchlist[real_idx].pop("triggered_at", None)
                save_json(WATCHLIST_PATH, watchlist)
                st.rerun()
            
            if col_delete.button("🗑️", key=f"delete_{real_idx}", help="Eliminar"):
                del watchlist[real_idx]
                save_json(WATCHLIST_PATH, watchlist)
                st.rerun()
    
    else:
        st.info("No hay activos que coincidan con los filtros aplicados.")
    
    # Acciones masivas
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 1, 2])
    
    with col1:
        if st.button("🔄 Reactivar Todas"):
            for item in watchlist:
                item["triggered"] = False
                item.pop("triggered_at", None)
            save_json(WATCHLIST_PATH, watchlist)
            st.success("Todas las alertas reactivadas")
            st.rerun()
    
    with col2:
        if st.button("🗑️ Limpiar Lista"):
            if st.session_state.get("confirm_clear"):
                watchlist.clear()
                save_json(WATCHLIST_PATH, watchlist)
                st.success("Lista limpiada")
                st.session_state["confirm_clear"] = False
                st.rerun()
            else:
                st.session_state["confirm_clear"] = True
                st.warning("Presiona nuevamente para confirmar")

else:
    st.info("🚀 ¡Agrega tu primer activo para comenzar!")

# ---------- Footer ----------
st.markdown("---")
st.markdown("*Price Alerts Pro - Monitoreo inteligente de precios*")
