"""
notify.py — Preferencias de notificación por email
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date
from flask import Blueprint, request, g
from api.config import (
    ok, err, body, db_fetchone, db_exec, db_update,
    SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_PORT, APP_URL,
    NOTIFY_SECRET, require_auth
)

notify_bp = Blueprint("notify", __name__)


def send_reminder_email(to_email: str, due_count: int, is_test: bool) -> bool | str:
    subject = (
        "✉ Vocab — Email de prueba"
        if is_test
        else f"📚 Vocab — Tienes {due_count} palabra{'s' if due_count > 1 else ''} para repasar hoy"
    )
    body_html = build_email_html(due_count, is_test, APP_URL)
    plain = (
        f"Este es un email de prueba de Vocab.\n\nTu app: {APP_URL}"
        if is_test
        else f"Tienes {due_count} palabra{'s' if due_count > 1 else ''} pendiente{'s' if due_count > 1 else ''} de repasar hoy.\n\nAbrir app: {APP_URL}"
    )
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Lexlo <{SMTP_FROM}>"
        msg["To"] = to_email
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        return True
    except Exception as e:
        return str(e)


def build_email_html(due_count: int, is_test: bool, app_url: str) -> str:
    headline = "Email de prueba ✉" if is_test else f"Tienes {due_count} palabra{'s' if due_count > 1 else ''} para repasar"
    sub = "Las notificaciones de Vocab están funcionando correctamente." if is_test else "Tu sesión de repaso de hoy te está esperando. Solo toma unos minutos."
    count_html = "" if is_test else f"<div style='font-size:52px;font-weight:800;color:#d4f04a;line-height:1;margin-bottom:8px;'>{due_count}</div>"
    return f"""<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body style='margin:0;padding:0;background:#0c0c0c;font-family:sans-serif;'>
<table width='100%' cellpadding='0' cellspacing='0'><tr><td align='center' style='padding:40px 20px;'>
<table width='480' cellpadding='0' cellspacing='0' style='background:#161616;border-radius:16px;border:1px solid #252525;overflow:hidden;'>
  <tr><td style='padding:32px 36px;text-align:center;border-bottom:1px solid #252525;'>
    <div style='font-size:22px;font-weight:800;color:#eeebe2;letter-spacing:-.03em;'>vocab<span style='color:#d4f04a;'>.</span></div>
  </td></tr>
  <tr><td style='padding:36px;text-align:center;'>
    {count_html}
    <div style='font-size:20px;font-weight:700;color:#eeebe2;margin-bottom:10px;'>{headline}</div>
    <div style='font-size:14px;color:#555;margin-bottom:28px;'>{sub}</div>
    <a href='{app_url}' style='display:inline-block;background:#d4f04a;color:#0c0c0c;font-weight:800;font-size:14px;padding:14px 32px;border-radius:8px;text-decoration:none;letter-spacing:.02em;'>Abrir Vocab →</a>
  </td></tr>
  <tr><td style='padding:20px 36px;text-align:center;border-top:1px solid #252525;'>
    <div style='font-size:11px;color:#333;'>Para desactivar estas notificaciones, ve a Progreso → Notificaciones en la app.</div>
  </td></tr>
</table></td></tr></table></body></html>"""


@notify_bp.route("/notify", methods=["GET", "POST", "OPTIONS"])
@require_auth
def notify():
    if request.method == "OPTIONS":
        return "", 204

    uid = g.uid
    method = request.method
    action = request.args.get("action", "")

    # GET ?action=prefs
    if method == "GET" and action == "prefs":
        row = db_fetchone(
            "SELECT email, enabled, notify_hour FROM notification_prefs WHERE user_id = %s", (uid,)
        )
        return ok(row or {"email": "", "enabled": False, "notify_hour": 8})

    # POST ?action=save
    if method == "POST" and action == "save":
        import re
        b = body()
        email = b.get("email", "").strip()
        enabled = int(bool(b.get("enabled", False)))
        hour = max(0, min(23, int(b.get("notify_hour", 8))))

        if enabled and not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            return err("Email inválido")

        db_exec(
            """INSERT INTO notification_prefs (user_id, email, enabled, notify_hour)
               VALUES (%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE email=%s, enabled=%s, notify_hour=%s""",
            (uid, email, enabled, hour, email, enabled, hour),
        )
        return ok({"saved": True})

    # POST ?action=test
    if method == "POST" and action == "test":
        import re
        b = body()
        email = b.get("email", "").strip()
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            return err("Email inválido")

        due_row = db_fetchone(
            "SELECT COUNT(*) AS cnt FROM word_srs WHERE user_id = %s AND next_review <= CURRENT_DATE AND mastered = 0",
            (uid,),
        )
        due_count = int(due_row["cnt"]) if due_row else 0
        result = send_reminder_email(email, due_count, True)
        if result is True:
            return ok({"sent": True})
        return err(f"No se pudo enviar: {result}")

    return err("Acción no válida")


# Endpoint sin auth para cron
@notify_bp.route("/notify/send_due", methods=["GET"])
def send_due():
    secret = request.args.get("secret", "")
    if secret != NOTIFY_SECRET:
        return "", 403

    from api.config import db_fetchall, db_update
    hour = date.today().timetuple().tm_hour

    from api.config import get_db
    # Need to init g.db without require_auth
    from flask import g
    if not hasattr(g, "db"):
        from api.config import get_db
        get_db()

    rows = db_fetchall(
        """SELECT np.user_id, np.email, np.notify_hour, COUNT(s.group_id) AS due_count
           FROM notification_prefs np
           JOIN word_srs s ON s.user_id = np.user_id
           WHERE np.enabled = 1 AND np.email != ''
             AND (np.last_sent IS NULL OR np.last_sent < CURRENT_DATE)
             AND np.notify_hour = %s
             AND s.next_review <= CURRENT_DATE AND s.mastered = 0
           GROUP BY np.user_id, np.email, np.notify_hour
           HAVING due_count > 0""",
        (hour,),
    )

    sent = 0
    errors = []
    for r in rows:
        result = send_reminder_email(r["email"], int(r["due_count"]), False)
        if result is True:
            db_update(
                "UPDATE notification_prefs SET last_sent = CURRENT_DATE WHERE user_id = %s",
                (r["user_id"],),
            )
            sent += 1
        else:
            errors.append(f"{r['email']}: {result}")

    return ok({"sent": sent, "errors": errors})
