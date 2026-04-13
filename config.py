"""
config.py — Configuración + helpers globales
"""
import os
import json
import secrets
import functools
from datetime import datetime, timedelta
from typing import Any

import pymysql
import pymysql.cursors
from flask import request, jsonify, g
from dotenv import load_dotenv

load_dotenv()

# ── Variables de entorno ─────────────────────────────────────────────────────
def env(key: str, default=None):
    value = os.environ.get(key)
    if value is None:
        if default is not None:
            return default
        raise EnvironmentError(f"Falta variable de entorno: {key}")
    return value

DB_HOST   = env("DB_HOST")
DB_NAME   = env("DB_NAME")
DB_USER   = env("DB_USER")
DB_PASS   = env("DB_PASS")
DB_PORT   = int(env("DB_PORT", "3306"))

GROQ_KEY   = env("GROQ_API_KEY")
GROQ_MODEL = env("GROQ_MODEL", "llama-3.3-70b-versatile")

SMTP_HOST = env("SMTP_HOST", "smtp.gmail.com")
SMTP_USER = env("SMTP_USER", "")
SMTP_PASS = env("SMTP_PASS", "")
SMTP_FROM = env("SMTP_FROM", "")
SMTP_PORT = int(env("SMTP_PORT", "587"))

APP_URL       = env("APP_URL")
NOTIFY_SECRET = env("NOTIFY_SECRET")

# ── DB ───────────────────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def db_exec(sql: str, params=None):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur


def db_fetchall(sql: str, params=None):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def db_fetchone(sql: str, params=None):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


def db_insert(sql: str, params=None):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.lastrowid


def db_update(sql: str, params=None):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.rowcount


# ── Helpers de respuesta ─────────────────────────────────────────────────────
def ok(data: Any):
    return jsonify({"ok": True, "data": data})


def err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


# ── Body parser ──────────────────────────────────────────────────────────────
def body() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    if request.form:
        return request.form.to_dict()
    raw = request.get_data(as_text=True)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


# ── Autenticación ────────────────────────────────────────────────────────────
def get_uid() -> int:
    """Obtiene el user_id del request actual. Lanza 401 si no autenticado."""
    # Header X-Session-Token
    token = (
        request.args.get("_t", "")
        or request.headers.get("X-Session-Token", "")
        or request.cookies.get("lexlo_token", "")
    )
    if token:
        row = db_fetchone(
            "SELECT user_id FROM session_tokens WHERE token = %s AND expires_at > NOW()",
            (token,),
        )
        if row:
            return int(row["user_id"])
    return None


def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        uid = get_uid()
        if uid is None:
            return err("No autenticado", 401)
        g.uid = uid
        return f(*args, **kwargs)
    return decorated


# ── Token de sesión ──────────────────────────────────────────────────────────
def make_token(uid: int) -> str:
    token = secrets.token_hex(32)
    expires = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    db_insert(
        "INSERT INTO session_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
        (uid, token, expires),
    )
    return token


# ── Groq helpers ─────────────────────────────────────────────────────────────
import requests as _requests
import re


def groq_call(prompt: str, max_tokens: int = 120) -> str | None:
    try:
        resp = _requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.1,
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_KEY}",
            },
            timeout=10,
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def parse_groq_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    clean = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(clean)
    except Exception:
        return None
