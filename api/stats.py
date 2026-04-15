"""
stats.py — Estadísticas del usuario (PostgreSQL)
"""
import re
from datetime import date
from flask import Blueprint, request, g, jsonify, make_response
from config import (
    ok, err, body, db_exec, db_fetchall, db_fetchone,
    db_insert, db_update, require_auth, get_db
)
import json

stats_bp = Blueprint("stats", __name__)


def _split(val):
    return val.split("||") if val else []


@stats_bp.route("/stats", methods=["GET", "POST", "OPTIONS"])
@require_auth
def stats():
    if request.method == "OPTIONS":
        return "", 204

    uid = g.uid
    method = request.method
    action = request.args.get("action", "")

    # ── GET action=full_summary ──────────────────────────────────────────────
    if method == "GET" and action == "full_summary":
        total = db_fetchone(
            "SELECT COUNT(*) AS total FROM word_groups WHERE user_id = %s", (uid,)
        )
        
        # Calcular streaks solo si hay práctica
        streaks = db_fetchall(
            """SELECT practice_date FROM practice_log
               WHERE user_id = %s ORDER BY practice_date DESC""",
            (uid,),
        )
        
        current_streak = 0
        best_streak = 0
        if streaks:
            streak = 1
            prev = streaks[0]["practice_date"]
            for row in streaks[1:]:
                d = row["practice_date"]
                if (prev - d).days == 1:
                    streak += 1
                else:
                    best_streak = max(best_streak, streak)
                    streak = 1
                prev = d
            best_streak = max(best_streak, streak)
            today = date.today()
            current_streak = streak if (today - streaks[0]["practice_date"]).days <= 1 else 0

        acc = db_fetchone(
            """SELECT COALESCE(SUM(correct),0) AS total_correct,
                      COALESCE(SUM(attempts),0) AS total_attempts
               FROM word_srs WHERE user_id = %s""",
            (uid,),
        )
        
        # Siempre devolver ok=True, incluso con datos vacíos
        return ok({
            "total_words":    int(total["total"]) if total and total["total"] else 0,
            "current_streak": current_streak,
            "best_streak":    best_streak,
            "total_correct":  int(acc["total_correct"]) if acc else 0,
            "total_attempts": int(acc["total_attempts"]) if acc else 0,
            "days_practiced": len(streaks) if streaks else 0,
        })

    # ── GET action=srs_overview ──────────────────────────────────────────────
    if method == "GET" and action == "srs_overview":
        today = str(date.today())
        due = db_fetchone(
            """SELECT COUNT(*) AS n FROM word_srs
               WHERE user_id = %s AND next_review <= %s""",
            (uid, today),
        )
        new_w = db_fetchone(
            """SELECT COUNT(*) AS n FROM word_groups g
               LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
               WHERE g.user_id = %s AND (s.attempts IS NULL OR s.attempts = 0)""",
            (uid,),
        )
        learning = db_fetchone(
            """SELECT COUNT(*) AS n FROM word_srs
               WHERE user_id = %s AND interval < 21 AND attempts > 0""",
            (uid,),
        )
        mature = db_fetchone(
            """SELECT COUNT(*) AS n FROM word_srs
               WHERE user_id = %s AND interval >= 21""",
            (uid,),
        )
        
        # Siempre devolver ok=True
        return ok({
            "due_today": int(due["n"]) if due else 0,
            "new_words": int(new_w["n"]) if new_w else 0,
            "learning":  int(learning["n"]) if learning else 0,
            "mature":    int(mature["n"]) if mature else 0,
        })

    # ── GET action=mode_breakdown ────────────────────────────────────────────
    if method == "GET" and action == "mode_breakdown":
        rows = db_fetchall(
            """SELECT mode,
                      COALESCE(SUM(correct),0)  AS correct,
                      COALESCE(SUM(attempts),0) AS attempts
               FROM practice_answers
               WHERE user_id = %s
               GROUP BY mode""",
            (uid,),
        )
        return ok(rows if rows else [])

    # ── GET action=word_progress ─────────────────────────────────────────────
    if method == "GET" and action == "word_progress":
        f = request.args.get("filter", "all")
        today = str(date.today())

        if f == "due":
            extra = "AND (s.next_review IS NULL OR s.next_review <= %s)"
            params = (uid, today)
        elif f == "new":
            extra = "AND (s.attempts IS NULL OR s.attempts = 0)"
            params = (uid,)
        else:  # "all"
            extra = ""
            params = (uid,)

        rows = db_fetchall(
            f"""SELECT g.id AS group_id, g.spanish,
                       STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                       COALESCE(s.next_review::text, '')  AS next_review,
                       COALESCE(s.interval, 1)            AS interval,
                       COALESCE(s.ease_factor, 2.5)       AS ease_factor,
                       COALESCE(s.correct, 0)             AS correct,
                       COALESCE(s.attempts, 0)            AS attempts
                FROM word_groups g
                JOIN words w ON w.group_id = g.id
                LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                WHERE g.user_id = %s {extra}
                GROUP BY g.id, g.spanish, s.next_review, s.interval, s.ease_factor, s.correct, s.attempts
                ORDER BY g.created_at DESC
                LIMIT 200""",
            params,
        )
        
        # Siempre devolver una lista (puede estar vacía)
        for r in rows:
            r["english_words"] = _split(r["english_words"])
        
        return ok(rows if rows else [])

    # ── GET action=export ────────────────────────────────────────────────────
    if method == "GET" and action == "export":
        rows = db_fetchall(
            """SELECT g.spanish, g.created_at,
                      STRING_AGG(w.english, '||' ORDER BY w.id)       AS english_words,
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