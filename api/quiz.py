"""
quiz.py — Quiz semanal (PostgreSQL)
"""
import random
from datetime import date, timedelta
from flask import Blueprint, request, g
from config import (
    ok, err, body, db_exec, db_fetchall, db_fetchone,
    db_insert, db_update, groq_call, parse_groq_json, require_auth
)

quiz_bp = Blueprint("quiz", __name__)


def _split(val):
    return val.split("||") if val else []


def srs_update_quiz(uid: int, group_id: int, quality: int):
    try:
        db_exec("INSERT INTO word_srs (user_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (uid, group_id))
        srs = db_fetchone("SELECT easiness, interval_days, repetitions FROM word_srs WHERE user_id = %s AND group_id = %s", (uid, group_id))
        if not srs: return
        ef = float(srs["easiness"]); interval = int(srs["interval_days"]); reps = int(srs["repetitions"])
        if quality >= 3:
            if reps == 0: interval = 1
            elif reps == 1: interval = 6
            else: interval = round(interval * ef)
            reps += 1
            ef = max(1.3, ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        else:
            reps = 0; interval = 1; ef = max(1.3, ef - 0.2)
        interval = min(365, max(1, interval))
        mastered = interval >= 21 and quality >= 4
        db_update(
            "UPDATE word_srs SET easiness=%s, interval_days=%s, repetitions=%s, next_review=%s, last_quality=%s, mastered=%s WHERE user_id=%s AND group_id=%s",
            (ef, interval, reps, str(date.today() + timedelta(days=interval)), quality, mastered, uid, group_id),
        )
    except Exception:
        pass


@quiz_bp.route("/quiz", methods=["GET", "POST", "OPTIONS"])
@require_auth
def quiz():
    if request.method == "OPTIONS":
        return "", 204

    uid = g.uid
    method = request.method
    action = request.args.get("action", "")
    today = str(date.today())

    # GET ?action=questions
    if method == "GET" and action == "questions":
        ws_str = request.args.get("week_start")
        ws = date.fromisoformat(ws_str) if ws_str else date.today() - timedelta(days=date.today().weekday())
        we = ws + timedelta(days=6)

        rows = db_fetchall(
            """SELECT g.id, g.spanish,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                      COALESCE(s.easiness, 2.5)       AS easiness,
                      COALESCE(s.repetitions, 0)      AS repetitions,
                      COALESCE(s.interval_days, 1)    AS interval_days,
                      COALESCE(s.mastered, FALSE)      AS mastered,
                      COALESCE(s.next_review, CURRENT_DATE) AS next_review
               FROM word_groups g
               JOIN words w ON w.group_id = g.id
               LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = %s
               WHERE g.user_id = %s AND g.created_at BETWEEN %s AND %s
               GROUP BY g.id, s.easiness, s.repetitions, s.interval_days, s.mastered, s.next_review""",
            (uid, uid, str(ws), str(we)),
        )
        if not rows:
            return err("No hay palabras esta semana", 404)

        for r in rows:
            ef = float(r["easiness"]); reps = int(r["repetitions"]); nr = str(r["next_review"])
            score = 0
            if nr <= today and not r["mastered"]: score += 100
            score += (2.5 - ef) * 30
            score += max(0, 5 - reps) * 10
            if r["mastered"]: score -= 50
            r["_score"] = score + random.randint(0, 20)

        rows.sort(key=lambda x: x["_score"], reverse=True)

        for r in rows:
            r["english_words"] = _split(r["english_words"])
            r["direction"] = random.choice(["es_en", "en_es"])
            r["question"] = (r["english_words"][random.randint(0, len(r["english_words"]) - 1)] if r["direction"] == "en_es" else r["spanish"])
            interval = int(r["interval_days"]); mastered = bool(r["mastered"]); reps = int(r["repetitions"])
            if mastered: r["srs_level"] = "dominada"
            elif reps == 0: r["srs_level"] = "nueva"
            elif interval <= 3: r["srs_level"] = "aprendiendo"
            elif interval <= 14: r["srs_level"] = "repasando"
            else: r["srs_level"] = "consolidada"
            for k in ("_score", "easiness", "repetitions", "interval_days", "next_review", "mastered"):
                r.pop(k, None)

        return ok({"week_start": str(ws), "week_end": str(we), "questions": rows})

    # GET ?action=check_done
    if method == "GET" and action == "check_done":
        ws_str = request.args.get("week_start")
        ws = ws_str if ws_str else str(date.today() - timedelta(days=date.today().weekday()))
        row = db_fetchone("SELECT id, score, total FROM weekly_tests WHERE user_id = %s AND week_start = %s ORDER BY id DESC LIMIT 1", (uid, ws))
        return ok({"done": bool(row), "score": int(row["score"]) if row else None, "total": int(row["total"]) if row else None})

    # POST ?action=submit
    if method == "POST" and action == "submit":
        b = body()
        today_d = date.today()
        ws = b.get("week_start", str(today_d - timedelta(days=today_d.weekday())))
        answers = b.get("answers", [])
        duration = int(b.get("duration_secs", 0))
        if not answers: return err("Sin respuestas")

        score = 0; results = []
        for ans in answers:
            gid = int(ans.get("group_id", 0))
            direction = ans.get("direction", "es_en")
            answer = ans.get("answer", "").strip()
            question = ans.get("question", "").strip()

            group = db_fetchone(
                """SELECT g.spanish, STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words
                   FROM word_groups g JOIN words w ON w.group_id = g.id
                   WHERE g.id = %s AND g.user_id = %s GROUP BY g.id""",
                (gid, uid),
            )
            if not group: continue

            english_list = _split(group["english_words"])
            spanish = group["spanish"]
            correct = False; feedback = ""

            if answer:
                if direction == "es_en":
                    prompt = f'Palabra español: "{spanish}". Respuesta: "{answer}". Correctas: {", ".join(english_list)}. ¿Correcto? SOLO JSON: {{"correct":true/false,"feedback":"max 15 palabras"}}'
                else:
                    prompt = f'Palabra inglés: "{question}". Respuesta español: "{answer}". Correcta: "{spanish}". ¿Correcto? SOLO JSON: {{"correct":true/false,"feedback":"max 15 palabras"}}'
                raw = groq_call(prompt, 80)
                parsed = parse_groq_json(raw)
                if parsed and "correct" in parsed:
                    correct = bool(parsed["correct"]); feedback = parsed.get("feedback", "")
                if not feedback:
                    correct = answer.lower() in [e.lower() for e in english_list] if direction == "es_en" else answer.lower() == spanish.lower()
                    feedback = "Correcto" if correct else "Incorrecto"
            else:
                feedback = "Sin respuesta"

            if correct: score += 1
            db_exec(
                "INSERT INTO practice_log (user_id, group_id, direction, practice_mode, answer, correct, feedback) VALUES (%s, %s, 'quiz', %s, %s, %s, %s)",
                (uid, gid, direction, answer, correct, f"Quiz: {feedback}"),
            )
            srs_update_quiz(uid, gid, 4 if correct else (2 if answer else 1))
            results.append({"group_id": gid, "correct": correct, "your_answer": answer, "correct_answer": english_list if direction == "es_en" else [spanish], "direction": direction, "question": question, "feedback": feedback})

        db_exec("INSERT INTO weekly_tests (user_id, week_start, score, total) VALUES (%s, %s, %s, %s)", (uid, ws, score, len(answers)))
        try:
            db_exec("INSERT INTO session_history (user_id, session_date, practice_mode, total, correct, duration_secs) VALUES (%s, %s, 'quiz', %s, %s, %s)", (uid, today, len(answers), score, duration or None))
        except Exception: pass

        return ok({"score": score, "total": len(answers), "results": results})

    # GET ?action=history
    if method == "GET" and action == "history":
        rows = db_fetchall("SELECT * FROM weekly_tests WHERE user_id = %s ORDER BY created_at DESC LIMIT 10", (uid,))
        for r in rows:
            if r.get("week_start"): r["week_start"] = str(r["week_start"])
            if r.get("created_at"): r["created_at"] = str(r["created_at"])
        return ok(rows)

    # GET ?action=questions_n
    if method == "GET" and action == "questions_n":
        n = max(1, min(200, int(request.args.get("n", 20))))
        src = request.args.get("src", "all")

        db_exec("INSERT INTO word_srs (user_id, group_id) SELECT %s, g.id FROM word_groups g WHERE g.user_id = %s ON CONFLICT DO NOTHING", (uid, uid))

        base = """SELECT g.id, g.spanish,
                   STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                   COALESCE(s.easiness, 2.5)    AS easiness,
                   COALESCE(s.repetitions, 0)   AS repetitions,
                   COALESCE(s.interval_days, 1) AS interval_days,
                   COALESCE(s.mastered, FALSE)   AS mastered,
                   COALESCE(s.next_review, CURRENT_DATE) AS next_review,
                   CASE WHEN s.mastered THEN 'dominada'
                        WHEN s.repetitions=0 THEN 'nueva'
                        WHEN s.interval_days<=3 THEN 'aprendiendo'
                        WHEN s.interval_days<=14 THEN 'repasando'
                        ELSE 'consolidada' END AS srs_level
                 FROM word_groups g
                 JOIN words w ON w.group_id = g.id
                 LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = %s
                 WHERE g.user_id = %s"""

        if src == "due":
            sql = base + " AND (s.mastered = FALSE OR s.mastered IS NULL) AND (s.next_review <= %s OR s.next_review IS NULL)"
            params = (uid, uid, today)
        elif src == "learning":
            sql = base + " AND s.mastered = FALSE AND s.repetitions >= 1"
            params = (uid, uid)
        elif src == "hard":
            sql = base + " AND (s.easiness < 2.2 OR w.is_hard = TRUE)"
            params = (uid, uid)
        else:
            sql = base; params = (uid, uid)

        sql += f" GROUP BY g.id, g.spanish, s.easiness, s.repetitions, s.interval_days, s.mastered, s.next_review ORDER BY RANDOM() LIMIT {n}"
        rows = db_fetchall(sql, params)
        if not rows: return err("No hay palabras disponibles", 404)

        for r in rows:
            r["english_words"] = _split(r["english_words"])
            r["direction"] = random.choice(["es_en", "en_es"])
            if r.get("next_review"): r["next_review"] = str(r["next_review"])

        return ok({"questions": rows, "total": len(rows)})

    return err("Acción no válida")