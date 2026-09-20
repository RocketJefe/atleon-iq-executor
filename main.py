import os
import time
import re
import requests
from threading import Thread
from flask import Flask
from iqoptionapi.stable_api import IQ_Option

# ================= SERVIDOR WEB (RENDER FREE TIER) =================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "OK - Atleon IQ Blitz & 60s Executor Activo", 200

def iniciar_servidor_web():
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)

# ================= CONFIGURACIÓN =================
IQ_USER = os.getenv("IQ_USER")
IQ_PASS = os.getenv("IQ_PASS")
IQ_ACCOUNT_TYPE = os.getenv("IQ_ACCOUNT_TYPE", "PRACTICE").upper()
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "1"))
DEFAULT_ACTIVE = os.getenv("DEFAULT_ACTIVE", "EURUSD-OTC")
DEFAULT_DURATION = int(os.getenv("DEFAULT_DURATION", "60"))

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def inicializar_iq():
    print(f"\n[IQ] Conectando con {IQ_USER}...")
    api = IQ_Option(IQ_USER, IQ_PASS)
    conectado, motivo = api.connect()

    if not conectado:
        print(f"❌ [ERROR IQ] Falla: {motivo}")
        return None

    api.change_balance(IQ_ACCOUNT_TYPE)
    saldo = api.get_balance()
    print(f"✅ [IQ CONECTADO] Cuenta: {IQ_ACCOUNT_TYPE} | Saldo: ${saldo}")
    return api

def extraer_datos_senal(texto):
    texto_upper = texto.upper()
    
    # 1. Dirección
    es_call = any(k in texto_upper for k in ["COMPRA", "CALL", "SUBE", "HIGHER"])
    es_put = any(k in texto_upper for k in ["VENTA", "PUT", "BAJA", "LOWER"])
    
    if not (es_call or es_put):
        return None, None, None

    direccion = "call" if es_call else "put"
    
    # 2. Duración
    duracion = DEFAULT_DURATION
    if re.search(r"\b5\s*(?:S|SEG)?\b", texto_upper):
        duracion = 5
    elif re.search(r"\b15\s*(?:S|SEG)?\b", texto_upper):
        duracion = 15
    elif re.search(r"\b30\s*(?:S|SEG)?\b", texto_upper):
        duracion = 30
    elif re.search(r"\b(60\s*(?:S|SEG)?|1\s*(?:M|MIN)?)\b", texto_upper):
        duracion = 60

    # 3. Activo
    # Limpiar palabras clave para aislar el activo
    limpio = texto_upper
    for palabra in ["COMPRA", "CALL", "SUBE", "HIGHER", "VENTA", "PUT", "BAJA", "LOWER", 
                    "BLITZ", "5S", "15S", "30S", "60S", "1M", "SEG", "SEGUNDOS"]:
        limpio = re.sub(rf"\b{palabra}\b", "", limpio)
    
    tokens = [t.strip() for t in re.split(r"[\s/]+", limpio) if len(t.strip()) >= 2]
    
    activo = DEFAULT_ACTIVE
    if tokens:
        # Tomar el primer token representativo (ej: GER30, EURUSD-OTC, TRUMP)
        activo = tokens[0]

    return direccion, activo, duracion

def ejecutar_orden(api, activo, direccion, duracion):
    """
    Intenta ejecutar la orden con timeout y reporte directo de error.
    """
    # Manejo de Blitz (5s, 15s, 30s)
    if duracion in [5, 15, 30]:
        try:
            check, id_trade = api.buy_digital_spot(activo, TRADE_AMOUNT, direccion, duracion)
            if check and id_trade:
                return True, id_trade, f"Blitz {duracion}s"
        except Exception as e:
            print(f"[BLITZ SPOT ERROR]: {e}")

        # Si el activo tiene sufijo -OTC o formato binario y falla en Blitz, avisar
        return False, f"El par {activo} no admite Blitz de {duracion}s en este momento.", "Error"

    # Manejo estándar de 60 segundos
    try:
        check, id_trade = api.buy(TRADE_AMOUNT, activo, direccion, 1)
        if check and id_trade:
            return True, id_trade, "Binaria 60s (1m)"
    except Exception as e:
        print(f"[BINARIA ERROR]: {e}")

    # Fallback Digital 1m
    try:
        check, id_trade = api.buy_digital_spot(activo, TRADE_AMOUNT, direccion, 1)
        if check and id_trade:
            return True, id_trade, "Digital 60s (1m)"
    except Exception:
        pass

    return False, f"No se pudo abrir orden en {activo}", "Error"

# ================= BUCLE PRINCIPAL =================
def main():
    Thread(target=iniciar_servidor_web, daemon=True).start()

    if not IQ_USER or not IQ_PASS:
        print("❌ Variables de credenciales no configuradas.")
        return

    api = inicializar_iq()
    if not api:
        return

    try:
        requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except Exception:
        pass

    print("\n" + "=" * 55)
    print("  🚀 ATLEON IQ EXECUTOR V3 (BLITZ + 60S LISTO) 🚀")
    print("=" * 55 + "\n")

    last_update_id = 0

    while True:
        try:
            if not api.check_connect():
                print("[RECONEXIÓN] Conectando sesión...")
                api.connect()

            url = f"{TG_API}/getUpdates?offset={last_update_id + 1}&timeout=10"
            res = requests.get(url, timeout=15).json()

            if not res.get("ok"):
                time.sleep(1)
                continue

            for item in res.get("result", []):
                last_update_id = item["update_id"]
                msg = item.get("message") or item.get("channel_post")
                if not msg or "text" not in msg:
                    continue

                chat_id = msg["chat"]["id"]
                texto = msg["text"].strip()
                print(f"\n[TELEGRAM]: {texto}")

                if texto.upper().startswith("/STATUS"):
                    saldo = api.get_balance()
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"📊 Atleon IQ Cloud:\n• Estado: 🟢 Conectado\n• Saldo: ${saldo:.2f}\n• Listo para: Blitz (5s/15s/30s) y 60s"
                    }, timeout=5)
                    continue

                direccion, activo, duracion = extraer_datos_senal(texto)
                if not direccion:
                    # Si no detectó dirección, notificar al usuario para no quedar en silencio
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"⚠️ Mensaje recibido sin dirección clara (SUBE/BAJA/CALL/PUT): '{texto}'"
                    }, timeout=5)
                    continue

                print(f"⚡ [DISPARO] {direccion.upper()} | {activo} | {duracion}s | ${TRADE_AMOUNT}")
                exito, resultado, modo = ejecutar_orden(api, activo, direccion, duracion)

                if exito:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"✅ Trade Ejecutado ({modo}):\n• Activo: {activo}\n• Dirección: {direccion.upper()}\n• Tiempo: {duracion}s\n• Monto: ${TRADE_AMOUNT}"
                    }, timeout=5)
                else:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"❌ Error en orden ({duracion}s): {resultado}"
                    }, timeout=5)

        except Exception as e:
            print(f"[LOOP EXCEPTION]: {e}")
            time.sleep(2)

        time.sleep(0.2)

if __name__ == "__main__":
    main()
