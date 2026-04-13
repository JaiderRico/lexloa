"""
words.py — CRUD de palabras + reset (PostgreSQL)
"""
import re
from datetime import date
from flask import Blueprint, request, g
from config import (
    ok, err, body, db_exec, db_fetchall, db_fetchone,
    db_insert, db_update, groq_call, parse_groq_json, require_auth, get_db
)

words_bp = Blueprint("words", __name__)


def _split(val):
    return val.split("||") if val else []


@words_bp.route("/words", methods=["GET", "POST", "DELETE", "OPTIONS"])
@require_auth
def words():
    if request.method == "OPTIONS":
        return "", 204

    uid = g.uid
    method = request.method
    action = request.args.get("action", "")

    # GET ?action=list&date=YYYY-MM-DD
    if method == "GET" and action == "list":
        d = request.args.get("date", str(date.today()))
        rows = db_fetchall(
            """SELECT g.id, g.spanish, g.created_at, g.example_sentence,
                      COALESCE(g.category,'') AS category,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                      STRING_AGG(w.is_hard::text, '||' ORDER BY w.id) AS english_diffs
               FROM word_groups g
               JOIN words w ON w.group_id = g.id
               WHERE g.user_id = %s AND g.created_at = %s
               GROUP BY g.id
               ORDER BY g.id DESC""",
            (uid, d),
        )
        for r in rows:
            r["english_words"] = _split(r["english_words"])
            r["english_diffs"] = [
                "hard" if v in ("true", "t", "1") else "normal"
                for v in _split(r.get("english_diffs") or "")
            ]
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
        return ok(rows)

    # GET ?action=dates
    if method == "GET" and action == "dates":
        rows = db_fetchall(
            """SELECT created_at AS date, COUNT(*) AS total
               FROM word_groups WHERE user_id = %s
               GROUP BY created_at ORDER BY created_at DESC""",
            (uid,),
        )
        for r in rows:
            r["date"] = str(r["date"])
        return ok(rows)

    # GET ?action=week
    if method == "GET" and action == "week":
        from datetime import timedelta
        ws_str = request.args.get("week_start")
        if ws_str:
            ws = date.fromisoformat(ws_str)
        else:
            today = date.today()
            ws = today - timedelta(days=today.weekday())
        we = ws + timedelta(days=6)
        rows = db_fetchall(
            """SELECT g.id, g.spanish, g.created_at, g.example_sentence,
                      COALESCE(g.category,'') AS category,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words
               FROM word_groups g
               JOIN words w ON w.group_id = g.id
               WHERE g.user_id = %s AND g.created_at BETWEEN %s AND %s
               GROUP BY g.id ORDER BY g.created_at, g.id""",
            (uid, str(ws), str(we)),
        )
        for r in rows:
            r["english_words"] = _split(r["english_words"])
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
        return ok(rows)

    # GET ?action=weekly_stats
    if method == "GET" and action == "weekly_stats":
        rows = db_fetchall(
            """SELECT date_trunc('week', created_at::timestamp) AS week_start,
                      COUNT(*) AS total_groups,
                      COUNT(DISTINCT created_at) AS days_active
               FROM word_groups WHERE user_id = %s
               GROUP BY week_start ORDER BY week_start DESC LIMIT 12""",
            (uid,),
        )
        for r in rows:
            r["week_start"] = str(r["week_start"])[:10] if r.get("week_start") else None
        return ok(rows)

    # GET ?action=categories
    if method == "GET" and action == "categories":
        rows = db_fetchall(
            """SELECT COALESCE(category,'') AS category, COUNT(*) AS word_count
               FROM word_groups WHERE user_id = %s AND category != ''
               GROUP BY category ORDER BY word_count DESC""",
            (uid,),
        )
        return ok(rows)

    # GET ?action=by_category&category=X
    if method == "GET" and action == "by_category":
        cat = request.args.get("category", "").strip()
        if not cat:
            return err("category requerido")
        rows = db_fetchall(
            """SELECT g.id, g.spanish, g.created_at, COALESCE(g.category,'') AS category,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                      STRING_AGG(w.is_hard::text, '||' ORDER BY w.id) AS english_diffs
               FROM word_groups g
               JOIN words w ON w.group_id = g.id
               WHERE g.user_id = %s AND g.category = %s
               GROUP BY g.id ORDER BY g.id DESC""",
            (uid, cat),
        )
        for r in rows:
            r["english_words"] = _split(r["english_words"])
            r["english_diffs"] = [
                "hard" if v in ("true", "t", "1") else "normal"
                for v in _split(r.get("english_diffs") or "")
            ]
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
        return ok(rows)

    # GET ?action=search&q=TEXTO
    if method == "GET" and action == "search":
        q = request.args.get("q", "").strip()
        lang = request.args.get("lang", "both")
        if not q:
            return err("Parámetro q requerido")
        if len(q) < 2:
            return err("Mínimo 2 caracteres para buscar")
        like = f"%{q}%"
        if lang == "es":
            where = "g.spanish ILIKE %s"
            params = (uid, like)
        elif lang == "en":
            where = "w.english ILIKE %s"
            params = (uid, like)
        else:
            where = "(g.spanish ILIKE %s OR w.english ILIKE %s)"
            params = (uid, like, like)
        rows = db_fetchall(
            f"""SELECT DISTINCT g.id, g.spanish, g.created_at,
                       STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words
                FROM word_groups g
                JOIN words w ON w.group_id = g.id
                WHERE g.user_id = %s AND {where}
                GROUP BY g.id
                ORDER BY g.created_at DESC, g.id DESC
                LIMIT 100""",
            params,
        )
        for r in rows:
            r["english_words"] = _split(r["english_words"])
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
        return ok({"query": q, "total": len(rows), "results": rows})

    # GET ?action=distractors
    if method == "GET" and action == "distractors":
        gid = int(request.args.get("group_id", 0))
        d = request.args.get("date", str(date.today()))
        if not gid:
            return err("group_id requerido")
        rows = db_fetchall(
            """SELECT g.id, g.spanish,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words
               FROM word_groups g
               JOIN words w ON w.group_id = g.id
               WHERE g.user_id = %s AND g.id != %s
               GROUP BY g.id
               ORDER BY ABS(g.created_at - %s::date), RANDOM()
               LIMIT 3""",
            (uid, gid, d),
        )
        for r in rows:
            r["english_words"] = _split(r["english_words"])
        return ok(rows)

    # POST ?action=validate
    if method == "POST" and action == "validate":
        b = body()
        spanish = b.get("spanish", "").strip()
        english = [str(e).strip() for e in b.get("english", []) if str(e).strip()]
        if not spanish or not english:
            return err("Faltan campos")

        word_results = []
        has_invalid = False
        for en_word in english:
            prompt = (
                f'Eres un asistente de vocabulario estricto. El estudiante quiere registrar: español="{spanish}", inglés="{en_word}".\n'
                "Determina si esta traducción específica es correcta o razonablemente válida.\n"
                "REGLAS:\n"
                f'- valid=false si la palabra inglesa es inventada, sin sentido, o claramente incorrecta.\n'
                f'- valid=false si no hay relación semántica entre "{spanish}" y "{en_word}".\n'
                f'- valid=true solo si la traducción es correcta o es un sinónimo cercano reconocido.\n'
                'Responde SOLO JSON sin markdown: {"valid":true/false,"warning":"","suggestion":""}\n'
                "- warning: vacío si válida, descripción del error en español (máx 15 palabras)\n"
                "- suggestion: traducción correcta en inglés si hay error, sino vacío"
            )
            raw = groq_call(prompt, 100)
            parsed = parse_groq_json(raw)
            result = {"word": en_word, "valid": True, "warning": "", "suggestion": ""}
            if parsed and "valid" in parsed:
                result["valid"] = bool(parsed["valid"])
                result["warning"] = parsed.get("warning", "")
                result["suggestion"] = parsed.get("suggestion", "")
            word_results.append(result)
            if not result["valid"]:
                has_invalid = True

        if not has_invalid:
            return ok({"valid": True, "warning": "", "suggestion": "", "word_results": word_results})
        invalid_labels = [f'"{r["word"]}"' for r in word_results if not r["valid"]]
        warning = (
            f"La palabra {invalid_labels[0]} parece incorrecta"
            if len(invalid_labels) == 1
            else f"Las palabras {', '.join(invalid_labels)} parecen incorrectas"
        )
        return ok({"valid": False, "warning": warning, "suggestion": "", "word_results": word_results})

    # POST ?action=add
    if method == "POST" and action == "add":
        b = body()
        spanish = b.get("spanish", "").strip().lower()
        category = b.get("category", "").strip()
        raw_english = b.get("english", [])
        english = []
        for entry in raw_english:
            if isinstance(entry, dict):
                word = entry.get("word", "").strip().lower()
                diff = entry.get("difficulty", "normal")
                if diff not in ("normal", "hard"):
                    diff = "normal"
            else:
                word = str(entry).strip().lower()
                diff = "normal"
            if word:
                english.append({"word": word, "difficulty": diff})
        seen = []
        unique_english = []
        for e in english:
            if e["word"] not in seen:
                seen.append(e["word"])
                unique_english.append(e)
        english = unique_english

        if not spanish:
            return err("El español es requerido")
        if not english:
            return err("Al menos una palabra en inglés")

        existing = db_fetchone(
            "SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s LIMIT 1",
            (uid, spanish),
        )
        if existing:
            return ok({"duplicate": True, "group_id": int(existing["id"])})

        words_only = [e["word"] for e in english]
        placeholders = ",".join(["%s"] * len(words_only))
        dup_en = db_fetchall(
            f"""SELECT w.english FROM words w
                JOIN word_groups g ON g.id = w.group_id
                WHERE g.user_id = %s AND w.english IN ({placeholders})""",
            tuple([uid] + words_only),
        )
        if dup_en:
            return ok({"duplicate": True, "duplicate_en": [r["english"] for r in dup_en]})

        conn = get_db()
        conn.autocommit = False
        try:
            today = str(date.today())
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO word_groups (user_id, spanish, created_at, category) VALUES (%s, %s, %s, %s) RETURNING id",
                    (uid, spanish, today, category),
                )
                gid = cur.fetchone()["id"]
                for en in english:
                    cur.execute(
                        "INSERT INTO words (group_id, english, is_hard) VALUES (%s, %s, %s)",
                        (gid, en["word"], en["difficulty"] == "hard"),
                    )
            conn.commit()
            conn.autocommit = True

            # Generar frase de ejemplo
            try:
                prompt = (
                    f'You are an English tutor. Create ONE short natural English sentence (10-15 words) using the word "{english[0]["word"]}". '
                    f'The sentence should clearly show the word\'s meaning (Spanish: "{spanish}"). '
                    'Reply ONLY with a JSON object, no markdown: {"sentence":"the English sentence","translation":"Spanish translation"}'
                )
                raw = groq_call(prompt, 100)
                parsed = parse_groq_json(raw)
                if parsed and parsed.get("sentence"):
                    example = parsed["sentence"]
                    if parsed.get("translation"):
                        example += " — " + parsed["translation"]
                    db_update("UPDATE word_groups SET example_sentence = %s WHERE id = %s", (example, gid))
            except Exception:
                pass

            return ok({"group_id": int(gid)})
        except Exception as e:
            conn.rollback()
            conn.autocommit = True
            return err(f"Error al guardar: {e}", 500)

    # POST ?action=set_word_diff
    if method == "POST" and action == "set_word_diff":
        b = body()
        gid = int(b.get("group_id", 0))
        word_index = int(b.get("word_index", 0))
        difficulty = b.get("difficulty", "normal")
        if difficulty not in ("normal", "hard"):
            difficulty = "normal"
        if not gid:
            return err("group_id requerido")
        check = db_fetchone("SELECT id FROM word_groups WHERE id = %s AND user_id = %s", (gid, uid))
        if not check:
            return err("Grupo no encontrado", 404)
        word_rows = db_fetchall("SELECT id FROM words WHERE group_id = %s ORDER BY id ASC", (gid,))
        if word_index >= len(word_rows):
            return err("Palabra no encontrada", 404)
        wid = word_rows[word_index]["id"]
        db_update("UPDATE words SET is_hard = %s WHERE id = %s", (difficulty == "hard", wid))
        return ok({"word_id": wid, "difficulty": difficulty, "is_hard": difficulty == "hard"})

    # DELETE ?action=delete&id=X
    if method == "DELETE" and action == "delete":
        gid = int(request.args.get("id", 0))
        if not gid:
            return err("ID requerido")
        rows_affected = db_update("DELETE FROM word_groups WHERE id = %s AND user_id = %s", (gid, uid))
        if rows_affected == 0:
            return err("Grupo no encontrado", 404)
        return ok(None)

    # POST ?action=reset
    if method == "POST" and action == "reset":
        b = body()
        scope = b.get("scope", "")
        value = b.get("value", "")

        if scope == "all":
            db_update("DELETE FROM word_groups WHERE user_id = %s", (uid,))
            db_update("DELETE FROM practice_log WHERE user_id = %s", (uid,))
            db_update("DELETE FROM weekly_tests WHERE user_id = %s", (uid,))
            return ok({"deleted": "all"})

        elif scope == "week" and value:
            from datetime import timedelta
            ws = date.fromisoformat(value)
            we = ws + timedelta(days=6)
            gids = [r["id"] for r in db_fetchall(
                "SELECT id FROM word_groups WHERE user_id = %s AND created_at BETWEEN %s AND %s",
                (uid, str(ws), str(we)),
            )]
            if gids:
                ph = ",".join(["%s"] * len(gids))
                db_update(f"DELETE FROM word_groups WHERE id IN ({ph})", tuple(gids))
            db_update("DELETE FROM weekly_tests WHERE user_id = %s AND week_start = %s", (uid, value))
            return ok({"deleted": "week", "week_start": value, "groups_removed": len(gids)})

        elif scope == "date" and value:
            gids = [r["id"] for r in db_fetchall(
                "SELECT id FROM word_groups WHERE user_id = %s AND created_at = %s", (uid, value)
            )]
            if gids:
                ph = ",".join(["%s"] * len(gids))
                db_update(f"DELETE FROM word_groups WHERE id IN ({ph})", tuple(gids))
            return ok({"deleted": "date", "date": value, "groups_removed": len(gids)})

        else:
            return err("scope inválido. Usa: all | week | date")

    # POST ?action=add_synonym
    if method == "POST" and action == "add_synonym":
        b = body()
        group_id = int(b.get("group_id", 0))
        word = b.get("word", "").strip()
        if not group_id or not word:
            return err("Faltan campos")
        st = db_fetchone("SELECT id FROM word_groups WHERE id = %s AND user_id = %s", (group_id, uid))
        if not st:
            return err("Grupo no encontrado", 404)
        existing = db_fetchone(
            "SELECT id FROM words WHERE group_id = %s AND LOWER(english) = LOWER(%s)", (group_id, word)
        )
        if existing:
            return ok({"added": False, "message": "Ya existe"})
        db_exec("INSERT INTO words (group_id, english, is_hard) VALUES (%s, %s, FALSE)", (group_id, word))
        return ok({"added": True})

    return err("Acción no válida")