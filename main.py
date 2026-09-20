import os
import time
import re
import requests
from threading import Thread
from flask import Flask
from iqoptionapi.api import IQOptionAPI

# ================= SERVIDOR WEB (RENDER FREE TIER) =================
app = Flask(__name__)

@app.route('/')
def health_check():
    return "OK - Atleon IQ Executor Activo", 200

def iniciar_servidor_web():
    puerto = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=puerto)

# ================= CONFIGURACIÓN =================
IQ_USER = os.getenv("IQ_USER")
IQ_PASS = os.getenv("IQ_PASS")
IQ_ACCOUNT_TYPE = os.getenv("IQ_ACCOUNT_TYPE", "PRACTICE").upper()
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "1"))
DEFAULT_ACTIVE = os.getenv("DEFAULT_ACTIVE", "EURUSD")
EXPIRATION_TIME = int(os.getenv("EXPIRATION_TIME", "1"))

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ================= CONEXIÓN IQ OPTION =================
def inicializar_iq():
    print(f"\n[IQ] Conectando con {IQ_USER}...")
    api = IQOptionAPI("iqoption.com", IQ_USER, IQ_PASS)
    conectado, motivo = api.connect()

    if not conectado:
        print(f"❌ [ERROR IQ] Falla: {motivo}")
        return None

    # Método nativo sin guion bajo
    try:
        api.changebalance(IQ_ACCOUNT_TYPE)
    except Exception:
        pass

    saldo = api.get_balance()
    print(f"✅ [IQ CONECTADO] Cuenta: {IQ_ACCOUNT_TYPE} | Saldo: ${saldo:.2f}")
    return api

def extraer_datos_senal(texto):
    texto_upper = texto.upper()
    es_call = any(k in texto_upper for k in ["COMPRA", "CALL", "SUBE", "HIGHER"])
    es_put = any(k in texto_upper for k in ["VENTA", "PUT", "BAJA", "LOWER"])
    
    if not (es_call or es_put):
        return None, None

    direccion = "call" if es_call else "put"
    par_match = re.search(r"\b([A-Z]{3}/?[A-Z]{3}(?:-OTC)?)\b", texto_upper)
    activo = par_match.group(1).replace("/", "") if par_match else DEFAULT_ACTIVE

    return direccion, activo

# ================= BUCLE PRINCIPAL =================
def main():
    # Iniciar servidor HTTP en segundo plano
    Thread(target=iniciar_servidor_web, daemon=True).start()

    if not IQ_USER or not IQ_PASS:
        print("❌ [FATAL] Variables IQ_USER e IQ_PASS no configuradas.")
        return

    api = inicializar_iq()
    if not api:
        return

    try:
        requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except Exception:
        pass

    print("\n" + "=" * 55)
    print("  🚀 ATLEON IQ EXECUTOR ACTIVO EN RENDER (GRATIS) 🚀")
    print(f"  Modo: {IQ_ACCOUNT_TYPE} | Monto base: ${TRADE_AMOUNT}")
    print("=" * 55 + "\n")

    last_update_id = 0

    while True:
        try:
            if not api.check_connect():
                print("[RECONEXIÓN] Reconectando sesión de IQ Option...")
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
                print(f"\n[TELEGRAM RECIBIDO]: {texto}")

                if texto.upper().startswith("/STATUS"):
                    saldo = api.get_balance()
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"📊 IQ Option Cloud:\n• Estado: 🟢 Conectado\n• Cuenta: {IQ_ACCOUNT_TYPE}\n• Saldo: ${saldo:.2f}"
                    }, timeout=5)
                    continue

                direccion, activo = extraer_datos_senal(texto)
                if not direccion:
                    continue

                print(f"⚡ [DISPARO] Orden {direccion.upper()} en {activo} (${TRADE_AMOUNT})...")
                exito, resultado = api.buy(TRADE_AMOUNT, activo, direccion, EXPIRATION_TIME)

                if exito:
                    print(f"💥 [ORDEN EXITOSA] ID: {resultado}")
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"✅ Trade IQ Option Ejecutado:\n• Activo: {activo}\n• Tipo: {direccion.upper()}\n• Monto: ${TRADE_AMOUNT}\n• Expiración: {EXPIRATION_TIME}m"
                    }, timeout=5)
                else:
                    print(f"⚠️ [FALLO DE EJECUCIÓN]: {resultado}")
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"❌ Error al abrir en IQ ({activo} {direccion.upper()}): {resultado}"
                    }, timeout=5)

        except Exception as e:
            print(f"[EXCEPCIÓN LOOP]: {e}")
            time.sleep(2)

        time.sleep(0.3)

if __name__ == "__main__":
    main()
