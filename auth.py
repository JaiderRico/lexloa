"""
auth.py — Registro · Login · Logout · Me (PostgreSQL)
"""
import re
from flask import Blueprint, request, g
from config import (
    ok, err, body, db_exec, db_fetchone, db_insert,
    make_token, get_uid, get_db
)
import bcrypt

auth_bp = Blueprint("auth", __name__)


def create_tables():
    tables = [
        """CREATE TABLE IF NOT EXISTS users (
            id         SERIAL PRIMARY KEY,
            username   VARCHAR(30)  NOT NULL UNIQUE,
            email      VARCHAR(120),
            password   VARCHAR(255) NOT NULL,
            created_at TIMESTAMP    NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS session_tokens (
            id         SERIAL PRIMARY KEY,
            user_id    INT         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token      VARCHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMP   NOT NULL,
            created_at TIMESTAMP   NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS word_groups (
            id               SERIAL PRIMARY KEY,
            user_id          INT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            spanish          VARCHAR(120) NOT NULL,
            example_sentence TEXT,
            category         VARCHAR(80)  NOT NULL DEFAULT '',
            created_at       DATE         NOT NULL DEFAULT CURRENT_DATE
        )""",
        """CREATE TABLE IF NOT EXISTS words (
            id       SERIAL PRIMARY KEY,
            group_id INT          NOT NULL REFERENCES word_groups(id) ON DELETE CASCADE,
            english  VARCHAR(120) NOT NULL,
            is_hard  BOOLEAN      NOT NULL DEFAULT FALSE
        )""",
        """CREATE TABLE IF NOT EXISTS practice_log (
            id            SERIAL PRIMARY KEY,
            user_id       INT         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            group_id      INT         NOT NULL REFERENCES word_groups(id) ON DELETE CASCADE,
            direction     VARCHAR(10) NOT NULL,
            practice_mode VARCHAR(20),
            answer        TEXT        NOT NULL,
            correct       BOOLEAN     NOT NULL DEFAULT FALSE,
            feedback      TEXT,
            created_at    TIMESTAMP   NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS weekly_tests (
            id         SERIAL PRIMARY KEY,
            user_id    INT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            week_start DATE NOT NULL,
            score      INT  NOT NULL DEFAULT 0,
            total      INT  NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS word_srs (
            id          SERIAL PRIMARY KEY,
            user_id     INT   NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            group_id    INT   NOT NULL REFERENCES word_groups(id) ON DELETE CASCADE,
            easiness    FLOAT NOT NULL DEFAULT 2.5,
            interval_days INT NOT NULL DEFAULT 1,
            repetitions INT   NOT NULL DEFAULT 0,
            next_review DATE  NOT NULL DEFAULT '2000-01-01',
            last_review DATE,
            last_quality INT,
            mastered    BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at  TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, group_id)
        )""",
        """CREATE TABLE IF NOT EXISTS session_history (
            id            SERIAL PRIMARY KEY,
            user_id       INT         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_date  DATE        NOT NULL,
            practice_mode VARCHAR(20) NOT NULL DEFAULT 'type',
            total         INT         NOT NULL DEFAULT 0,
            correct       INT         NOT NULL DEFAULT 0,
            duration_secs INT,
            created_at    TIMESTAMP   NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS notification_prefs (
            id          SERIAL PRIMARY KEY,
            user_id     INT          NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            email       VARCHAR(120) NOT NULL DEFAULT '',
            enabled     BOOLEAN      NOT NULL DEFAULT FALSE,
            notify_hour INT          NOT NULL DEFAULT 8,
            last_sent   DATE
        )""",
        """CREATE TABLE IF NOT EXISTS shared_packs (
            id           SERIAL PRIMARY KEY,
            token        VARCHAR(32)  NOT NULL UNIQUE,
            user_id      INT          NOT NULL,
            label        VARCHAR(120) NOT NULL DEFAULT '',
            category     VARCHAR(80)  NOT NULL DEFAULT '',
            is_public    BOOLEAN      NOT NULL DEFAULT FALSE,
            words_json   TEXT         NOT NULL,
            word_count   INT          NOT NULL DEFAULT 0,
            created_at   TIMESTAMP    DEFAULT NOW(),
            expires_at   TIMESTAMP,
            import_count INT          NOT NULL DEFAULT 0
        )""",
    ]
    conn = get_db()
    conn.autocommit = True
    for sql in tables:
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        except Exception as e:
            print(f"[TABLE WARNING] {e}")


@auth_bp.route("/auth", methods=["GET", "POST", "OPTIONS"])
def auth():
    if request.method == "OPTIONS":
        return "", 204

    method = request.method
    action = request.args.get("action", "")

    # POST ?action=register
    if method == "POST" and action == "register":
        b = body()
        username = b.get("username", "").strip().lower()
        password = b.get("password", "").strip()

        if len(username) < 3:
            return err("El usuario debe tener al menos 3 caracteres")
        if len(username) > 30:
            return err("El usuario no puede superar 30 caracteres")
        if not re.match(r"^[a-z0-9_]+$", username):
            return err("Solo letras, números y guión bajo")
        if len(password) < 6:
            return err("La contraseña debe tener al menos 6 caracteres")

        existing = db_fetchone("SELECT id FROM users WHERE username = %s", (username,))
        if existing:
            return err("Ese nombre de usuario ya existe", 409)

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        uid = db_insert(
            "INSERT INTO users (username, password) VALUES (%s, %s) RETURNING id",
            (username, pw_hash),
        )
        token = make_token(uid)
        return ok({"user_id": uid, "username": username, "token": token})

    # POST ?action=login
    if method == "POST" and action == "login":
        b = body()
        username = b.get("username", "").strip().lower()
        password = b.get("password", "").strip()

        if not username or not password:
            return err("Credenciales requeridas")

        user = db_fetchone(
            "SELECT id, password FROM users WHERE username = %s", (username,)
        )
        if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
            return err("Usuario o contraseña incorrectos", 401)

        uid = int(user["id"])
        token = make_token(uid)
        return ok({"user_id": uid, "username": username, "token": token})

    # POST ?action=logout
    if method == "POST" and action == "logout":
        token = (
            request.args.get("_t", "")
            or request.headers.get("X-Session-Token", "")
            or body().get("token", "")
            or request.cookies.get("lexlo_token", "")
        )
        if token:
            db_exec("DELETE FROM session_tokens WHERE token = %s", (token,))
        return ok(None)

    # GET ?action=me
    if method == "GET" and action == "me":
        token = (
            request.args.get("_t", "")
            or request.headers.get("X-Session-Token", "")
            or request.args.get("token", "")
            or request.cookies.get("lexlo_token", "")
        )
        if not token:
            return err("Sin token", 401)

        row = db_fetchone(
            """SELECT u.id, u.username, st.expires_at
               FROM session_tokens st
               JOIN users u ON u.id = st.user_id
               WHERE st.token = %s AND st.expires_at > NOW()""",
            (token,),
        )
        if not row:
            return err("Sesión inválida o expirada", 401)

        return ok({"user_id": int(row["id"]), "username": row["username"]})

    return err("Acción no válida")