"""
srs.py — Spaced Repetition System (SM-2 simplificado)
"""
from datetime import date, timedelta
from flask import Blueprint, request, g
from config import (
    ok, err, body, db_exec, db_fetchall, db_fetchone,
    db_insert, db_update, require_auth
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
    today = str(date.today())

    # Compatibilidad ONLY_FULL_GROUP_BY
    try:
        db_exec("SET SESSION sql_mode = (SELECT REPLACE(@@SESSION.sql_mode,'ONLY_FULL_GROUP_BY',''))")
    except Exception:
        pass

    # Fix rows con next_review null
    try:
        db_exec("UPDATE word_srs SET next_review = CURDATE() WHERE next_review IS NULL OR next_review = '0000-00-00' OR next_review = '2000-01-01'")
    except Exception:
        pass

    # GET ?action=due
    if method == "GET" and action == "due":
        review_date = request.args.get("date", today)
        limit = min(100, max(1, int(request.args.get("limit", 20))))

        try:
            db_exec(
                "INSERT IGNORE INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s",
                (uid, uid),
            )
        except Exception:
            pass

        rows = db_fetchall(
            """SELECT g.id, g.spanish, g.created_at,
                      GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
                      GROUP_CONCAT(w.is_hard  ORDER BY w.id SEPARATOR '||') AS english_diffs,
                      s.easiness, s.`interval`, s.repetitions, s.next_review, s.mastered
               FROM word_srs s
               JOIN word_groups g ON g.id = s.group_id
               JOIN words w ON w.group_id = g.id
               WHERE s.user_id = %s AND s.next_review <= %s AND s.mastered = 0
               GROUP BY g.id, g.spanish, g.created_at, s.easiness, s.`interval`, s.repetitions, s.next_review, s.mastered
               ORDER BY s.next_review ASC, s.easiness ASC
               LIMIT %s""",
            (uid, review_date, limit),
        )

        for r in rows:
            r["english_words"] = r["english_words"].split("||") if r["english_words"] else []
            r["english_diffs"] = [
                "hard" if v == "1" or v == 1 else "normal"
                for v in (r["english_diffs"].split("||") if r["english_diffs"] else [])
            ]
            r["easiness"] = float(r["easiness"])
            r["interval"] = int(r["interval"])
            r["repetitions"] = int(r["repetitions"])
            r["mastered"] = bool(r["mastered"])
            if r.get("next_review"):
                r["next_review"] = str(r["next_review"])
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])

        count_row = db_fetchone(
            "SELECT COUNT(*) AS cnt FROM word_srs WHERE user_id = %s AND next_review <= %s AND mastered = 0",
            (uid, review_date),
        )
        total_due = int(count_row["cnt"]) if count_row else 0

        return ok({"due": rows, "total_due": total_due})

    # POST ?action=review
    if method == "POST" and action == "review":
        b = body()
        group_id = int(b.get("group_id", 0))
        quality = max(0, min(5, int(b.get("quality", 0))))
        if not group_id:
            return err("group_id requerido")

        try:
            db_exec("INSERT IGNORE INTO word_srs (user_id, group_id) VALUES (%s, %s)", (uid, group_id))
        except Exception:
            pass

        srs = db_fetchone(
            "SELECT easiness, `interval`, repetitions FROM word_srs WHERE user_id = %s AND group_id = %s",
            (uid, group_id),
        )
        if not srs:
            return err("SRS record not found", 500)

        ef = float(srs["easiness"])
        interval = int(srs["interval"])
        reps = int(srs["repetitions"])

        if quality >= 3:
            if reps == 0:
                interval = 1
            elif reps == 1:
                interval = 6
            else:
                interval = round(interval * ef)
            reps += 1
            ef = max(1.3, ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        else:
            reps = 0
            interval = 1
            ef = max(1.3, ef - 0.2)

        interval = min(365, max(1, interval))
        next_review = str(date.today() + timedelta(days=interval))
        mastered = 1 if (interval >= 21 and quality == 5) else 0

        db_update(
            "UPDATE word_srs SET easiness=%s, `interval`=%s, repetitions=%s, next_review=%s, last_quality=%s, mastered=%s WHERE user_id=%s AND group_id=%s",
            (ef, interval, reps, next_review, quality, mastered, uid, group_id),
        )

        return ok({
            "easiness": round(ef, 2),
            "interval": interval,
            "repetitions": reps,
            "next_review": next_review,
            "mastered": bool(mastered),
        })

    # POST ?action=mark_mastered
    if method == "POST" and action == "mark_mastered":
        b = body()
        group_id = int(b.get("group_id", 0))
        mastered = int(bool(b.get("mastered", True)))
        if not group_id:
            return err("group_id requerido")
        next_review_val = "9999-12-31" if mastered else today
        db_exec(
            """INSERT INTO word_srs (user_id, group_id, mastered, next_review)
               VALUES (%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE mastered = %s, next_review = %s""",
            (uid, group_id, mastered, next_review_val, mastered, next_review_val),
        )
        return ok({"group_id": group_id, "mastered": bool(mastered)})

    # GET ?action=word_status
    if method == "GET" and action == "word_status":
        gid = int(request.args.get("group_id", 0))
        if not gid:
            return err("group_id requerido")
        row = db_fetchone(
            "SELECT easiness, `interval`, repetitions, next_review, mastered FROM word_srs WHERE user_id = %s AND group_id = %s",
            (uid, gid),
        )
        if row:
            if row.get("next_review"):
                row["next_review"] = str(row["next_review"])
        else:
            row = {"easiness": 2.5, "interval": 1, "repetitions": 0, "next_review": today, "last_quality": None, "mastered": False}
        return ok(row)

    # GET ?action=overview
    if method == "GET" and action == "overview":
        try:
            db_exec(
                "INSERT IGNORE INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s",
                (uid, uid),
            )
        except Exception:
            pass

        ov = db_fetchone(
            """SELECT COUNT(*) AS total,
                      SUM(mastered = 1) AS mastered,
                      SUM(mastered = 0 AND next_review <= %s) AS due_today,
                      SUM(mastered = 0 AND next_review > %s) AS scheduled,
                      SUM(repetitions = 0) AS new_words,
                      SUM(repetitions >= 1 AND mastered = 0) AS learning,
                      ROUND(AVG(easiness), 2) AS avg_easiness
               FROM word_srs WHERE user_id = %s""",
            (today, today, uid),
        )

        forecast = []
        for i in range(7):
            day = str(date.today() + timedelta(days=i))
            cnt = db_fetchone(
                "SELECT COUNT(*) AS cnt FROM word_srs WHERE user_id = %s AND next_review = %s AND mastered = 0",
                (uid, day),
            )
            forecast.append({"date": day, "count": int(cnt["cnt"]) if cnt else 0})

        levels = db_fetchall(
            """SELECT CASE
                   WHEN mastered = 1       THEN 'dominada'
                   WHEN repetitions = 0    THEN 'nueva'
                   WHEN `interval` <= 3    THEN 'aprendiendo'
                   WHEN `interval` <= 14   THEN 'repasando'
                   ELSE 'consolidada'
               END AS level, COUNT(*) AS cnt
               FROM word_srs WHERE user_id = %s GROUP BY level""",
            (uid,),
        )

        return ok({"overview": ov, "forecast": forecast, "levels": levels})

    # GET ?action=word_progress
    if method == "GET" and action == "word_progress":
        try:
            db_exec(
                "INSERT IGNORE INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s",
                (uid, uid),
            )
        except Exception:
            pass

        filter_val = request.args.get("filter", "all")
        where_extra = ""
        if filter_val == "due":
            where_extra = f" AND s.next_review <= '{today}' AND s.mastered = 0"
        elif filter_val == "mastered":
            where_extra = " AND s.mastered = 1"
        elif filter_val == "learning":
            where_extra = " AND s.repetitions >= 1 AND s.mastered = 0"
        elif filter_val == "new":
            where_extra = " AND s.repetitions = 0"

        rows = db_fetchall(
            f"""SELECT g.id, g.spanish, g.created_at,
                       GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
                       s.easiness, s.`interval`, s.repetitions, s.next_review, s.mastered,
                       COALESCE(acc.total, 0) AS practice_total,
                       COALESCE(acc.correct, 0) AS practice_correct,
                       CASE WHEN s.mastered=1 THEN 'dominada'
                            WHEN s.repetitions=0 THEN 'nueva'
                            WHEN s.`interval`<=3 THEN 'aprendiendo'
                            WHEN s.`interval`<=14 THEN 'repasando'
                            ELSE 'consolidada' END AS srs_level
                FROM word_srs s
                JOIN word_groups g ON g.id = s.group_id
                JOIN words w ON w.group_id = g.id
                LEFT JOIN (SELECT group_id, COUNT(*) AS total, SUM(correct) AS correct
                           FROM practice_log WHERE user_id = %s GROUP BY group_id) acc
                       ON acc.group_id = g.id
                WHERE s.user_id = %s{where_extra}
                GROUP BY g.id, g.spanish, g.created_at, s.easiness, s.`interval`, s.repetitions,
                         s.next_review, s.mastered, acc.total, acc.correct
                ORDER BY s.mastered ASC, s.next_review ASC, s.easiness ASC
                LIMIT 200""",
            (uid, uid),
        )

        for r in rows:
            r["english_words"] = r["english_words"].split("||") if r["english_words"] else []
            r["easiness"] = round(float(r["easiness"]), 2)
            r["interval"] = int(r["interval"])
            r["repetitions"] = int(r["repetitions"])
            r["mastered"] = bool(r["mastered"])
            r["practice_total"] = int(r["practice_total"])
            r["practice_correct"] = int(r["practice_correct"])
            r["accuracy_pct"] = round(r["practice_correct"] / r["practice_total"] * 100) if r["practice_total"] > 0 else None
            if r.get("next_review"):
                r["next_review"] = str(r["next_review"])
            if r.get("created_at"):
                r["created_at"] = str(r["created_at"])

        return ok(rows)

    # POST ?action=reset_progress
    if method == "POST" and action == "reset_progress":
        db_update("DELETE FROM word_srs WHERE user_id = %s", (uid,))
        db_update("DELETE FROM practice_log WHERE user_id = %s", (uid,))
        try:
            db_update("DELETE FROM weekly_tests WHERE user_id = %s", (uid,))
        except Exception:
            pass
        return ok({"reset": "srs_and_practice_log"})

    # GET ?action=words_by_filter
    if method == "GET" and action == "words_by_filter":
        filter_val = request.args.get("filter", "all")
        try:
            db_exec(
                "INSERT IGNORE INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s",
                (uid, uid),
            )
        except Exception:
            pass

        if filter_val == "due":
            rows = db_fetchall(
                """SELECT g.id, g.spanish FROM word_groups g
                   LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                   WHERE g.user_id = %s AND (s.id IS NULL OR (s.mastered = 0 AND s.next_review <= %s))""",
                (uid, today),
            )
        elif filter_val == "learning":
            rows = db_fetchall(
                """SELECT g.id, g.spanish FROM word_groups g
                   JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                   WHERE g.user_id = %s AND s.mastered = 0 AND s.repetitions >= 1""",
                (uid,),
            )
        elif filter_val == "mastered":
            rows = db_fetchall(
                """SELECT g.id, g.spanish FROM word_groups g
                   JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                   WHERE g.user_id = %s AND s.mastered = 1""",
                (uid,),
            )
        elif filter_val == "new":
            rows = db_fetchall(
                """SELECT g.id, g.spanish FROM word_groups g
                   LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                   WHERE g.user_id = %s AND (s.id IS NULL OR s.repetitions = 0)""",
                (uid,),
            )
        else:
            rows = db_fetchall(
                "SELECT g.id, g.spanish FROM word_groups g WHERE g.user_id = %s", (uid,)
            )

        if not rows:
            return err("No hay palabras en este filtro", 404)
        return ok(rows)

    return err("Acción no válida")
