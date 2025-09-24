#!/usr/bin/env python3
import os, sys, json, time, requests
from datetime import datetime, timedelta
import pytz
from ics import Calendar
from dotenv import load_dotenv
import mimetypes


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# Cargar variables desde .env
ENV_PATH = os.path.join(BASE_DIR, "config", ".env")
load_dotenv(ENV_PATH)
# load_dotenv()

# ============ CONFIG ============
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")  # requerido
ICS_URL      = os.getenv("ICS_URL")       # requerido
TZ_STR       = os.getenv("TZ", "Europe/Madrid")

# USERS_JSON    = os.getenv("USERS_JSON", "users.json")
# PENDING_JSON  = os.getenv("PENDING_JSON", "pending_review.json")
# STATE_FILE    = os.getenv("UPDATES_STATE", "last_update_id.json")
MAX_NEW_PER_RUN = int(os.getenv("MAX_NEW_PER_RUN", "30"))
DEBUG_NOTIFY_NEXT = os.getenv("DEBUG_NOTIFY_NEXT", "0") in ("1", "true", "True", "YES", "yes")

def to_abs(path, default_rel):
    p = os.getenv(path, default_rel)
    return p if os.path.isabs(p) else os.path.join(BASE_DIR, p)

USERS_JSON   = to_abs("USERS_JSON",   "data/users.json")
PENDING_JSON = to_abs("PENDING_JSON", "data/pending_review.json")
STATE_FILE   = to_abs("UPDATES_STATE","data/last_update_id.json")
LOG_DIR      = to_abs("LOG_DIR",      "logs")
BETIS_GIF    = to_abs("BETIS_GIF",    "media/betis.gif")

# BETIS_GIF = os.getenv("BETIS_GIF", "media/betis.gif")


# WELCOME_TEXT = "¡Apuntado para recibir avisos del Betis! ⚽️"

WELCOME_TEXT = (
    "¡Apuntado para recibir avisos del Betis! ⚽️\n\n"
    "Comandos disponibles:\n"
    "• /stop — darte de baja y dejar de recibir avisos\n"
    "• /start — volver a darte de alta si estabas de baja\n"
    "• /help — ver esta ayuda\n"
)

WELCOME_BACK_TEXT = (
    "¡Te hemos vuelto a activar! ✅\n"
    "Recibirás avisos el día antes y el mismo día del partido.\n"
    "Si quieres darte de baja, usa /stop."
)

HELP_TEXT = (
    "Ayuda del bot ⚽️\n\n"
    "• Recibirás avisos el día antes y el mismo día del partido.\n"
    "• /start — darte de alta / reactivar si estabas de baja.\n"
    "• /stop — darte de baja y no recibir avisos.\n"
    "• /help — ver esta ayuda.\n"
)

GOODBYE_TEXT = "Has sido dado de baja. Si quieres volver a apuntarte, escribe /start."
ADMIN_ALERT_TITLE = "⚠️ Alta masiva pendiente de revisión"
ADMIN_ALERT_BODY  = "Se han detectado {n} nuevas altas. Están en pending_review.json. No se añadieron automáticamente."
SEND_ERRORS_TITLE = "⚠️ Incidencias en el envío de recordatorios"
RUN_PING_TITLE    = "🟢 Ejecución realizada"

# Mensajes de eventos
# HEADER_TODAY = "💚🤍  ¡DÍA DE BETIS!  🤍💚"
# SUBHEADER_TODAY = "🔥 Partido(s) de HOY:"
# FOOTER_TODAY = "🎉 ¡Mucho Betis! #DíaDeBetis"

# HEADER_TOMORROW = "⏰ Recordatorio: mañana juega el Betis"
HEADER_TODAY = "💚🤍  <b>¡DÍA DE BETIS!</b>  🤍💚"
SUBHEADER_TODAY = "<b>🔥 Partido(s) de HOY:</b>"
FOOTER_TODAY = "🎉 ¡Mucho Betis! #DíaDeBetis"

HEADER_TOMORROW = "⏰ <b>Recordatorio: mañana juega el Betis</b>"

# ---- LOGGING POR EJECUCIÓN ----
# LOG_DIR = os.getenv("LOG_DIR", "logs")

def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)

def new_log_file():
    ensure_log_dir()
    ts = datetime.now(tz).strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(LOG_DIR, f"run_{ts}.log")

def write_log(path, *parts):
    with open(path, "a", encoding="utf-8") as f:
        f.write(" ".join(str(p) for p in parts) + "\n")




# ===============================

def require_env(var):
    if not os.getenv(var):
        print(f"Falta {var}", file=sys.stderr); sys.exit(1)
require_env("TG_BOT_TOKEN")
require_env("ICS_URL")

tz = pytz.timezone(TZ_STR)

# ---------- Utils JSON ----------
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

# ---------- Usuarios ----------
def load_users():
    data = load_json(USERS_JSON, [])
    norm, seen = [], set()
    for u in data:
        if not u: continue
        chat_id = str(u.get("chat_id"))
        if not chat_id or chat_id in seen:
            continue
        seen.add(chat_id)
        norm.append({
            "chat_id": chat_id,
            "name": u.get("name") or "",
            "enabled": bool(u.get("enabled", True)),
            "is_admin": bool(u.get("is_admin", False)),
        })
    return norm

def save_users(users):
    by_id = {}
    for u in users:
        by_id[str(u["chat_id"])] = {
            "chat_id": str(u["chat_id"]),
            "name": u.get("name",""),
            "enabled": bool(u.get("enabled", True)),
            "is_admin": bool(u.get("is_admin", False)),
        }
    save_json(USERS_JSON, list(by_id.values()))

def find_user(users, chat_id):
    sid = str(chat_id)
    for u in users:
        if u["chat_id"] == sid:
            return u
    return None

def user_exists(users, chat_id):
    return find_user(users, chat_id) is not None

def get_admins(users):
    return [u for u in users if u.get("enabled", True) and u.get("is_admin", False)]

# ---------- Telegram ----------
def tg_get(url, params=None):
    r = requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data

def tg_post(url, data=None):
    r = requests.post(url, data=data or {}, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data

def send_message(chat_id, text, parse_mode="HTML", disable_preview=True):
    base = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_preview,
    }
    try:
        tg_post(base, payload)
        return True, None
    except Exception as e:
        return False, str(e)

def notify_admins(users, text):
    for a in get_admins(users):
        send_message(a["chat_id"], text)

# ---------- getUpdates state ----------
def load_last_update_id():
    data = load_json(STATE_FILE, {})
    return data.get("last_update_id")

def save_last_update_id(uid):
    save_json(STATE_FILE, {"last_update_id": uid})

# ---------- Alta/Baja desde updates ----------
STOP_KEYWORDS = ("/stop", "stop", "baja", "unsubscribe")

def drain_updates_and_collect(users):
    """
    Procesa updates y devuelve:
      - truly_new: altas NUEVAS (solo si envían /start y no existían)
      - deactivated_ids: chat_ids dados de baja (/stop o expulsión)
      - reactivated_ids: chat_ids reactivados (enviaron /start teniendo enabled:false)
    Reglas:
      - /start -> alta o reactivación
      - /stop -> baja
      - /help -> mostramos ayuda
      - cualquier otro texto: mostramos ayuda SOLO en chats privados; NO da de alta.
    """
    base = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getUpdates"
    params = {
        "timeout": 0,
        "limit": 100,
        "allowed_updates": json.dumps(["message", "my_chat_member"])
    }
    last = load_last_update_id()
    if last is not None:
        params["offset"] = last + 1

    data = tg_get(base, params)
    max_update_id = last
    new_users_map = {}    # chat_id -> dict (posibles ALTAS nuevas)
    deactivated_ids = set()
    reactivated_ids = set()

    for upd in data.get("result", []):
        uid = upd["update_id"]
        max_update_id = max(uid, max_update_id or uid)

        # Mensajes
        if "message" in upd and "chat" in upd["message"]:
            msg = upd["message"]
            chat = msg["chat"]
            chat_type = chat.get("type", "private")  # "private", "group", "supergroup", "channel"
            chat_id = str(chat["id"])
            name = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
            text = (msg.get("text") or "").strip()

            lower = text.lower()

            if lower.startswith("/start"):
                u = find_user(users, chat_id)
                if u:
                    # ya existe: si estaba de baja, reactivar
                    if not u.get("enabled", True):
                        u["enabled"] = True
                        reactivated_ids.add(chat_id)
                else:
                    # alta nueva
                    new_users_map[chat_id] = {"chat_id": chat_id, "name": name, "enabled": True, "is_admin": False}

            elif any(lower.startswith(k) for k in STOP_KEYWORDS):
                u = find_user(users, chat_id)
                if u and u.get("enabled", True):
                    u["enabled"] = False
                    deactivated_ids.add(chat_id)

            elif lower.startswith("/help"):
                # responder ayuda (no tocamos alta/baja)
                send_message(chat_id, HELP_TEXT)

            else:
                # Texto desconocido: respondemos ayuda SOLO en privados (no spamear grupos)
                if chat_type == "private":
                    send_message(chat_id, HELP_TEXT)

        # Cambios de estado (añadido/expulsado de grupos)
        if "my_chat_member" in upd:
            chat = upd["my_chat_member"]["chat"]
            status = upd["my_chat_member"]["new_chat_member"]["status"]
            chat_id = str(chat["id"])
            name = chat.get("title") or ""
            if status in ("administrator", "member"):
                # Para grupos, exigimos /start para alta; no auto-alta por estar dentro
                pass
            else:
                # left/kicked/restricted: desactivar si existe
                u = find_user(users, chat_id)
                if u and u.get("enabled", True):
                    u["enabled"] = False
                    deactivated_ids.add(chat_id)

    if max_update_id is not None:
        save_last_update_id(max_update_id)

    # Solo ALTAS nuevas auténticas (que no existían antes)
    truly_new = [u for cid, u in new_users_map.items() if not user_exists(users, cid)]
    return truly_new, list(deactivated_ids), list(reactivated_ids)


# ---------- Calendario ----------
def fetch_calendar():
    resp = requests.get(ICS_URL, timeout=30); resp.raise_for_status()
    return Calendar(resp.text)

def fetch_events_today_and_tomorrow(cal=None):
    cal = cal or fetch_calendar()
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)
    ev_today, ev_tomorrow = [], []
    for ev in cal.events:
        try:
            ev_dt = ev.begin.to(TZ_STR).datetime
        except Exception:
            ev_dt = ev.begin.datetime
        d = ev_dt.date()
        title = (ev.name or "Partido del Betis").strip()
        place = (ev.location or "").strip()
        hora = ev_dt.strftime("%H:%M")
        item = (title, hora, place)
        if d == today:
            ev_today.append(item)
        elif d == tomorrow:
            ev_tomorrow.append(item)
    return ev_today, ev_tomorrow

def fetch_next_event(cal=None):
    """Devuelve el próximo evento a partir de ahora (title, fecha, hora, lugar) o None."""
    now = datetime.now(tz)
    cal = cal or fetch_calendar()
    next_ev = None
    for ev in cal.events:
        try:
            ev_dt = ev.begin.to(TZ_STR).datetime
        except Exception:
            ev_dt = ev.begin.datetime
        if ev_dt >= now and (next_ev is None or ev_dt < next_ev[1]):
            title = (ev.name or "Partido del Betis").strip()
            place = (ev.location or "").strip()
            next_ev = (title, ev_dt, place)
    if not next_ev:
        return None
    title, dtobj, place = next_ev
    return {
        "title": title,
        "date": dtobj.strftime("%Y-%m-%d"),
        "time": dtobj.strftime("%H:%M"),
        "place": place
    }


def build_run_ping(next_ev=None):
    now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"{RUN_PING_TITLE}", f"Fecha/Hora: {now_str} ({TZ_STR})"]
    if DEBUG_NOTIFY_NEXT:
        if next_ev:
            place = f" ({next_ev['place']})" if next_ev.get("place") else ""
            lines.append("Próximo partido (DEBUG):")
            lines.append(f"• {next_ev['title']} — {next_ev['date']} {next_ev['time']}{place}")
        else:
            lines.append("Próximo partido (DEBUG): no encontrado en el feed.")
    return "\n".join(lines)

def build_msg_today(events):
    lines = [HEADER_TODAY, "", SUBHEADER_TODAY]  # línea en blanco tras cabecera
    for title, hora, place in sorted(events, key=lambda x: x[1]):
        place_txt = f" ({place})" if place else ""
        lines.append(f"• <b>{title}</b> — {hora}{place_txt}")
    lines.append("")
    lines.append(FOOTER_TODAY)
    return "\n".join(lines)

def build_msg_tomorrow(events):
    lines = [HEADER_TOMORROW, ""]
    for title, hora, place in sorted(events, key=lambda x: x[1]):
        place_txt = f" ({place})" if place else ""
        lines.append(f"• <b>{title}</b> — {hora}{place_txt}")
    return "\n".join(lines)

#esta era para si el gif es una url

# def send_gif(chat_id, gif_url, caption=None):
#     base = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendAnimation"
#     payload = {
#         "chat_id": chat_id,
#         "animation": gif_url,
#     }
#     if caption:
#         payload["caption"] = caption
#         payload["parse_mode"] = "HTML"
#     try:
#         tg_post(base, payload)
#         return True, None
#     except Exception as e:
#         return False, str(e)


def send_gif(chat_id, src, caption=None):
    """
    src puede ser:
      - URL http(s)://
      - ruta local a un .gif/.mp4 (se sube por multipart)
    """
    base = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendAnimation"

    # Caso URL
    if src.startswith("http://") or src.startswith("https://"):
        payload = {"chat_id": chat_id, "animation": src}
        if caption:
            payload["caption"] = caption
            payload["parse_mode"] = "HTML"
        try:
            r = requests.post(base, data=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(data)
            return True, None
        except Exception as e:
            return False, str(e)

    # Caso fichero local
    if not os.path.isfile(src):
        return False, f"Archivo no encontrado: {src}"

    mime = mimetypes.guess_type(src)[0] or "application/octet-stream"
    try:
        with open(src, "rb") as fh:
            files = {"animation": (os.path.basename(src), fh, mime)}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "HTML"
            r = requests.post(base, data=data, files=files, timeout=60)
            r.raise_for_status()
            resp = r.json()
            if not resp.get("ok"):
                raise RuntimeError(resp)
        return True, None
    except Exception as e:
        return False, str(e)



# ---------- Main ----------
def main():
    # LOG de esta ejecución
    log_file = new_log_file()
    write_log(log_file, "==== EJECUCIÓN BOT BETIS ====")
    write_log(log_file, "Inicio:", datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z"))

    # 1) Carga usuarios
    users = load_users()
    enabled_count = len([u for u in users if u.get("enabled", True)])
    admins = [ (u["chat_id"], u.get("name","")) for u in get_admins(users) ]
    write_log(log_file, f"Usuarios cargados: {len(users)} | habilitados: {enabled_count}")
    write_log(log_file, f"Admins: {admins if admins else '—'}")

    # 1.1) Ping a admins (ejecución + próximo partido si DEBUG)
    try:
        cal = fetch_calendar()
        next_ev = fetch_next_event(cal)
    except Exception as e:
        cal, next_ev = None, None
        write_log(log_file, "[ERROR] fetch_calendar/fetch_next_event:", e)
    notify_admins(users, build_run_ping(next_ev))

    # 2) Drena updates: altas, bajas, reactivaciones
    try:
        new_users, deactivated_ids, reactivated_ids = drain_updates_and_collect(users)
    except Exception as e:
        write_log(log_file, "[ERROR] drain_updates_and_collect:", e)
        new_users, deactivated_ids, reactivated_ids = [], [], []

    if new_users:
        write_log(log_file, f"Nuevas altas recibidas: {len(new_users)} ->", [(u['chat_id'], u.get('name','')) for u in new_users])
    if deactivated_ids:
        write_log(log_file, f"Bajas solicitadas: {len(deactivated_ids)} ->", deactivated_ids)
    if reactivated_ids:
        write_log(log_file, f"Reactivaciones: {len(reactivated_ids)} ->", reactivated_ids)

    # 2.1) Aplica bajas y confirma
    if deactivated_ids:
        save_users(users)  # guardamos antes de notificar
        for cid in deactivated_ids:
            try:
                if not str(cid).startswith("-"):
                    ok, err = send_message(cid, GOODBYE_TEXT)
                    write_log(log_file, "[BAJA OK]" if ok else "[BAJA FAIL]", cid, err or "")
            except Exception as e:
                write_log(log_file, "[BAJA EXC]", cid, e)

    # 2.2) Reactivaciones
    if reactivated_ids:
        save_users(users)
        for cid in reactivated_ids:
            try:
                if not str(cid).startswith("-"):
                    ok, err = send_message(cid, WELCOME_BACK_TEXT)
                    write_log(log_file, "[REACT OK]" if ok else "[REACT FAIL]", cid, err or "")
            except Exception as e:
                write_log(log_file, "[REACT EXC]", cid, e)

    # 2.3) Altas masivas vs normales
    if new_users:
        if len(new_users) > MAX_NEW_PER_RUN:
            pending = load_json(PENDING_JSON, [])
            timestamp = datetime.now(tz).isoformat()
            for u in new_users:
                u["received_at"] = timestamp
            pending.extend(new_users)
            save_json(PENDING_JSON, pending)
            notify_admins(users, f"{ADMIN_ALERT_TITLE}\n\n{ADMIN_ALERT_BODY.format(n=len(new_users))}")
            write_log(log_file, "[ALTAS BLOQUEADAS] Pasadas a pending_review.json:", len(new_users))
        else:
            users.extend(new_users)
            save_users(users)
            for u in new_users:
                ok, err = send_message(u["chat_id"], WELCOME_TEXT)
                write_log(log_file, "[WELCOME OK]" if ok else "[WELCOME FAIL]", u["chat_id"], u.get("name",""), err or "")

    # 3) Eventos: hoy y mañana
    try:
        ev_today, ev_tomorrow = fetch_events_today_and_tomorrow(cal)
    except Exception as e:
        ev_today, ev_tomorrow = [], []
        write_log(log_file, "[ERROR] fetch_events_today_and_tomorrow:", e)

    if ev_today:
        write_log(log_file, "Eventos HOY:", ev_today)
    if ev_tomorrow:
        write_log(log_file, "Eventos MAÑANA:", ev_tomorrow)

    if not ev_today and not ev_tomorrow:
        write_log(log_file, "No hay partidos hoy ni mañana. No se envían avisos.")
        write_log(log_file, "==== FIN EJECUCIÓN ====")
        return

    enabled = [u for u in users if u.get("enabled", True)]
    write_log(log_file, f"Usuarios a notificar: {len(enabled)}")

    failures = []

    # --- Partidos HOY: texto + GIF ---
    if ev_today:
        msg_today = build_msg_today(ev_today)
        write_log(log_file, "--- MENSAJE HOY ---\n", msg_today, "\n--- FIN MENSAJE HOY ---")
        for u in enabled:
            ok, err = send_message(u["chat_id"], msg_today)
            if ok:
                write_log(log_file, "[SEND TODAY OK]", u["chat_id"], u.get("name",""))
            else:
                failures.append({"chat_id": u["chat_id"], "name": u.get("name",""), "error": err})
                write_log(log_file, "[SEND TODAY FAIL]", u["chat_id"], u.get("name",""), err or "")
            time.sleep(0.1)

        # GIF motivacional (si está configurado)
        if BETIS_GIF:
            for u in enabled:
                ok, err = send_gif(u["chat_id"], BETIS_GIF, caption="💚🤍 ¡Arriba ese Betis! 🤍💚")
                if ok:
                    write_log(log_file, "[SEND GIF OK]", u["chat_id"], u.get("name",""))
                else:
                    failures.append({"chat_id": u["chat_id"], "name": u.get("name",""), "error": err})
                    write_log(log_file, "[SEND GIF FAIL]", u["chat_id"], u.get("name",""), err or "")
                time.sleep(0.1)
        else:
            write_log(log_file, "[GIF OMITIDO] BETIS_GIF vacío")

    # --- Partidos MAÑANA: solo texto ---
    if ev_tomorrow:
        msg_tomorrow = build_msg_tomorrow(ev_tomorrow)
        write_log(log_file, "--- MENSAJE MAÑANA ---\n", msg_tomorrow, "\n--- FIN MENSAJE MAÑANA ---")
        for u in enabled:
            ok, err = send_message(u["chat_id"], msg_tomorrow)
            if ok:
                write_log(log_file, "[SEND TOMORROW OK]", u["chat_id"], u.get("name",""))
            else:
                failures.append({"chat_id": u["chat_id"], "name": u.get("name",""), "error": err})
                write_log(log_file, "[SEND TOMORROW FAIL]", u["chat_id"], u.get("name",""), err or "")
            time.sleep(0.1)

    # 4) Resumen + reporte a admins
    ok_count = (len(enabled) * (1 if ev_tomorrow else 0) + len(enabled) * (1 if ev_today else 0)) - len(failures)
    write_log(log_file, f"Fallos totales: {len(failures)}")
    write_log(log_file, "==== FIN EJECUCIÓN ====")

    if failures:
        lines = [f"{SEND_ERRORS_TITLE}", f"Total fallos: {len(failures)}"]
        for f in failures[:10]:
            lines.append(f"• {f['chat_id']} {('('+f['name']+')') if f['name'] else ''} — {f['error'][:120]}")
        if len(failures) > 10:
            lines.append(f"... y {len(failures)-10} más.")
        notify_admins(users, "\n".join(lines))


if __name__ == "__main__":
    main()
