"""
auth.py — Registro · Login · Logout · Me
"""
import hashlib
from flask import Blueprint, request, g
from config import (
    ok, err, body, db_exec, db_fetchone, db_insert,
    make_token, get_uid
)
import bcrypt

auth_bp = Blueprint("auth", __name__)


def create_tables():
    tables = [
        """CREATE TABLE IF NOT EXISTS users (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            username   VARCHAR(30)  NOT NULL UNIQUE,
            email      VARCHAR(120) NULL,
            password   VARCHAR(255) NOT NULL,
            created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS session_tokens (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            user_id    INT          NOT NULL,
            token      VARCHAR(64)  NOT NULL UNIQUE,
            expires_at DATETIME     NOT NULL,
            created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS word_groups (
            id               INT AUTO_INCREMENT PRIMARY KEY,
            user_id          INT          NOT NULL,
            spanish          VARCHAR(120) NOT NULL,
            example_sentence TEXT         NULL,
            category         VARCHAR(80)  NOT NULL DEFAULT '',
            created_at       DATE         NOT NULL DEFAULT (CURRENT_DATE),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS words (
            id       INT AUTO_INCREMENT PRIMARY KEY,
            group_id INT          NOT NULL,
            english  VARCHAR(120) NOT NULL,
            is_hard  TINYINT(1)   NOT NULL DEFAULT 0,
            FOREIGN KEY (group_id) REFERENCES word_groups(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS practice_log (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            user_id       INT          NOT NULL,
            group_id      INT          NOT NULL,
            direction     VARCHAR(10)  NOT NULL,
            practice_mode VARCHAR(20)  NULL,
            answer        TEXT         NOT NULL,
            correct       TINYINT(1)   NOT NULL DEFAULT 0,
            feedback      TEXT         NULL,
            created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id)  REFERENCES users(id)       ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES word_groups(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS weekly_tests (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            user_id    INT  NOT NULL,
            week_start DATE NOT NULL,
            score      INT  NOT NULL DEFAULT 0,
            total      INT  NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS word_srs (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            user_id     INT   NOT NULL,
            group_id    INT   NOT NULL,
            easiness    FLOAT NOT NULL DEFAULT 2.5,
            `interval`  INT   NOT NULL DEFAULT 1,
            repetitions INT   NOT NULL DEFAULT 0,
            next_review DATE  NOT NULL DEFAULT '2000-01-01',
            last_review DATE  NULL,
            last_quality INT  NULL,
            mastered    TINYINT(1) NOT NULL DEFAULT 0,
            updated_at  DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_user_group (user_id, group_id),
            FOREIGN KEY (user_id)  REFERENCES users(id)       ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES word_groups(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS session_history (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            user_id       INT         NOT NULL,
            session_date  DATE        NOT NULL,
            practice_mode VARCHAR(20) NOT NULL DEFAULT 'type',
            total         INT         NOT NULL DEFAULT 0,
            correct       INT         NOT NULL DEFAULT 0,
            duration_secs INT         NULL,
            created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS notification_prefs (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            user_id     INT          NOT NULL UNIQUE,
            email       VARCHAR(120) NOT NULL DEFAULT '',
            enabled     TINYINT(1)   NOT NULL DEFAULT 0,
            notify_hour INT          NOT NULL DEFAULT 8,
            last_sent   DATE         NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS shared_packs (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            token        VARCHAR(32)  NOT NULL UNIQUE,
            user_id      INT          NOT NULL,
            label        VARCHAR(120) NOT NULL DEFAULT '',
            category     VARCHAR(80)  NOT NULL DEFAULT '',
            is_public    TINYINT(1)   NOT NULL DEFAULT 0,
            words_json   MEDIUMTEXT   NOT NULL,
            word_count   INT          NOT NULL DEFAULT 0,
            created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
            expires_at   DATETIME     NULL DEFAULT NULL,
            import_count INT          NOT NULL DEFAULT 0
        )""",
    ]
    for sql in tables:
        try:
            db_exec(sql)
        except Exception:
            pass
    # Silent migrations
    silent_alters = [
        "ALTER TABLE practice_log ADD COLUMN practice_mode VARCHAR(20) NULL AFTER direction",
        "ALTER TABLE users ADD COLUMN email VARCHAR(120) NULL AFTER username",
        "ALTER TABLE word_groups ADD COLUMN example_sentence TEXT NULL AFTER spanish",
        "ALTER TABLE word_groups ADD COLUMN category VARCHAR(80) NOT NULL DEFAULT '' AFTER example_sentence",
        "ALTER TABLE word_srs ADD COLUMN last_quality INT NULL",
    ]
    for sql in silent_alters:
        try:
            db_exec(sql)
        except Exception:
            pass


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
        import re
        if not re.match(r"^[a-z0-9_]+$", username):
            return err("Solo letras, números y guión bajo")
        if len(password) < 6:
            return err("La contraseña debe tener al menos 6 caracteres")

        existing = db_fetchone("SELECT id FROM users WHERE username = %s", (username,))
        if existing:
            return err("Ese nombre de usuario ya existe", 409)

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        uid = db_insert(
            "INSERT INTO users (username, password) VALUES (%s, %s)",
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
