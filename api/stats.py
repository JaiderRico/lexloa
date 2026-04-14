"""
DIFF para stats.py
Agrega dos nuevos actions al endpoint /stats:

  1. GET/POST  action=import_preview   → filtra duplicados y devuelve lista para revisar
  2. POST      action=add_single       → agrega una sola palabra confirmada por el usuario

También agrega action=import_preview a share.py para el flujo de token.
"""

# ── Pega estos bloques DENTRO del if/elif chain de stats() ─────────────────

# ★ NUEVO action: import_preview (POST)
# Recibe el mismo payload que "import" pero NO inserta nada.
# Solo filtra duplicados y devuelve la lista limpia para que el usuario revise.

    if method == "POST" and action == "import_preview":
        b = body()
        data = b.get("data")
        if not data or not data.get("words"):
            return err("Datos de importación inválidos")

        import re
        words_to_review = []
        duplicates = 0

        for entry in data["words"]:
            spanish = entry.get("spanish", "").strip().lower()
            if not spanish:
                duplicates += 1
                continue
            # Verificar si ya existe
            dup = db_fetchone(
                "SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s LIMIT 1",
                (uid, spanish)
            )
            if dup:
                duplicates += 1
                continue

            english = []
            for e in entry.get("english", []):
                word = e.get("word", "").strip().lower() if isinstance(e, dict) else str(e).strip().lower()
                diff = e.get("difficulty", "normal") if isinstance(e, dict) else "normal"
                if word:
                    english.append({"word": word, "difficulty": diff})

            if not english:
                duplicates += 1
                continue

            created_at = entry.get("created_at", str(date.today()))
            if not re.match(r"^\d{4}-\d{2}-\d{2}", created_at):
                created_at = str(date.today())
            created_at = created_at[:10]

            words_to_review.append({
                "spanish": spanish,
                "english": english,
                "created_at": created_at
            })

        return ok({"words": words_to_review, "duplicates": duplicates})


# ★ NUEVO action: add_single (POST)
# Agrega una sola palabra que el usuario ya confirmó escribiendo.

    if method == "POST" and action == "add_single":
        b = body()
        spanish = b.get("spanish", "").strip().lower()
        english_list = b.get("english", [])
        created_at_raw = b.get("created_at", str(date.today()))

        if not spanish or not english_list:
            return err("Datos incompletos")

        import re
        created_at = created_at_raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", created_at_raw) else str(date.today())

        # Verificar duplicado de último momento
        dup = db_fetchone(
            "SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s LIMIT 1",
            (uid, spanish)
        )
        if dup:
            return ok({"id": dup["id"], "skipped": True})

        conn = get_db()
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO word_groups (user_id, spanish, created_at) VALUES (%s, %s, %s) RETURNING id",
                    (uid, spanish, created_at)
                )
                gid = cur.fetchone()["id"]
                for en in english_list:
                    word = en.get("word", "").strip().lower() if isinstance(en, dict) else str(en).strip().lower()
                    diff = en.get("difficulty", "normal") if isinstance(en, dict) else "normal"
                    if word:
                        cur.execute(
                            "INSERT INTO words (group_id, english, is_hard) VALUES (%s, %s, %s)",
                            (gid, word, diff == "hard")
                        )
            conn.commit()
            conn.autocommit = True
        except Exception as e:
            conn.rollback()
            conn.autocommit = True
            return err(f"Error al insertar: {e}", 500)

        return ok({"id": gid})


# ── share.py: agrega import_preview para el flujo de token ─────────────────
# En el Blueprint de share, agrega este endpoint GET:

# @share_bp.route("/share", methods=["GET"])
# @require_auth
# def share_import_preview():
#     action = request.args.get("action", "")
#     uid = g.uid
#
#     if action == "import_preview":
#         token = request.args.get("token", "").strip()
#         if not token:
#             return err("Token requerido")
#
#         pack = db_fetchone(
#             "SELECT id FROM word_packs WHERE token = %s",
#             (token,)
#         )
#         if not pack:
#             return err("Paquete no encontrado", 404)
#
#         pack_id = pack["id"]
#         rows = db_fetchall(
#             """SELECT g.spanish, g.created_at,
#                       STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
#                       STRING_AGG(w.is_hard::text, '||' ORDER BY w.id) AS english_diffs
#                FROM pack_words pw
#                JOIN word_groups g ON g.id = pw.word_group_id
#                JOIN words w ON w.group_id = g.id
#                WHERE pw.pack_id = %s
#                GROUP BY g.id, g.spanish, g.created_at""",
#             (pack_id,)
#         )
#
#         words_to_review = []
#         duplicates = 0
#         for r in rows:
#             spanish = r["spanish"].strip().lower()
#             dup = db_fetchone(
#                 "SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s LIMIT 1",
#                 (uid, spanish)
#             )
#             if dup:
#                 duplicates += 1
#                 continue
#             wlist = r["english_words"].split("||") if r["english_words"] else []
#             diffs = r["english_diffs"].split("||") if r.get("english_diffs") else []
#             english = [
#                 {"word": w.strip().lower(), "difficulty": "hard" if diffs[i] in ("true","t") else "normal"}
#                 for i, w in enumerate(wlist) if w.strip()
#             ]
#             if not english:
#                 duplicates += 1
#                 continue
#             words_to_review.append({
#                 "spanish": spanish,
#                 "english": english,
#                 "created_at": str(r["created_at"])
#             })
#
#         return ok({"words": words_to_review, "duplicates": duplicates})
#
#     return err("Acción no válida", 400)