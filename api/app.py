"""
app.py — Entry point principal (Flask + Gunicorn para Render)
"""
import os
from flask import Flask, request, send_from_directory
from flask_cors import CORS

from config import close_db, get_db
from auth import auth_bp, create_tables
from words import words_bp
from practice import practice_bp
from quiz import quiz_bp
from srs import srs_bp
from stats import stats_bp
from notify import notify_bp
from share import share_bp

app = Flask(__name__, static_folder="static", static_url_path="")

# CORS — permite peticiones del frontend (mismo dominio en Render o diferente)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

# Teardown: cerrar conexión DB al final de cada request
app.teardown_appcontext(close_db)


# ── Inicializar tablas al arrancar ───────────────────────────────────────────
with app.app_context():
    try:
        get_db()
        create_tables()
    except Exception as e:
        print(f"[WARNING] No se pudieron crear las tablas al inicio: {e}")


# ── Registrar blueprints bajo /api ───────────────────────────────────────────
app.register_blueprint(auth_bp,     url_prefix="/api")
app.register_blueprint(words_bp,    url_prefix="/api")
app.register_blueprint(practice_bp, url_prefix="/api")
app.register_blueprint(quiz_bp,     url_prefix="/api")
app.register_blueprint(srs_bp,      url_prefix="/api")
app.register_blueprint(stats_bp,    url_prefix="/api")
app.register_blueprint(notify_bp,   url_prefix="/api")
app.register_blueprint(share_bp,    url_prefix="/api")


# ── Sirve el frontend (index.html) ───────────────────────────────────────────
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    static_dir = app.static_folder
    if path and os.path.exists(os.path.join(static_dir, path)):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, "index.html")


# ── Health check ─────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)