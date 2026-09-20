import os
import time
import re
import requests
import concurrent.futures
from threading import Thread
from flask import Flask
from iqoptionapi.stable_api import IQ_Option

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
DEFAULT_ACTIVE = os.getenv("DEFAULT_ACTIVE", "EURUSD-OTC")
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

# ================= PARSER DE SEÑALES =================
def parsear_mensaje(texto):
    texto_upper = texto.upper()

    # 1. Dirección
    direccion = None
    if any(k in texto_upper for k in ["COMPRA", "CALL", "SUBE", "HIGHER"]):
        direccion = "call"
    elif any(k in texto_upper for k in ["VENTA", "PUT", "BAJA", "LOWER"]):
        direccion = "put"

    if not direccion:
        return None, None, None

    # 2. Activo: Soporta formato estructurado de radar o texto manual
    activo = DEFAULT_ACTIVE
    match_radar = re.search(r"ACTIVO:\s*([A-Z0-9_\-]+)", texto_upper)
    if match_radar:
        activo = match_radar.group(1).strip()
    else:
        par_match = re.search(r"\b([A-Z0-9]{2,10}(?:-OTC)?)\b", texto_upper.replace("/", ""))
        palabras_ignorar = ["CALL", "PUT", "SUBE", "BAJA", "STATUS", "BLITZ", "COMPRA", "VENTA", "HORA", "PRECIO", "SENAL", "30S", "60S", "5S", "15S", "1M"]
        if par_match and par_match.group(1) not in palabras_ignorar:
            activo = par_match.group(1)

    # 3. Duración (30s, 60s, 5s)
    duracion = DEFAULT_DURATION
    if re.search(r"\b(30\s*(?:S|SEG)?)\b", texto_upper):
        duracion = 30
    elif re.search(r"\b(5\s*(?:S|SEG)?)\b", texto_upper):
        duracion = 5
    elif re.search(r"\b(15\s*(?:S|SEG)?)\b", texto_upper):
        duracion = 15
    elif re.search(r"\b(60\s*(?:S|SEG)?|1\s*(?:M|MIN)?)\b", texto_upper):
        duracion = 60

    return direccion, activo, duracion

# ================= EJECUCIÓN CON TIMEOUT (ANTI-CONGELAMIENTO) =================
def orden_interna(api, activo, direccion, duracion):
    # Si se pide Blitz (5s, 15s, 30s)
    if duracion in [5, 15, 30]:
        try:
            ok, res = api.buy_digital_spot(activo, TRADE_AMOUNT, direccion, duracion)
            if ok and res:
                return True, res, f"Blitz {duracion}s"
        except Exception:
            pass

    # Modo estándar / Fallback: Binaria 1m (60s)
    try:
        ok, res = api.buy(TRADE_AMOUNT, activo, direccion, 1)
        if ok and res:
            return True, res, "Binaria 60s"
    except Exception:
        pass

    # Segundo fallback: Digital 1m
    try:
        ok, res = api.buy_digital_spot(activo, TRADE_AMOUNT, direccion, 1)
        if ok and res:
            return True, res, "Digital 60s"
    except Exception as e:
        return False, str(e), "Error"

    return False, "Activo cerrado o no disponible", "Error"

def disparar_con_timeout(api, activo, direccion, duracion):
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(orden_interna, api, activo, direccion, duracion)
    try:
        return future.result(timeout=4)  # Máximo 4 segundos de espera
    except concurrent.futures.TimeoutError:
        return False, "Tiempo de espera agotado en broker (Timeout)", "Timeout"
    finally:
        executor.shutdown(wait=False)

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
    print("  🚀 ATLEON IQ EXECUTOR V4 (ANTIBLOQUEO ACTIVO) 🚀")
    print("=" * 55 + "\n")

    last_update_id = 0

    while True:
        try:
            if not api.check_connect():
                print("[RECONEXIÓN] Reconectando sesión...")
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
                print(f"\n[MENSAJE DETECTADO]:\n{texto}")

                if texto.upper().startswith("/STATUS"):
                    saldo = api.get_balance()
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"📊 Atleon IQ Conectado:\n• Saldo: ${saldo:.2f}\n• Cuenta: {IQ_ACCOUNT_TYPE}\n• Listo para operar 24/7"
                    }, timeout=5)
                    continue

                direccion, activo, duracion = parsear_mensaje(texto)
                if not direccion:
                    continue

                print(f"⚡ [DISPARO] {activo} | {direccion.upper()} | {duracion}s")
                exito, resultado, modo = disparar_con_timeout(api, activo, direccion, duracion)

                if exito:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"✅ Trade Ejecutado ({modo}):\n• Par: {activo}\n• Tipo: {direccion.upper()}\n• Monto: ${TRADE_AMOUNT}"
                    }, timeout=5)
                else:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"⚠️ Fallo al abrir {activo} ({duracion}s): {resultado}"
                    }, timeout=5)

        except Exception as e:
            print(f"[LOOP EXCEPTION]: {e}")
            time.sleep(2)

        time.sleep(0.2)

if __name__ == "__main__":
    main()
