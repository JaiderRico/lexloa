"""
stats.py — Estadísticas del usuario (PostgreSQL)
"""
from datetime import date, timedelta
from flask import Blueprint, request, g, make_response
from config import (
    ok, err, db_exec, db_fetchall, db_fetchone, require_auth, today_col
)
import json

stats_bp = Blueprint("stats", __name__)

@stats_bp.route("/stats", methods=["GET", "POST", "OPTIONS"])
@require_auth
def stats():
    if request.method == "OPTIONS":
        return "", 204
    print(f"[STATS] uid={g.uid} method={request.method} action={request.args.get('action','')}", flush=True)

    uid = g.uid
    method = request.method
    action = request.args.get("action", "")
    # Usamos la fecha del sistema o la proporcionada por la config
    today_date = today_col() 

    if method == "GET" and action == "full_summary":
        # Total de palabras
        total = db_fetchone(
            "SELECT COUNT(*) AS total FROM word_groups WHERE user_id = %s", (uid,)
        )
        
        # Obtener fechas únicas de práctica para calcular racha
        streaks = db_fetchall(
            """SELECT DISTINCT DATE(created_at) as practice_date 
               FROM practice_log
               WHERE user_id = %s 
               ORDER BY practice_date DESC""",
            (uid,),
        )
        
        current_streak = 0
        best_streak = 0
        
        if streaks:
            # Lógica de racha actual
            latest_practice = streaks[0]["practice_date"]
            # Si practicó hoy o ayer, la racha sigue viva
            if (today_date - latest_practice).days <= 1:
                temp_streak = 1
                for i in range(len(streaks) - 1):
                    if (streaks[i]["practice_date"] - streaks[i+1]["practice_date"]).days == 1:
                        temp_streak += 1
                    else:
                        break
                current_streak = temp_streak
            
            # Lógica de mejor racha histórica
            temp_best = 1
            for i in range(len(streaks) - 1):
                if (streaks[i]["practice_date"] - streaks[i+1]["practice_date"]).days == 1:
                    temp_best += 1
                else:
                    best_streak = max(best_streak, temp_best)
                    temp_best = 1
            best_streak = max(best_streak, temp_best)

        # Totales de precisión
        acc = db_fetchone(
            """SELECT COALESCE(SUM(correct::int), 0) AS total_correct,
                      COALESCE(SUM(attempts), 0) AS total_attempts
               FROM practice_log WHERE user_id = %s""",
            (uid,),
        )
        
        return ok({
            "total_words":    int(total["total"]) if total else 0,
            "current_streak": current_streak,
            "best_streak":    best_streak,
            "total_correct":  int(acc["total_correct"]) if acc else 0,
            "total_attempts": int(acc["total_attempts"]) if acc else 0,
            "days_practiced": len(streaks)
        })

    if method == "GET" and action == "mode_breakdown":
        # Asegúrate de que practice_answers tenga los datos por modo
        rows = db_fetchall(
            """SELECT mode,
                      COALESCE(SUM(correct::int), 0) AS correct,
                      COALESCE(SUM(attempts), 0) AS attempts
               FROM practice_answers
               WHERE user_id = %s
               GROUP BY mode""",
            (uid,),
        )
        return ok(rows)

    # ... (el resto de acciones se mantienen igual)

    # ── GET action=word_progress ─────────────────────────────────────────────
    if method == "GET" and action == "word_progress":
        f = request.args.get("filter", "all")
        
        db_exec("INSERT INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s ON CONFLICT DO NOTHING", (uid, uid))
        
        if f == "due":
            extra = "AND s.next_review <= %s AND s.mastered = FALSE"
            params = (uid, today)
        elif f == "new":
            extra = "AND s.repetitions = 0"
            params = (uid,)
        elif f == "learning":
            extra = "AND s.repetitions >= 1 AND s.mastered = FALSE"
            params = (uid,)
        elif f == "mastered":
            extra = "AND s.mastered = TRUE"
            params = (uid,)
        else:
            extra = ""
            params = (uid,)

        rows = db_fetchall(
            f"""SELECT g.id AS group_id, g.spanish, g.created_at,
                       STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                       COALESCE(s.next_review::text, '')  AS next_review,
                       COALESCE(s.interval_days, 1)       AS interval,
                       COALESCE(s.easiness, 2.5)          AS ease_factor,
                       COALESCE(s.repetitions, 0)         AS repetitions,
                       COALESCE(s.mastered, FALSE)        AS mastered
                FROM word_groups g
                JOIN words w ON w.group_id = g.id
                LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                WHERE g.user_id = %s {extra}
                GROUP BY g.id, g.spanish, g.created_at, s.next_review, s.interval_days, s.easiness, s.repetitions, s.mastered
                ORDER BY g.created_at DESC
                LIMIT 200""",
            params,
        )
        
        for r in rows:
            r["english_words"] = _split(r["english_words"])
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])
        
        return ok(rows if rows else [])

    # ── GET action=export ────────────────────────────────────────────────────
    if method == "GET" and action == "export":
        rows = db_fetchall(
            """SELECT g.spanish, g.created_at,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                      STRING_AGG(w.is_hard::text, '||' ORDER BY w.id) AS english_diffs
               FROM word_groups g
               JOIN words w ON w.group_id = g.id
               WHERE g.user_id = %s
               GROUP BY g.id, g.spanish, g.created_at
               ORDER BY g.created_at DESC""",
            (uid,),
        )
        words = []
        for r in rows:
            eng_words = _split(r["english_words"])
            eng_diffs = _split(r.get("english_diffs") or "")
            english = [
                {
                    "word": w.strip(),
                    "difficulty": "hard" if eng_diffs[i] in ("true", "t") else "normal",
                }
                for i, w in enumerate(eng_words)
                if w.strip()
            ]
            words.append({
                "spanish":    r["spanish"],
                "english":    english,
                "created_at": str(r["created_at"]),
            })
        
        resp = make_response(json.dumps({"version": 2, "words": words}, ensure_ascii=False, indent=2))
        resp.headers["Content-Type"] = "application/json; charset=utf-8"
        resp.headers["Content-Disposition"] = "attachment; filename=lexlo_export.json"
        return resp

    # ── POST action=import_preview ───────────────────────────────────────────
    if method == "POST" and action == "import_preview":
        b = body()
        data = b.get("data")
        if not data or not data.get("words"):
            return err("Datos de importación inválidos")

        words_to_review = []
        duplicates = 0

        for entry in data["words"]:
            spanish = entry.get("spanish", "").strip().lower()
            if not spanish:
                duplicates += 1
                continue
            dup = db_fetchone(
                "SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s LIMIT 1",
                (uid, spanish),
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
                "spanish":    spanish,
                "english":    english,
                "created_at": created_at,
            })

        return ok({"words": words_to_review, "duplicates": duplicates})

    # ── POST action=add_single ───────────────────────────────────────────────
    if method == "POST" and action == "add_single":
        b = body()
        spanish = b.get("spanish", "").strip().lower()
        english_list = b.get("english", [])
        created_at_raw = b.get("created_at", str(date.today()))

        if not spanish or not english_list:
            return err("Datos incompletos")

        created_at = created_at_raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", created_at_raw) else str(date.today())

        dup = db_fetchone(
            "SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s LIMIT 1",
            (uid, spanish),
        )
        if dup:
            return ok({"id": dup["id"], "skipped": True})

        conn = get_db()
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO word_groups (user_id, spanish, created_at) VALUES (%s, %s, %s) RETURNING id",
                    (uid, spanish, created_at),
                )
                gid = cur.fetchone()["id"]
                for en in english_list:
                    word = en.get("word", "").strip().lower() if isinstance(en, dict) else str(en).strip().lower()
                    diff = en.get("difficulty", "normal") if isinstance(en, dict) else "normal"
                    if word:
                        cur.execute(
                            "INSERT INTO words (group_id, english, is_hard) VALUES (%s, %s, %s)",
                            (gid, word, diff == "hard"),
                        )
            conn.commit()
            conn.autocommit = True
        except Exception as e:
            conn.rollback()
            conn.autocommit = True
            return err(f"Error al insertar: {e}", 500)

        return ok({"id": gid})

    # ── GET action=heatmap ───────────────────────────────────────────────────
    if method == "GET" and action == "heatmap":
        months = max(1, min(12, int(request.args.get("months", 6))))
        rows = db_fetchall(
            """SELECT DATE(created_at)::text AS date,
                      COALESCE(SUM(correct::int), 0) AS correct,
                      COUNT(*) AS attempts
               FROM practice_log
               WHERE user_id = %s
                 AND created_at >= CURRENT_DATE - (%s * INTERVAL '30 days')
               GROUP BY DATE(created_at)
               ORDER BY DATE(created_at)""",
            (uid, months),
        )
        return ok(rows if rows else [])

    # ── GET action=session_history ───────────────────────────────────────────
    if method == "GET" and action == "session_history":
        days = max(1, min(90, int(request.args.get("days", 30))))
        rows = db_fetchall(
            """SELECT session_date::text AS date,
                      ROUND(100.0 * SUM(correct) / NULLIF(SUM(total), 0)) AS accuracy_pct,
                      SUM(total) AS total,
                      SUM(correct) AS correct,
                      json_agg(json_build_object(
                          'mode', practice_mode,
                          'total', total,
                          'correct', correct,
                          'duration_secs', duration_secs
                      ) ORDER BY id) AS modes
               FROM session_history
               WHERE user_id = %s
                 AND session_date >= CURRENT_DATE - %s * INTERVAL '1 day'
               GROUP BY session_date
               ORDER BY session_date DESC
               LIMIT 60""",
            (uid, days),
        )
        for r in rows:
            r["accuracy_pct"] = int(r["accuracy_pct"]) if r["accuracy_pct"] else 0
            r["total"] = int(r["total"]) if r["total"] else 0
            r["correct"] = int(r["correct"]) if r["correct"] else 0
        return ok(rows if rows else [])

    return err("Acción no válida", 400)