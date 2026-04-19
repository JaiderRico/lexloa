"""
srs.py — Spaced Repetition System (PostgreSQL)
"""
from datetime import date, timedelta
from flask import Blueprint, request, g
from config import (
    ok, err, body, db_exec, db_fetchall, db_fetchone,
    db_insert, db_update, require_auth,today_col
)

srs_bp = Blueprint("srs", __name__)


@srs_bp.route("/srs", methods=["GET", "POST", "OPTIONS"])
@require_auth
def srs():
    if request.method == "OPTIONS":
        return "", 204

    uid = g.uid
    method = request.method
    action = request.args.get("action", "")
    today = str(today_col())

    # GET ?action=due
    if method == "GET" and action == "due":
        review_date = request.args.get("date", today)
        limit = min(100, max(1, int(request.args.get("limit", 20))))

        db_exec("INSERT INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s ON CONFLICT DO NOTHING", (uid, uid))

        rows = db_fetchall(
            """SELECT g.id, g.spanish, g.created_at,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                      STRING_AGG(w.is_hard::text, '||' ORDER BY w.id) AS english_diffs,
                      s.easiness, s.interval_days, s.repetitions, s.next_review, s.mastered
               FROM word_srs s
               JOIN word_groups g ON g.id = s.group_id
               JOIN words w ON w.group_id = g.id
               WHERE s.user_id = %s AND s.next_review <= %s AND s.mastered = FALSE
               GROUP BY g.id, g.spanish, g.created_at, s.easiness, s.interval_days, s.repetitions, s.next_review, s.mastered
               ORDER BY s.next_review ASC, s.easiness ASC
               LIMIT %s""",
            (uid, review_date, limit),
        )

        for r in rows:
            r["english_words"] = r["english_words"].split("||") if r["english_words"] else []
            r["english_diffs"] = ["hard" if v in ("true","t") else "normal" for v in (r["english_diffs"].split("||") if r["english_diffs"] else [])]
            r["easiness"] = float(r["easiness"])
            r["interval"] = int(r["interval_days"])
            r["repetitions"] = int(r["repetitions"])
            r["mastered"] = bool(r["mastered"])
            if r.get("next_review"): r["next_review"] = str(r["next_review"])
            if r.get("created_at"): r["created_at"] = str(r["created_at"])
            r.pop("interval_days", None)

        count_row = db_fetchone("SELECT COUNT(*) AS cnt FROM word_srs WHERE user_id = %s AND next_review <= %s AND mastered = FALSE", (uid, review_date))
        return ok({"due": rows, "total_due": int(count_row["cnt"]) if count_row else 0})

    # POST ?action=review
    if method == "POST" and action == "review":
        b = body()
        group_id = int(b.get("group_id", 0))
        quality = max(0, min(5, int(b.get("quality", 0))))
        if not group_id: return err("group_id requerido")

        db_exec("INSERT INTO word_srs (user_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (uid, group_id))
        srs_row = db_fetchone("SELECT easiness, interval_days, repetitions FROM word_srs WHERE user_id = %s AND group_id = %s", (uid, group_id))
        if not srs_row: return err("SRS record not found", 500)

        hard_word = db_fetchone("SELECT COUNT(*) AS hard FROM words WHERE group_id = %s AND is_hard = TRUE", (group_id,))
        is_hard = hard_word and hard_word["hard"] > 0
        difficulty_mod = 1.5 if is_hard else 1.0

        ef = float(srs_row["easiness"]); interval = int(srs_row["interval_days"]); reps = int(srs_row["repetitions"])

        if quality >= 3:
            if reps == 0: interval = 1
            elif reps == 1: interval = 6
            else: interval = round(interval * ef * difficulty_mod)
            reps += 1
            ef = max(1.3, ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        else:
            reps = 0; interval = 1; ef = max(1.3, ef - 0.2)

        if is_hard:
            interval = min(365, max(1, round(interval * 1.3)))
        interval = min(365, max(1, interval))
        next_review = str(today_col() + timedelta(days=interval))
        mastered = interval >= 21 and quality >= 4

        db_update(
            "UPDATE word_srs SET easiness=%s, interval_days=%s, repetitions=%s, next_review=%s, last_quality=%s, mastered=%s WHERE user_id=%s AND group_id=%s",
            (ef, interval, reps, next_review, quality, mastered, uid, group_id),
        )
        return ok({"easiness": round(ef, 2), "interval": interval, "repetitions": reps, "next_review": next_review, "mastered": mastered, "is_hard": is_hard})

    # POST ?action=mark_mastered
    if method == "POST" and action == "mark_mastered":
        b = body()
        group_id = int(b.get("group_id", 0))
        mastered = bool(b.get("mastered", True))
        if not group_id: return err("group_id requerido")
        next_review_val = "9999-12-31" if mastered else today
        db_exec(
            """INSERT INTO word_srs (user_id, group_id, mastered, next_review)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (user_id, group_id) DO UPDATE SET mastered = %s, next_review = %s""",
            (uid, group_id, mastered, next_review_val, mastered, next_review_val),
        )
        return ok({"group_id": group_id, "mastered": mastered})

    # GET ?action=word_status
    if method == "GET" and action == "word_status":
        gid = int(request.args.get("group_id", 0))
        if not gid: return err("group_id requerido")
        row = db_fetchone("SELECT easiness, interval_days AS interval, repetitions, next_review, mastered FROM word_srs WHERE user_id = %s AND group_id = %s", (uid, gid))
        if row and row.get("next_review"):
            row["next_review"] = str(row["next_review"])
        return ok(row or {"easiness": 2.5, "interval": 1, "repetitions": 0, "next_review": today, "last_quality": None, "mastered": False})

    # GET ?action=overview
    if method == "GET" and action == "overview":
        db_exec("INSERT INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s ON CONFLICT DO NOTHING", (uid, uid))

        ov = db_fetchone(
            """SELECT COUNT(*) AS total,
                      SUM(mastered::int) AS mastered,
                      SUM(CASE WHEN mastered=FALSE AND next_review <= %s THEN 1 ELSE 0 END) AS due_today,
                      SUM(CASE WHEN mastered=FALSE AND next_review > %s THEN 1 ELSE 0 END) AS scheduled,
                      SUM(CASE WHEN repetitions=0 THEN 1 ELSE 0 END) AS new_words,
                      SUM(CASE WHEN repetitions>=1 AND mastered=FALSE THEN 1 ELSE 0 END) AS learning,
                      ROUND(AVG(easiness)::numeric, 2) AS avg_easiness
               FROM word_srs WHERE user_id = %s""",
            (today, today, uid),
        )

        forecast = []
        for i in range(7):
            day = str(today_col() + timedelta(days=i))
            cnt = db_fetchone("SELECT COUNT(*) AS cnt FROM word_srs WHERE user_id = %s AND next_review = %s AND mastered = FALSE", (uid, day))
            forecast.append({"date": day, "count": int(cnt["cnt"]) if cnt else 0})

        levels = db_fetchall(
            """SELECT CASE WHEN mastered THEN 'dominada'
                          WHEN repetitions=0 THEN 'nueva'
                          WHEN interval_days<=3 THEN 'aprendiendo'
                          WHEN interval_days<=14 THEN 'repasando'
                          ELSE 'consolidada' END AS level,
                      COUNT(*) AS cnt
               FROM word_srs WHERE user_id = %s GROUP BY level""",
            (uid,),
        )
        return ok({"overview": ov, "forecast": forecast, "levels": levels})

    # GET ?action=word_progress
    if method == "GET" and action == "word_progress":
        db_exec("INSERT INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s ON CONFLICT DO NOTHING", (uid, uid))

        filter_val = request.args.get("filter", "all")
        where_extra = ""
        if filter_val == "due": where_extra = f" AND s.next_review <= '{today}' AND s.mastered = FALSE"
        elif filter_val == "mastered": where_extra = " AND s.mastered = TRUE"
        elif filter_val == "learning": where_extra = " AND s.repetitions >= 1 AND s.mastered = FALSE"
        elif filter_val == "new": where_extra = " AND s.repetitions = 0"

        rows = db_fetchall(
            f"""SELECT g.id, g.spanish, g.created_at,
                       STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                       s.easiness, s.interval_days, s.repetitions, s.next_review, s.mastered,
                       COALESCE(acc.total, 0) AS practice_total,
                       COALESCE(acc.correct, 0) AS practice_correct,
                       CASE WHEN s.mastered THEN 'dominada'
                            WHEN s.repetitions=0 THEN 'nueva'
                            WHEN s.interval_days<=3 THEN 'aprendiendo'
                            WHEN s.interval_days<=14 THEN 'repasando'
                            ELSE 'consolidada' END AS srs_level
                FROM word_srs s
                JOIN word_groups g ON g.id = s.group_id
                JOIN words w ON w.group_id = g.id
                LEFT JOIN (SELECT group_id, COUNT(*) AS total, SUM(correct::int) AS correct
                           FROM practice_log WHERE user_id = %s GROUP BY group_id) acc ON acc.group_id = g.id
                WHERE s.user_id = %s{where_extra}
                GROUP BY g.id, g.spanish, g.created_at, s.easiness, s.interval_days, s.repetitions, s.next_review, s.mastered, acc.total, acc.correct
                ORDER BY s.mastered ASC, s.next_review ASC, s.easiness ASC
                LIMIT 200""",
            (uid, uid),
        )
        for r in rows:
            r["english_words"] = r["english_words"].split("||") if r["english_words"] else []
            r["easiness"] = round(float(r["easiness"]), 2)
            r["interval"] = int(r["interval_days"]); r.pop("interval_days", None)
            r["repetitions"] = int(r["repetitions"]); r["mastered"] = bool(r["mastered"])
            r["practice_total"] = int(r["practice_total"]); r["practice_correct"] = int(r["practice_correct"])
            r["accuracy_pct"] = round(r["practice_correct"] / r["practice_total"] * 100) if r["practice_total"] > 0 else None
            if r.get("next_review"): r["next_review"] = str(r["next_review"])
            if r.get("created_at"): r["created_at"] = str(r["created_at"])
        return ok(rows)

    # POST ?action=reset_progress
    if method == "POST" and action == "reset_progress":
        db_update("DELETE FROM word_srs WHERE user_id = %s", (uid,))
        db_update("DELETE FROM practice_log WHERE user_id = %s", (uid,))
        try: db_update("DELETE FROM weekly_tests WHERE user_id = %s", (uid,))
        except Exception: pass
        return ok({"reset": "srs_and_practice_log"})

    # GET ?action=words_by_filter
    if method == "GET" and action == "words_by_filter":
        filter_val = request.args.get("filter", "all")
        db_exec("INSERT INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s ON CONFLICT DO NOTHING", (uid, uid))

        if filter_val == "due":
            rows = db_fetchall("SELECT g.id, g.spanish FROM word_groups g LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id WHERE g.user_id = %s AND (s.id IS NULL OR (s.mastered = FALSE AND s.next_review <= %s))", (uid, today))
        elif filter_val == "learning":
            rows = db_fetchall("SELECT g.id, g.spanish FROM word_groups g JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id WHERE g.user_id = %s AND s.mastered = FALSE AND s.repetitions >= 1", (uid,))
        elif filter_val == "mastered":
            rows = db_fetchall("SELECT g.id, g.spanish FROM word_groups g JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id WHERE g.user_id = %s AND s.mastered = TRUE", (uid,))
        elif filter_val == "new":
            rows = db_fetchall("SELECT g.id, g.spanish FROM word_groups g LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id WHERE g.user_id = %s AND (s.id IS NULL OR s.repetitions = 0)", (uid,))
        else:
            rows = db_fetchall("SELECT g.id, g.spanish FROM word_groups g WHERE g.user_id = %s", (uid,))

        if not rows: return err("No hay palabras en este filtro", 404)
        return ok(rows)

    return err("Acción no válida")