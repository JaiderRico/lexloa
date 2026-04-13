"""
share.py — Compartir paquetes de palabras entre usuarios
"""
import json
import secrets
from datetime import date
from flask import Blueprint, request, g
from api.config import (
    ok, err, body, db_exec, db_fetchall, db_fetchone,
    db_insert, db_update, APP_URL, require_auth
)

share_bp = Blueprint("share", __name__)


@share_bp.route("/share", methods=["GET", "POST", "DELETE", "OPTIONS"])
def share():
    if request.method == "OPTIONS":
        return "", 204

    method = request.method
    action = request.args.get("action", "")

    # ── Public endpoints (no auth required) ─────────────────────────────────

    # GET ?action=get&token=XXX
    if method == "GET" and action == "get":
        token = request.args.get("token", "").strip()
        if not token:
            return err("Token requerido")
        pack = db_fetchone("SELECT * FROM shared_packs WHERE token = %s", (token,))
        if not pack:
            return err("Paquete no encontrado o expirado", 404)
        owner = db_fetchone("SELECT username FROM users WHERE id = %s", (pack["user_id"],))
        return ok({
            "token": pack["token"],
            "label": pack["label"],
            "word_count": int(pack["word_count"]),
            "owner": owner["username"] if owner else "usuario",
            "import_count": int(pack["import_count"]),
            "created_at": str(pack["created_at"]),
            "words": json.loads(pack["words_json"]),
        })

    # GET ?action=public_packs
    if method == "GET" and action == "public_packs":
        cat = request.args.get("category", "").strip()
        if cat:
            packs = db_fetchall(
                """SELECT sp.token, sp.label, COALESCE(sp.category,'') AS category,
                          sp.word_count, sp.import_count, u.username AS owner
                   FROM shared_packs sp JOIN users u ON u.id = sp.user_id
                   WHERE COALESCE(sp.is_public,0) = 1 AND sp.category = %s
                   ORDER BY sp.import_count DESC, sp.created_at DESC LIMIT 50""",
                (cat,),
            )
        else:
            packs = db_fetchall(
                """SELECT sp.token, sp.label, COALESCE(sp.category,'') AS category,
                          sp.word_count, sp.import_count, u.username AS owner
                   FROM shared_packs sp JOIN users u ON u.id = sp.user_id
                   WHERE COALESCE(sp.is_public,0) = 1
                   ORDER BY sp.import_count DESC, sp.created_at DESC LIMIT 50""",
            )
        try:
            cats = [r["category"] for r in db_fetchall(
                "SELECT DISTINCT category FROM shared_packs WHERE COALESCE(is_public,0)=1 AND category != '' ORDER BY category"
            )]
        except Exception:
            cats = []
        return ok({"packs": packs, "categories": cats})

    # ── Auth-required endpoints ──────────────────────────────────────────────
    from api.config import get_uid
    uid = get_uid()
    if uid is None and action in ("create", "import", "mine", "delete"):
        return err("No autenticado", 401)

    # POST ?action=create
    if method == "POST" and action == "create":
        b = body()
        label = b.get("label", "").strip()
        category = b.get("category", "").strip()
        is_public = int(bool(b.get("is_public", False)))
        group_ids = b.get("group_ids", [])
        date_val = b.get("date")
        dates = b.get("dates", [])
        words_direct = b.get("_words_direct", [])

        if date_val:
            dates = [date_val]

        words = []
        if words_direct:
            for w in words_direct:
                sp = w.get("spanish", "").strip()
                en = [e.strip() for e in (w.get("english") or []) if str(e).strip()]
                if sp and en:
                    words.append({"spanish": sp, "english": en})
        elif group_ids:
            ph = ",".join(["%s"] * len(group_ids))
            rows = db_fetchall(
                f"""SELECT g.id, g.spanish,
                           GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words
                    FROM word_groups g JOIN words w ON w.group_id = g.id
                    WHERE g.user_id = %s AND g.id IN ({ph})
                    GROUP BY g.id""",
                tuple([uid] + group_ids),
            )
            for r in rows:
                words.append({"spanish": r["spanish"], "english": r["english_words"].split("||")})
        elif dates:
            ph = ",".join(["%s"] * len(dates))
            rows = db_fetchall(
                f"""SELECT g.id, g.spanish,
                           GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words
                    FROM word_groups g JOIN words w ON w.group_id = g.id
                    WHERE g.user_id = %s AND DATE(g.created_at) IN ({ph})
                    GROUP BY g.id""",
                tuple([uid] + dates),
            )
            for r in rows:
                words.append({"spanish": r["spanish"], "english": r["english_words"].split("||")})
        else:
            return err("Debes especificar group_ids, date, dates o _words_direct")

        if not words:
            return err("No hay palabras para compartir")
        if not label:
            label = f"{len(words)} palabras"

        token = secrets.token_hex(16)
        db_insert(
            "INSERT INTO shared_packs (token, user_id, label, category, is_public, words_json, word_count) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (token, uid, label, category, is_public, json.dumps(words, ensure_ascii=False), len(words)),
        )
        return ok({
            "token": token,
            "word_count": len(words),
            "label": label,
            "category": category,
            "is_public": bool(is_public),
            "url": f"{APP_URL}/?share={token}",
        })

    # POST ?action=import&token=XXX
    if method == "POST" and action == "import":
        token = request.args.get("token", "").strip()
        if not token:
            return err("Token requerido")
        pack = db_fetchone("SELECT * FROM shared_packs WHERE token = %s", (token,))
        if not pack:
            return err("Paquete no encontrado", 404)

        words = json.loads(pack["words_json"]) or []
        added = 0
        skipped = 0
        group_ids = []

        from api.config import get_db
        conn = get_db()
        conn.begin()
        try:
            for w in words:
                spanish = w.get("spanish", "").strip()
                english = [e.strip() for e in w.get("english", []) if str(e).strip()]
                if not spanish or not english:
                    skipped += 1
                    continue
                dup = db_fetchone(
                    "SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s LIMIT 1",
                    (uid, spanish),
                )
                if dup:
                    skipped += 1
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO word_groups (user_id, spanish, created_at) VALUES (%s, %s, NOW())",
                        (uid, spanish),
                    )
                    gid = cur.lastrowid
                    group_ids.append(int(gid))
                    for en in english:
                        cur.execute(
                            "INSERT INTO words (group_id, english, is_hard) VALUES (%s, %s, 0)",
                            (gid, en),
                        )
                added += 1
            conn.commit()
            db_update("UPDATE shared_packs SET import_count = import_count + 1 WHERE token = %s", (token,))
        except Exception as e:
            conn.rollback()
            return err(f"Error al importar: {e}", 500)

        return ok({"added": added, "skipped": skipped, "group_ids": group_ids})

    # GET ?action=mine
    if method == "GET" and action == "mine":
        rows = db_fetchall(
            """SELECT token, label, COALESCE(category,'') AS category,
                      COALESCE(is_public,0) AS is_public, word_count, import_count, created_at
               FROM shared_packs WHERE user_id = %s ORDER BY created_at DESC LIMIT 20""",
            (uid,),
        )
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
        return ok(rows)

    # DELETE ?action=delete&token=XXX
    if method == "DELETE" and action == "delete":
        token = request.args.get("token", "").strip()
        if not token:
            return err("Token requerido")
        affected = db_update(
            "DELETE FROM shared_packs WHERE token = %s AND user_id = %s", (token, uid)
        )
        if affected == 0:
            return err("Paquete no encontrado", 404)
        return ok(None)

    return err("Acción no válida", 400)
