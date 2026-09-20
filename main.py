import os
import time
import re
import requests
import threading
from flask import Flask
from iqoptionapi.stable_api import IQ_Option

# ================= SERVIDOR WEB (RENDER FREE TIER) =================
app = Flask(__name__)

@app.route('/')
def health():
    return "OK - Atleon IQ Executor Activo", 200

def iniciar_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ================= CONFIGURACIÓN =================
IQ_USER = os.getenv("IQ_USER")
IQ_PASS = os.getenv("IQ_PASS")
IQ_ACCOUNT_TYPE = os.getenv("IQ_ACCOUNT_TYPE", "PRACTICE").upper()
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "1"))
DEFAULT_ACTIVE = os.getenv("DEFAULT_ACTIVE", "EURUSD-OTC")
DEFAULT_DURATION = int(os.getenv("DEFAULT_DURATION", "60"))

BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Variables de estado global
iq_api = None
iq_conectado = False
lock_iq = threading.Lock()

# ================= GESTOR CONEXIÓN IQ OPTION =================
def conectar_iq():
    global iq_api, iq_conectado
    with lock_iq:
        try:
            print(f"[IQ] Conectando a {IQ_USER}...")
            cliente = IQ_Option(IQ_USER, IQ_PASS)
            ok, motivo = cliente.connect()
            if ok:
                cliente.change_balance(IQ_ACCOUNT_TYPE)
                iq_api = cliente
                iq_conectado = True
                print(f"✅ [IQ CONECTADO] Cuenta: {IQ_ACCOUNT_TYPE} | Saldo: ${iq_api.get_balance():.2f}")
            else:
                print(f"❌ [IQ ERROR]: {motivo}")
                iq_conectado = False
        except Exception as e:
            print(f"❌ [IQ EXCEPCIÓN]: {e}")
            iq_conectado = False

def asegurar_conexion():
    global iq_api, iq_conectado
    if not iq_conectado or iq_api is None or not iq_api.check_connect():
        conectar_iq()
    return iq_conectado

# ================= PARSER DE ATLEON TERMINAL =================
def extraer_datos(texto):
    texto_upper = texto.upper()

    # 1. Dirección (CALL / PUT)
    direccion = None
    if any(k in texto_upper for k in ["COMPRA", "CALL", "SUBE", "HIGHER"]):
        direccion = "call"
    elif any(k in texto_upper for k in ["VENTA", "PUT", "BAJA", "LOWER"]):
        direccion = "put"

    if not direccion:
        return None, None, None

    # 2. Activo
    activo = DEFAULT_ACTIVE
    match_activo = re.search(r"ACTIVO:\s*([A-Z0-9_\-]+)", texto_upper)
    if match_activo:
        activo = match_activo.group(1).strip()
    else:
        par_match = re.search(r"\b([A-Z0-9]{3,6}(?:-OTC)?)\b", texto_upper.replace("/", ""))
        palabras_filtro = ["CALL", "PUT", "SUBE", "BAJA", "STATUS", "BLITZ", "COMPRA", "VENTA", "HORA", "PRECIO", "SENAL", "30S", "60S", "5S", "15S", "1M"]
        if par_match and par_match.group(1) not in palabras_filtro:
            activo = par_match.group(1)

    # 3. Duración (Blitz o 60s)
    duracion = DEFAULT_DURATION
    if re.search(r"\b30\s*(?:S|SEG)?\b", texto_upper):
        duracion = 30
    elif re.search(r"\b5\s*(?:S|SEG)?\b", texto_upper):
        duracion = 5
    elif re.search(r"\b15\s*(?:S|SEG)?\b", texto_upper):
        duracion = 15
    elif re.search(r"\b(60\s*(?:S|SEG)?|1\s*(?:M|MIN)?)\b", texto_upper):
        duracion = 60

    return direccion, activo, duracion

# ================= DISPARADOR CON TIMEOUT =================
def ejecutar_trade(activo, direccion, duracion):
    global iq_api
    if not asegurar_conexion():
        return False, "IQ Option desconectado", "Error"

    resultado = {"exito": False, "id": None, "modo": "Error"}

    def tarea():
        try:
            # Modalidad Blitz (5s, 15s, 30s)
            if duracion in [5, 15, 30]:
                ok, res_id = iq_api.buy_digital_spot(activo, TRADE_AMOUNT, direccion, duracion)
                if ok and res_id:
                    resultado["exito"] = True
                    resultado["id"] = res_id
                    resultado["modo"] = f"Blitz {duracion}s"
                    return

            # Modalidad Estándar 60s / Binaria Turbo
            ok, res_id = iq_api.buy(TRADE_AMOUNT, activo, direccion, 1)
            if ok and res_id:
                resultado["exito"] = True
                resultado["id"] = res_id
                resultado["modo"] = "Binaria 60s"
            else:
                resultado["id"] = str(res_id)
        except Exception as err:
            resultado["id"] = str(err)

    hilo = threading.Thread(target=tarea)
    hilo.start()
    hilo.join(timeout=3.5)  # Máximo 3.5 segundos de espera al broker

    if hilo.is_alive():
        return False, "Timeout esperando respuesta de IQ", "Timeout"

    return resultado["exito"], resultado["id"], resultado["modo"]

# ================= BUCLE TELEGRAM =================
def loop_telegram():
    try:
        requests.get(f"{TG_API}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except Exception:
        pass

    last_update_id = 0
    print("🤖 [TELEGRAM] Escuchando mensajes...")

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

                # Comando de Estado
                if texto.upper().startswith("/STATUS"):
                    asegurar_conexion()
                    saldo_txt = f"${iq_api.get_balance():.2f}" if (iq_conectado and iq_api) else "Desconectado"
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"📊 Atleon IQ Cloud:\n• Estado: {'🟢 Conectado' if iq_conectado else '🔴 Desconectado'}\n• Saldo: {saldo_txt}\n• Modos: Blitz (30s) / 60s"
                    }, timeout=5)
                    continue

                # Señal de Trading
                direccion, activo, duracion = extraer_datos(texto)
                if not direccion:
                    continue

                print(f"⚡ [DISPARO] {activo} | {direccion.upper()} | {duracion}s")
                exito, id_trade, modo = ejecutar_trade(activo, direccion, duracion)

                if exito:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"✅ Trade Ejecutado ({modo}):\n• Par: {activo}\n• Tipo: {direccion.upper()}\n• Monto: ${TRADE_AMOUNT}"
                    }, timeout=5)
                else:
                    requests.post(f"{TG_API}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"⚠️ Orden no completada en {activo}: {id_trade}"
                    }, timeout=5)

        except Exception as e:
            print(f"[LOOP TG ERR]: {e}")
            time.sleep(1)

        time.sleep(0.1)

# ================= INICIO =================
def main():
    # 1. Iniciar servidor HTTP en segundo plano para Render
    threading.Thread(target=iniciar_web, daemon=True).start()

    # 2. Conectar IQ Option en segundo plano (para no retrasar Telegram)
    threading.Thread(target=conectar_iq, daemon=True).start()

    # 3. Iniciar bucle de Telegram en el hilo principal
    loop_telegram()

if __name__ == "__main__":
    main()
