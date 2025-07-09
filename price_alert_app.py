# --- Agregar activo ---
st.markdown("## ➕ Agregar activo a seguimiento")

# Inicializa las claves en st.session_state si no existen para los inputs de añadir activo
if "add_symbol_input_value" not in st.session_state:
    st.session_state.add_symbol_input_value = ""
if "add_target_price_input_value" not in st.session_state:
    st.session_state.add_target_price_input_value = 0.01
if "add_asset_type_select_value" not in st.session_state:
    st.session_state.add_asset_type_select_value = "stock" # O el valor por defecto que quieras
if "add_direction_select_value" not in st.session_state:
    st.session_state.add_direction_select_value = "above" # O el valor por defecto que quieras


col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1:
    symbol = st.text_input(
        "Ticker / Símbolo",
        placeholder="AAPL o BTC",
        key="symbol_input", # Mantén esta key para identificar el widget
        value=st.session_state.add_symbol_input_value # Controla el valor con session_state
    )
with col2:
    asset_type = st.selectbox(
        "Tipo",
        ["stock", "crypto"],
        index=["stock", "crypto"].index(st.session_state.add_asset_type_select_value), # Usa el valor de session_state para el index
        key="asset_type_select"
    )
with col3:
    direction = st.selectbox(
        "Condición",
        ["above", "below"],
        index=["above", "below"].index(st.session_state.add_direction_select_value),
        key="direction_select"
    )
with col4:
    target = st.number_input(
        "Precio USD",
        min_value=0.01,
        step=0.01,
        format="%.2f",
        key="target_price_input", # Mantén esta key
        value=st.session_state.add_target_price_input_value # Controla el valor con session_state
    )

if st.button("Agregar a lista", key="add_to_list_btn"):
    if symbol and target > 0:
        # Normalizar el símbolo para cripto para evitar duplicados si se ingresa con mayúsculas/minúsculas diferentes
        processed_symbol = symbol.upper() if asset_type == "stock" else symbol.lower()
        
        # Verificar si el activo ya está en la lista para evitar duplicados
        exists = any(
            item["symbol"].lower() == processed_symbol.lower() and item["type"] == asset_type
            for item in watchlist
        )
        if exists:
            st.warning(f"{symbol.upper() if asset_type == 'stock' else symbol.lower()} ya está en la lista de seguimiento.")
        else:
            new_item = {
                "symbol": processed_symbol,
                "type": asset_type,
                "direction": direction,
                "target": target,
                "last": None,
                "triggered": False,
                "error": None, # Inicializar sin errores
                "last_checked": None, # Para mostrar cuándo se verificó por última vez
                "last_triggered_time": None # Para registrar cuándo se disparó la última alerta
            }
            watchlist.append(new_item)
            save_json(WATCHLIST_PATH, watchlist)
            st.success(f"**{symbol.upper() if asset_type == 'stock' else symbol.lower()}** agregado a la lista ✔️")
            
            # Limpiar los campos de entrada después de agregar
            # Esto NO es una modificación directa del widget, sino una actualización
            # del valor en st.session_state que se usará en la PRÓXIMA re-ejecución
            st.session_state.add_symbol_input_value = ""
            st.session_state.add_target_price_input_value = 0.01
            # Para los selectbox, puedes resetear al índice 0 o a un valor específico
            st.session_state.add_asset_type_select_value = "stock"
            st.session_state.add_direction_select_value = "above"
            
            # Es importante volver a ejecutar el script para que los cambios se reflejen
            st.rerun() # Esto fuerza un rerun inmediato del script
            
    else:
        st.error("Por favor, completa el símbolo y un precio objetivo válido.")
