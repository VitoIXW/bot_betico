# Bot Betis Notifier

Bot de Telegram en Python que notifica partidos del Betis (ICS) **el día antes** y **el mismo día** (mensaje especial + GIF opcional). Incluye:

* Altas (`/start`), bajas (`/stop`) y ayuda (`/help`).
* Logs por ejecución en `logs/` con timestamp.
* Gestión de altas masivas con revisión manual (`data/pending_review.json`).
* Admins definidos solo en `data/users.json`.

---

## Requisitos

* **Python 3.10+** (probado en Ubuntu).
* Bot de Telegram (token de **@BotFather**).

---

## Estructura del repo

```
.
├─ bot_betis_notifier.py
├─ users.json.example
├─ config/
│  ├─ .env                  # no versionado (lo crea setup.sh si no existe)
│  └─ requirements.txt
├─ data/
│  ├─ users.json            # no versionado (copiado desde .example)
│  ├─ pending_review.json   # autogenerado si hace falta
│  └─ last_update_id.json   # autogenerado
├─ media/
│  └─ betis.gif             # opcional
└─ logs/
   └─ run_YYYY-MM-DD_HH-MM-SS.log
```

---

## Instalación rápida (con `setup.sh`)

```bash
# 1) Clona y entra al repo
git clone https://github.com/VitoIXW/bot_betico.git
cd bot_betico

# (opcional) cambiar a rama develop
# git checkout develop

# 2) Ejecuta el instalador
chmod +x setup.sh
./setup.sh
```

El script:

* Crea `config/`, `data/`, `media/`, `logs/`.
* Copia `users.json.example` → `data/users.json` (si no existe).
* Crea y activa `.venv/` e instala `config/requirements.txt`.
* Genera `config/.env` de ejemplo si no existe.

> Después de ejecutar `setup.sh`, edita **`config/.env`** y **`data/users.json`** con tu token/IDs.

---

## Configuración manual (alternativa a `setup.sh`)

1. Entorno virtual + dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r config/requirements.txt
```

2. Archivos y configuración

```bash
mkdir -p config data media logs
cp users.json.example data/users.json
```

3. Crea `config/.env` con:

```dotenv
TG_BOT_TOKEN=123456:ABCDEF_tu_token
ICS_URL=https://.../real-betis.ics
TZ=Europe/Madrid
USERS_JSON=data/users.json
PENDING_JSON=data/pending_review.json
UPDATES_STATE=data/last_update_id.json
LOG_DIR=logs
MAX_NEW_PER_RUN=30
DEBUG_NOTIFY_NEXT=0
BETIS_GIF=media/betis.gif
```

4. Define tu usuario admin en `data/users.json`:

```json
[
  {
    "chat_id": "470774810",
    "name": "Admin",
    "enabled": true,
    "is_admin": true
  }
]
```

---

## Uso

```bash
source .venv/bin/activate
python bot_betis_notifier.py
```

* Si **hoy** hay partido → mensaje “DÍA DE BETIS” + GIF (si `BETIS_GIF` está configurado).
* Si **mañana** hay partido → recordatorio.
* Los admins reciben ping de ejecución; con `DEBUG_NOTIFY_NEXT=1` verán también el próximo partido.
* Cada ejecución crea un log: `logs/run_YYYY-MM-DD_HH-MM-SS.log`.

### Comandos de usuario

* `/start` — alta/reativación.
* `/stop` — baja.
* `/help` — ayuda.

> En **privados**, cualquier mensaje desconocido devuelve la ayuda; en **grupos** no se auto-alta ni se responde al ruido.

---

## Programar ejecución diaria

### Cron (simple)

Ejecutar cada día a las **07:00** (hora del sistema):

```cron
0 7 * * * /usr/bin/env bash -lc 'cd /RUTA/ABSOLUTA/bot_betis && source .venv/bin/activate && python bot_betis_notifier.py'
```

> No redirijas a un log adicional: el bot ya guarda un log por ejecución en `logs/`.

### systemd timer (opcional, más robusto)

`/etc/systemd/system/betis.service`

```
[Unit]
Description=Bot avisos Betis

[Service]
WorkingDirectory=/RUTA/ABSOLUTA/bot_betis
Environment=PYTHONUNBUFFERED=1
ExecStart=/RUTA/ABSOLUTA/bot_betis/.venv/bin/python /RUTA/ABSOLUTA/bot_betis/bot_betis_notifier.py
Restart=on-failure
```

`/etc/systemd/system/betis.timer`

```
[Unit]
Description=Ejecuta bot Betis a diario 07:00 Europe/Madrid

[Timer]
OnCalendar=Europe/Madrid *-*-* 07:00:00
Persistent=true
Unit=betis.service

[Install]
WantedBy=timers.target
```

Activar:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now betis.timer
sudo systemctl list-timers | grep betis
```

---

## Notas importantes

* El feed **ICS** debe ser el del equipo que quieres (masculino/femenino).
* `update_id` de Telegram es creciente; el bot guarda el último en `data/last_update_id.json`.
* Si borras `data/last_update_id.json`, Telegram puede reenviar updates pendientes (\~24h) y se reprocesarán.
* **Admins**: solo editando `data/users.json`.
* **Altas masivas** (> `MAX_NEW_PER_RUN`): se guardan en `data/pending_review.json` y se avisa a admins.
* Para GIF: usa una **URL directa** al archivo o pon uno local en `media/betis.gif`.
