#!/usr/bin/env python3
import os, sys, json, time, requests
from datetime import datetime, timedelta
import pytz
from ics import Calendar
from dotenv import load_dotenv

# Cargar variables desde .env
load_dotenv()

# ============ CONFIG ============
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")  # requerido
ICS_URL      = os.getenv("ICS_URL")       # requerido
TZ_STR       = os.getenv("TZ", "Europe/Madrid")

USERS_JSON    = os.getenv("USERS_JSON", "users.json")
PENDING_JSON  = os.getenv("PENDING_JSON", "pending_review.json")
STATE_FILE    = os.getenv("UPDATES_STATE", "last_update_id.json")
MAX_NEW_PER_RUN = int(os.getenv("MAX_NEW_PER_RUN", "30"))
DEBUG_NOTIFY_NEXT = os.getenv("DEBUG_NOTIFY_NEXT", "0") in ("1", "true", "True", "YES", "yes")

WELCOME_TEXT = "¡Apuntado para recibir avisos del Betis! ⚽️"
GOODBYE_TEXT = "Has sido dado de baja. Si quieres volver a apuntarte, envíame cualquier mensaje."
ADMIN_ALERT_TITLE = "⚠️ Alta masiva pendiente de revisión"
ADMIN_ALERT_BODY  = "Se han detectado {n} nuevas altas. Están en pending_review.json. No se añadieron automáticamente."
SEND_ERRORS_TITLE = "⚠️ Incidencias en el envío de recordatorios"
RUN_PING_TITLE    = "🟢 Ejecución realizada"
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
    Devuelve:
      - truly_new: usuarios nuevos para alta (no existentes aún)
      - deactivated_ids: chat_ids dados de baja (/stop o expulsión)
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
    new_users_map = {}
    deactivated_ids = set()

    for upd in data.get("result", []):
        uid = upd["update_id"]
        max_update_id = max(uid, max_update_id or uid)

        # Mensajes
        if "message" in upd and "chat" in upd["message"]:
            msg = upd["message"]
            chat = msg["chat"]
            chat_id = str(chat["id"])
            name = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
            text = (msg.get("text") or "").strip().lower()

            if any(text.startswith(k) for k in STOP_KEYWORDS):
                u = find_user(users, chat_id)
                if u and u.get("enabled", True):
                    u["enabled"] = False
                    deactivated_ids.add(chat_id)
            else:
                new_users_map[chat_id] = {"chat_id": chat_id, "name": name, "enabled": True, "is_admin": False}

        # Cambios de estado (añadido/expulsado de grupos)
        if "my_chat_member" in upd:
            chat = upd["my_chat_member"]["chat"]
            status = upd["my_chat_member"]["new_chat_member"]["status"]
            chat_id = str(chat["id"])
            name = chat.get("title") or ""
            if status in ("administrator", "member"):
                new_users_map[chat_id] = {"chat_id": chat_id, "name": name, "enabled": True, "is_admin": False}
            else:
                u = find_user(users, chat_id)
                if u and u.get("enabled", True):
                    u["enabled"] = False
                    deactivated_ids.add(chat_id)

    if max_update_id is not None:
        save_last_update_id(max_update_id)

    truly_new = [u for cid, u in new_users_map.items() if not user_exists(users, cid)]
    return truly_new, list(deactivated_ids)

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

def build_msg(header, events):
    lines = [f"⚽️ <b>{header}</b>"]
    for title, hora, place in sorted(events, key=lambda x: x[1]):
        lines.append(f"• {title} — {hora}" + (f" ({place})" if place else ""))
    return "\n".join(lines)

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

# ---------- Main ----------
def main():
    # 1) Carga usuarios
    users = load_users()

    # 1.1) Ping a admins (ejecución +, si procede, próximo partido)
    try:
        cal = fetch_calendar()
        next_ev = fetch_next_event(cal)
    except Exception:
        cal, next_ev = None, None
    notify_admins(users, build_run_ping(next_ev))

    # 2) Drena updates: altas y bajas
    new_users, deactivated_ids = drain_updates_and_collect(users)

    # 2.1) Aplica bajas y confirma (privados)
    if deactivated_ids:
        save_users(users)
        for cid in deactivated_ids:
            try:
                if not str(cid).startswith("-"):
                    send_message(cid, GOODBYE_TEXT)
            except Exception:
                pass

    # 2.2) Altas masivas vs normales
    if new_users:
        if len(new_users) > MAX_NEW_PER_RUN:
            pending = load_json(PENDING_JSON, [])
            timestamp = datetime.now(tz).isoformat()
            for u in new_users:
                u["received_at"] = timestamp
            pending.extend(new_users)
            save_json(PENDING_JSON, pending)
            notify_admins(users, f"{ADMIN_ALERT_TITLE}\n\n{ADMIN_ALERT_BODY.format(n=len(new_users))}")
        else:
            users.extend(new_users)
            save_users(users)
            for u in new_users:
                send_message(u["chat_id"], WELCOME_TEXT)

    # 3) Eventos: hoy y mañana
    try:
        ev_today, ev_tomorrow = fetch_events_today_and_tomorrow(cal)
    except Exception:
        ev_today, ev_tomorrow = [], []

    msgs = []
    if ev_today:
        msgs.append(build_msg("¡Juega el Betis HOY!", ev_today))
    if ev_tomorrow:
        msgs.append(build_msg("Recordatorio: mañana hay partido", ev_tomorrow))

    if not msgs:
        print("No hay partidos hoy ni mañana. No se envían avisos.")
        return

    # 4) Envío y reporte de fallos
    enabled = [u for u in users if u.get("enabled", True)]
    print(f"Usuarios habilitados: {len(enabled)} | Mensajes: {len(msgs)}")

    failures = []
    for u in enabled:
        for m in msgs:
            ok, err = send_message(u["chat_id"], m)
            if not ok:
                failures.append({"chat_id": u["chat_id"], "name": u.get("name",""), "error": err})
            time.sleep(0.1)

    if failures:
        lines = [f"{SEND_ERRORS_TITLE}", f"Total fallos: {len(failures)}"]
        for f in failures[:10]:
            lines.append(f"• {f['chat_id']} {('('+f['name']+')') if f['name'] else ''} — {f['error'][:120]}")
        if len(failures) > 10:
            lines.append(f"... y {len(failures)-10} más.")
        notify_admins(users, "\n".join(lines))

if __name__ == "__main__":
    main()
