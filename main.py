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
# Duración por defecto (puedes poner 60 o 5)
DEFAULT_DURATION = int(os.getenv("DEFAULT_DURATION", "60")) 

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ================= CONEXIÓN IQ OPTION =================
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

# ================= PARSER INTELIGENTE DE SEÑALES =================
def extraer_datos_senal(texto):
    texto_upper = texto.upper()
    es_call = any(k in texto_upper for k in ["COMPRA", "CALL", "SUBE", "HIGHER"])
    es_put = any(k in texto_upper for k in ["VENTA", "PUT", "BAJA", "LOWER"])
    
    if not (es_call or es_put):
        return None, None, None

    direccion = "call" if es_call else "put"
    
    # 1. Detectar duración (5s, 15s, 30s, 60s, 1m, 60 seg)
    duracion = DEFAULT_DURATION
    if re.search(r"\b(60\s*(?:S|SEG|SEGUNDOS)?|1\s*(?:M|MIN|MINUTO)?)\b", texto_upper):
        duracion = 60
    elif re.search(r"\b5\s*(?:S|SEG|SEGUNDOS)?\b", texto_upper):
        duracion = 5
    elif re.search(r"\b15\s*(?:S|SEG|SEGUNDOS)?\b", texto_upper):
        duracion = 15
    elif re.search(r"\b30\s*(?:S|SEG|SEGUNDOS)?\b", texto_upper):
        duracion = 30

    # 2. Extraer activo o par (ej: EURUSD-OTC, GBPUSD, etc.)
    par_match = re.search(r"\b([A-Z0-9]{2,10}(?:-OTC)?)\b", texto_upper.replace("/", ""))
    palabras_reservadas = ["CALL", "PUT", "SUBE", "BAJA", "STATUS", "BLITZ", "60S", "5S", "15S", "30S", "1M"]
    activo = par_match.group(1) if (par_match and par_match.group(1) not in palabras_reservadas) else DEFAULT_ACTIVE

    return direccion, activo, duracion

# ================= DISPARADOR FLEXIBLE (BLITZ O 60S) =================
def ejecutar_orden(api, activo, direccion, duracion):
    """
    Si duracion == 60: ejecuta opción binaria de 1 minuto (o digital 1m).
    Si duracion in [5, 15, 30]: ejecuta digital_spot en modalidad Blitz ultrarrápida.
    """
    if duracion == 60:
        # Disparo clásico de 60 segundos (Binarias 1 minuto)
        try:
            check, id_trade = api.buy(TRADE_AMOUNT, activo, direccion, 1)
            if check and id_trade:
                return True, id_trade, "Binaria 60s (1m)"
        except Exception:
            pass

        # Fallback a Digital 1m si binarias está cerrada
        try:
            check, id_trade = api.buy_digital_spot(activo, TRADE_AMOUNT, direccion, 1)
            if check and id_trade:
                return True, id_trade, "Digital 60s (1m)"
        except Exception as e:
            return False, str(e), "Error"

    else:
        # Modo Blitz ultrarrápido (5s, 15s, 30s)
        try:
            check, id_trade = api.buy_digital_spot(activo, TRADE_AMOUNT, direccion, duracion)
            if check and id_trade:
                return True, id_trade, f"Blitz {duracion}s"
        except Exception:
            pass

        # Fallback a 1m si el broker no tiene habilitado Blitz en ese activo en ese momento
        try:
            check, id_trade = api.buy(TRADE_AMOUNT, activo, direccion, 1)
            if check and id_trade:
                return True, id_trade, "Fallback Binaria 1m"
        except Exception as e:
            return False, str(e), "Error"

    return False, "Fallo al colocar orden en IQ Option", "Error"

# ================= BUCLE PRINCIPAL =================
def main():
    Thread(target=iniciar_servidor_web, daemon=True).start()

    if not IQ_USER or not IQ_PASS:
        print("❌ [FATAL] Credenciales no configuradas.")
        return

    api = inicializar_iq()
    if not api:
        return

    try:
        requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except Exception:
        pass

    print("\n" + "=" * 55)
    print("  🚀 ATLEON IQ EJECUTOR DÚO (BLITZ & 60s) ACTIVO 🚀")
    print(f"  Modo: {IQ_ACCOUNT_TYPE} | Saldo: ${api.get_balance():.2f}")
    print("=" * 55 + "\n")

    last_update_id = 0

    while True:
        try:
            if not api.check_connect():
                print("[RECONEXIÓN] Reanudando WebSocket IQ...")
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
                        "text": f"📊 Atleon IQ Cloud:\n• Estado: 🟢 Conectado\n• Cuenta: {IQ_ACCOUNT_TYPE}\n• Saldo: ${saldo:.2f}\n• Modos: Blitz (5s/15s/30s) y 60s (1m)"
                    }, timeout=5)
                    continue

                direccion, activo, duracion = extraer_datos_senal(texto)
                if not direccion:
                    continue

                print(f"⚡ [TRIGGER] {direccion.upper()} | {activo} | Duración: {duracion}s | Monto: ${TRADE_AMOUNT}")
                exito, resultado, modo = ejecutar_orden(api, activo, direccion, duracion)

                if exito:
                    print(f"💥 [TRADE OK] ID: {resultado} ({modo})")
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"✅ Trade Ejecutado ({modo}):\n• Activo: {activo}\n• Dirección: {direccion.upper()}\n• Tiempo: {duracion}s\n• Monto: ${TRADE_AMOUNT}"
                    }, timeout=5)
                else:
                    print(f"⚠️ [FALLO TRADE]: {resultado}")
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"❌ Error en orden {activo} ({duracion}s): {resultado}"
                    }, timeout=5)

        except Exception as e:
            print(f"[LOOP ERR]: {e}")
            time.sleep(2)

        time.sleep(0.2)

if __name__ == "__main__":
    main()
