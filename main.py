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

api = None
conectado_iq = False

# ================= GESTOR DE CONEXIÓN IQ OPTION =================
def conectar_iq_seguro():
    global api, conectado_iq
    try:
        print(f"[IQ] Intentando conexión con {IQ_USER}...")
        instancia = IQ_Option(IQ_USER, IQ_PASS)
        check, reason = instancia.connect()
        if check:
            instancia.change_balance(IQ_ACCOUNT_TYPE)
            api = instancia
            conectado_iq = True
            print(f"✅ [IQ CONECTADO EXITOSO] Saldo: ${api.get_balance()}")
        else:
            print(f"❌ [IQ FALLO]: {reason}")
            conectado_iq = False
    except Exception as e:
        print(f"❌ [IQ EXCEPCION]: {e}")
        conectado_iq = False

# ================= PARSER DE ATLEON TERMINAL =================
def parsear_mensaje(texto):
    texto_upper = texto.upper()

    direccion = None
    if any(k in texto_upper for k in ["COMPRA", "CALL", "SUBE", "HIGHER"]):
        direccion = "call"
    elif any(k in texto_upper for k in ["VENTA", "PUT", "BAJA", "LOWER"]):
        direccion = "put"

    if not direccion:
        return None, None, None

    activo = DEFAULT_ACTIVE
    match_radar = re.search(r"ACTIVO:\s*([A-Z0-9_\-]+)", texto_upper)
    if match_radar:
        activo = match_radar.group(1).strip()
    else:
        par_match = re.search(r"\b([A-Z0-9]{2,10}(?:-OTC)?)\b", texto_upper.replace("/", ""))
        palabras_ignorar = ["CALL", "PUT", "SUBE", "BAJA", "STATUS", "BLITZ", "COMPRA", "VENTA", "HORA", "PRECIO", "SENAL", "30S", "60S", "5S", "15S", "1M"]
        if par_match and par_match.group(1) not in palabras_ignorar:
            activo = par_match.group(1)

    duracion = DEFAULT_DURATION
    if re.search(r"\b(30\s*(?:S|SEG)?)\b", texto_upper):
        duracion = 30
    elif re.search(r"\b(60\s*(?:S|SEG)?|1\s*(?:M|MIN)?)\b", texto_upper):
        duracion = 60
    elif re.search(r"\b(5\s*(?:S|SEG)?)\b", texto_upper):
        duracion = 5

    return direccion, activo, duracion

# ================= EJECUCIÓN DIRECTA =================
def ejecutar_trade(activo, direccion, duracion):
    global api
    if not api or not conectado_iq:
        return False, "Sesión de broker no lista", "Desconectado"

    # 1. Intento Digital / Blitz
    if duracion in [5, 15, 30]:
        try:
            ok, res_id = api.buy_digital_spot(activo, TRADE_AMOUNT, direccion, 1)
            if ok and res_id:
                return True, res_id, f"Blitz {duracion}s"
        except Exception:
            pass

    # 2. Operación Turbo / Binaria (Estándar 60s)
    try:
        ok, res_id = api.buy(TRADE_AMOUNT, activo, direccion, 1)
        if ok and res_id:
            return True, res_id, "Binaria 60s"
        else:
            return False, str(res_id), "Fallo Broker"
    except Exception as e:
        return False, str(e), "Excepción"

# ================= BUCLE TELEGRAM =================
def loop_telegram():
    global api, conectado_iq
    try:
        requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except Exception:
        pass

    last_update_id = 0
    print("🤖 [TELEGRAM LISTO] Escuchando actualizaciones de comandos y alertas...")

    while True:
        try:
            url = f"{TG_API}/getUpdates?offset={last_update_id + 1}&timeout=5"
            res = requests.get(url, timeout=10).json()

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
                print(f"[RECEPTOR TG]: {texto}")

                if texto.upper().startswith("/STATUS"):
                    estado_str = "🟢 Conectado" if conectado_iq else "🔴 Reconectando..."
                    saldo_str = f"${api.get_balance():.2f}" if (conectado_iq and api) else "Cargando..."
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"📊 Atleon IQ Cloud:\n• Estado: {estado_str}\n• Cuenta: {IQ_ACCOUNT_TYPE}\n• Saldo: {saldo_str}\n• Ejecución activa 24/7"
                    }, timeout=5)
                    continue

                direccion, activo, duracion = parsear_mensaje(texto)
                if not direccion:
                    continue

                if not conectado_iq:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "⚠️ Reconectando con IQ Option, reintenta en un momento..."
                    }, timeout=5)
                    Thread(target=conectar_iq_seguro).start()
                    continue

                print(f"⚡ [DISPARO] {activo} | {direccion.upper()} | {duracion}s")
                exito, id_orden, modo = ejecutar_trade(activo, direccion, duracion)

                if exito:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"✅ Trade Atleon Ejecutado ({modo}):\n• Par: {activo}\n• Señal: {direccion.upper()}\n• Monto: ${TRADE_AMOUNT}"
                    }, timeout=5)
                else:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"⚠️ Broker rechazó orden ({activo}): {id_orden}"
                    }, timeout=5)

        except Exception as e:
            print(f"[LOOP TG ERROR]: {e}")
            time.sleep(1)

        time.sleep(0.1)

# ================= ENTRADA PRINCIPAL =================
def main():
    Thread(target=iniciar_servidor_web, daemon=True).start()
    conectar_iq_seguro()
    loop_telegram()

if __name__ == "__main__":
    main()
