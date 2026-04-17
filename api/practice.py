"""
practice.py — Práctica diaria + streak + edit (PostgreSQL)
"""
from datetime import date, timedelta
from flask import Blueprint, request, g
from config import (
    ok, err, body, db_exec, db_fetchall, db_fetchone,
    db_insert, db_update, groq_call, parse_groq_json, require_auth, get_db, today_col
)

practice_bp = Blueprint("practice", __name__)


def srs_update(uid: int, group_id: int, correct: bool):
    try:
        db_exec("INSERT INTO word_srs (user_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (uid, group_id))
        srs = db_fetchone(
            "SELECT easiness, interval_days, repetitions FROM word_srs WHERE user_id = %s AND group_id = %s",
            (uid, group_id),
        )
        if not srs:
            return
        ef = float(srs["easiness"])
        interval = int(srs["interval_days"])
        reps = int(srs["repetitions"])
        quality = 4 if correct else 1

        if quality >= 3:
            if reps == 0: interval = 1
            elif reps == 1: interval = 6
            else: interval = round(interval * ef)
            reps += 1
            ef = max(1.3, ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        else:
            reps = 0
            interval = 1
            ef = max(1.3, ef - 0.2)

        interval = min(365, max(1, interval))
        next_review = str(today_col() + timedelta(days=interval))
        mastered = interval >= 21 and correct
        db_update(
            "UPDATE word_srs SET easiness=%s, interval_days=%s, repetitions=%s, next_review=%s, last_review=%s, mastered=%s WHERE user_id=%s AND group_id=%s",
            (ef, interval, reps, next_review, str(today_col()), mastered, uid, group_id),
        )
    except Exception:
        pass


def _split(val):
    return val.split("||") if val else []


@practice_bp.route("/practice", methods=["GET", "POST", "OPTIONS"])
@require_auth
def practice():
    if request.method == "OPTIONS":
        return "", 204

    uid = g.uid
    method = request.method
    action = request.args.get("action", "")

    # GET ?action=random_ids
    if method == "GET" and action == "random_ids":
        ids = [int(x) for x in request.args.get("ids", "").split(",") if x.strip().isdigit()]
        seen = [int(x) for x in request.args.get("seen", "").split(",") if x.strip().isdigit()]
        if not ids:
            return err("No hay IDs", 400)
        unseen = [i for i in ids if i not in seen] or ids
        ph = ",".join(["%s"] * len(unseen))
        row = db_fetchone(
            f"""SELECT g.id, g.spanish, g.created_at, g.example_sentence,
                       STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                       STRING_AGG(w.is_hard::text, '||' ORDER BY w.id) AS english_diffs
                FROM word_groups g JOIN words w ON w.group_id = g.id
                WHERE g.user_id = %s AND g.id IN ({ph})
                GROUP BY g.id ORDER BY RANDOM() LIMIT 1""",
            tuple([uid] + unseen),
        )
        if not row:
            return err("No hay palabras disponibles", 404)
        row["english_words"] = _split(row["english_words"])
        row["english_diffs"] = _split(row.get("english_diffs") or "")
        row["total_day"] = len(ids)
        if row.get("created_at"):
            row["created_at"] = str(row["created_at"])
        return ok(row)

    # GET ?action=random
    if method == "GET" and action == "random":
        import random as _random
        seen = [int(x) for x in request.args.get("seen", "").split(",") if x.strip().isdigit()]
        per_day = max(0, int(request.args.get("per_day", 0)))

        all_words_mode = not request.args.get("date") and not request.args.get("dates")

        if request.args.get("dates"):
            import re
            raw_dates = [d.strip() for d in request.args.get("dates", "").split(",")]
            dates = [d for d in raw_dates if re.match(r"^\d{4}-\d{2}-\d{2}$", d)]
        elif all_words_mode:
            dates = []
        else:
            dates = [request.args.get("date", str(today_col()))]

        if not all_words_mode and not dates:
            return err("Fechas inválidas", 400)

        eligible_ids = []
        total_count = 0

        if all_words_mode:
            all_ids = [r["id"] for r in db_fetchall(
                "SELECT id FROM word_groups WHERE user_id = %s ORDER BY id ASC", (uid,)
            )]
            eligible_ids = all_ids
            total_count = len(all_ids)
            if total_count == 0:
                return err("No hay palabras disponibles", 404)
        else:
            pass  # el for de abajo lo maneja

        for d in dates:
            day_ids = [r["id"] for r in db_fetchall(
                "SELECT id FROM word_groups WHERE user_id = %s AND created_at = %s ORDER BY id ASC", (uid, d)
            )]
            if not day_ids:
                continue
            if per_day > 0 and len(day_ids) > per_day:
                _random.shuffle(day_ids)
                day_ids = day_ids[:per_day]
            eligible_ids.extend(day_ids)
            total_count += len(day_ids)

        if total_count == 0:
            return err("No hay palabras para los días seleccionados", 404)

        unseen = [i for i in eligible_ids if i not in seen] or eligible_ids
        ph = ",".join(["%s"] * len(unseen))
        all_rows = db_fetchall(
            f"""SELECT g.id, g.spanish, g.created_at, g.example_sentence,
                       STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                       STRING_AGG(w.is_hard::text, '||' ORDER BY w.id) AS english_diffs
                FROM word_groups g JOIN words w ON w.group_id = g.id
                WHERE g.user_id = %s AND g.id IN ({ph})
                GROUP BY g.id ORDER BY RANDOM() LIMIT 20""",
            tuple([uid] + unseen),
        )
        if not all_rows:
            return err("No hay palabras disponibles", 404)

        pool = []
        for row in all_rows:
            diffs = _split(row.get("english_diffs") or "")
            pool.append(row)
            if "true" in diffs or "t" in diffs:
                pool.append(row)

        row = _random.choice(pool)
        row["english_words"] = _split(row["english_words"])
        row["english_diffs"] = _split(row.get("english_diffs") or "")
        row["total_day"] = total_count
        if row.get("created_at"):
            row["created_at"] = str(row["created_at"])
        return ok(row)

    # POST ?action=check
    if method == "POST" and action == "check":
        try:
            b = body()
            group_id = int(b.get("group_id", 0))
            direction = b.get("direction", "")
            answer = b.get("answer", "").strip()
            question = b.get("question", "").strip()
            mode = b.get("mode", "type")
            if not all([group_id, direction, answer, question]):
                return err("Faltan campos requeridos")

            group = db_fetchone(
                """SELECT g.spanish, STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words
                   FROM word_groups g JOIN words w ON w.group_id = g.id
                   WHERE g.id = %s AND g.user_id = %s GROUP BY g.id""",
                (group_id, uid),
            )
            if not group:
                return err("Grupo no encontrado", 404)

            english_list = _split(group["english_words"])
            spanish = group["spanish"]

            if direction == "es_en":
                correct_str = ", ".join(english_list)
                prompt = (
                    f'La palabra en español es: "{spanish}". '
                    f'Las traducciones correctas al inglés son: {correct_str}. '
                    f'El estudiante respondió: "{answer}". '
                    f'Evalúa si la respuesta es correcta. '
                    f'REGLAS ESTRICTAS: '
                    f'1. La respuesta debe ser la misma palabra o un sinónimo muy cercano (igual significado y registro). '
                    f'2. Una palabra completamente diferente es INCORRECTA aunque esté relacionada. '
                    f'3. Acepta variaciones menores de ortografía solo si la palabra es claramente reconocible. '
                    f'4. NO aceptes palabras que solo comparten la raíz o campo semántico. '
                    f'Responde SOLO JSON: {{"correct":true/false,"feedback":"explicación breve en español de máximo 20 palabras"}}'
                )
            else:
                prompt = (
                    f'La(s) palabra(s) en inglés era(n): "{question}". '
                    f'La traducción correcta al español es: "{spanish}". '
                    f'El estudiante respondió: "{answer}". '
                    f'Evalúa si la respuesta es correcta. '
                    f'REGLAS ESTRICTAS: '
                    f'1. La respuesta debe ser exactamente "{spanish}" o un sinónimo directo (misma palabra, mismo significado). '
                    f'2. Si "{answer}" es una palabra diferente aunque relacionada, es INCORRECTA. '
                    f'3. El orden de las palabras importa si son frases. '
                    f'4. NO aceptes paráfrasis ni palabras del mismo campo semántico. '
                    f'5. Solo acepta si es claramente la misma palabra o sinónimo perfecto. '
                    f'Responde SOLO JSON: {{"correct":true/false,"feedback":"explicación breve en español de máximo 20 palabras"}}'
                )

            raw = groq_call(prompt, 120)
            parsed = parse_groq_json(raw)
            if parsed and "correct" in parsed:
                correct = bool(parsed["correct"])
                feedback = parsed.get("feedback", "")
            else:
                correct = answer.lower() == (english_list[0] if direction == "es_en" else spanish).lower()
                feedback = "Correcto" if correct else "Incorrecto"

            db_exec(
                "INSERT INTO practice_log (user_id, group_id, direction, answer, correct, feedback, practice_mode) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uid, group_id, direction, answer, correct, feedback, mode),
            )
            srs_update(uid, group_id, correct)
            return ok({"correct": correct, "feedback": feedback, "correct_answer": english_list if direction == "es_en" else [spanish]})
        except Exception as e:
            return err(f"Error interno: {e}", 500)

    # POST ?action=check_multi
    if method == "POST" and action == "check_multi":
        try:
            b = body()
            group_id = int(b.get("group_id", 0))
            direction = b.get("direction", "")
            answer = b.get("answer", "").strip()
            question = b.get("question", "").strip()
            mode = b.get("mode", "type")
            if not all([group_id, direction, answer, question]):
                return err("Faltan campos requeridos")

            group = db_fetchone(
                """SELECT g.spanish, STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words
                   FROM word_groups g JOIN words w ON w.group_id = g.id
                   WHERE g.id = %s AND g.user_id = %s GROUP BY g.id""",
                (group_id, uid),
            )
            if not group:
                return err("Grupo no encontrado", 404)

            english_list = _split(group["english_words"])
            spanish = group["spanish"]
            import re
            user_parts = [p.strip() for p in re.split(r"[,/]", answer) if p.strip()]
            correct_str = ", ".join(english_list)

            prompt = (
                f'La palabra en español es "{spanish}". '
                f'Tiene exactamente {len(english_list)} significado(s) en inglés: {correct_str}.\n'
                f'El estudiante escribió: "{answer}".\n'
                f'REGLAS ESTRICTAS:\n'
                f'1. El estudiante debe escribir TODOS los significados correctos.\n'
                f'2. Cada palabra escrita debe corresponder exactamente a uno de los significados (o ser sinónimo perfecto).\n'
                f'3. Si escribe palabras adicionales incorrectas, es INCORRECTO.\n'
                f'4. Si falta algún significado, es INCORRECTO.\n'
                f'5. El orden no importa, pero cada palabra debe ser correcta individualmente.\n'
                f'6. NO aceptes palabras relacionadas o del mismo campo semántico como correctas.\n'
                f'Responde SOLO JSON: {{"correct":true/false,"feedback":"qué faltó o estuvo mal, en español, máximo 25 palabras"}}'
            )
            raw = groq_call(prompt, 150)
            parsed = parse_groq_json(raw)
            if parsed and "correct" in parsed:
                eval_result = parsed
            else:
                user_lc = [x.lower() for x in user_parts]
                expected_lc = [x.lower() for x in english_list]
                correct_flag = set(user_lc) == set(expected_lc)
                eval_result = {"correct": correct_flag, "feedback": "Correcto" if correct_flag else f"Debes escribir: {correct_str}"}

            db_exec(
                "INSERT INTO practice_log (user_id, group_id, direction, answer, correct, feedback, practice_mode) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uid, group_id, direction, answer, bool(eval_result["correct"]), eval_result.get("feedback", ""), mode),
            )
            srs_update(uid, group_id, bool(eval_result["correct"]))
            return ok({"correct": bool(eval_result["correct"]), "feedback": eval_result.get("feedback", ""), "correct_answer": english_list if direction == "es_en" else [spanish]})
        except Exception as e:
            return err(f"Error interno: {e}", 500)

    # POST ?action=edit
    if method == "POST" and action == "edit":
        b = body()
        gid = int(b.get("group_id", 0))
        spanish = b.get("spanish", "").strip().lower()
        english = list(set(filter(None, [e.strip().lower() for e in b.get("english", [])])))
        difficulties = b.get("difficulties", [])
        if not gid: return err("ID requerido")
        if not spanish: return err("El español es requerido")
        if not english: return err("Al menos una palabra en inglés")

        exists = db_fetchone("SELECT id FROM word_groups WHERE id = %s AND user_id = %s", (gid, uid))
        if not exists: return err("Grupo no encontrado", 404)
        dup = db_fetchone("SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s AND id != %s LIMIT 1", (uid, spanish, gid))
        if dup: return err(f'Ya existe "{spanish}" en otro grupo', 409)

        from config import get_db
        conn = get_db()
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE word_groups SET spanish = %s WHERE id = %s", (spanish, gid))
                cur.execute("DELETE FROM words WHERE group_id = %s", (gid,))
                for i, en in enumerate(english):
                    diff = difficulties[i] if i < len(difficulties) and difficulties[i] in ("normal", "hard") else "normal"
                    cur.execute("INSERT INTO words (group_id, english, is_hard) VALUES (%s, %s, %s)", (gid, en, diff == "hard"))
            conn.commit()
            conn.autocommit = True
            return ok({"group_id": gid})
        except Exception as e:
            conn.rollback()
            conn.autocommit = True
            return err(f"Error al editar: {e}", 500)

    # GET ?action=stats
    if method == "GET" and action == "stats":
        d = request.args.get("date", str(today_col()))
        row = db_fetchone(
            "SELECT COUNT(*) AS total_attempts, SUM(correct::int) AS correct_count FROM practice_log WHERE user_id = %s AND DATE(created_at) = %s",
            (uid, d),
        )
        return ok(row)

    # GET ?action=streak
    if method == "GET" and action == "streak":
        days = [str(r["created_at"]) for r in db_fetchall(
            "SELECT created_at FROM word_groups WHERE user_id = %s GROUP BY created_at ORDER BY created_at DESC", (uid,)
        )]
        streak = 0
        check = str(today_col())
        for day in days:
            if day == check:
                streak += 1
                check = str(date.fromisoformat(check) - timedelta(days=1))
            elif day < check:
                break
        best = 0
        current = 1
        for i in range(1, len(days)):
            diff = (date.fromisoformat(days[i - 1]) - date.fromisoformat(days[i])).days
            if diff == 1:
                current += 1
                best = max(best, current)
            else:
                current = 1
        best = max(best, streak)
        return ok({"streak": streak, "best": best})

    # GET ?action=word_accuracy
    if method == "GET" and action == "word_accuracy":
        rows = db_fetchall(
            """SELECT pl.group_id, g.spanish,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                      COUNT(*) AS total,
                      SUM(pl.correct::int) AS correct_count,
                      ROUND(SUM(pl.correct::int) * 100.0 / COUNT(*), 0) AS accuracy
               FROM practice_log pl
               JOIN word_groups g ON g.id = pl.group_id
               JOIN words w ON w.group_id = pl.group_id
               WHERE pl.user_id = %s
               GROUP BY pl.group_id, g.spanish
               HAVING COUNT(*) >= 3
               ORDER BY accuracy ASC, total DESC
               LIMIT 50""",
            (uid,),
        )
        for r in rows:
            r["english_words"] = _split(r["english_words"])
        return ok(rows)

    # POST ?action=hint
    if method == "POST" and action == "hint":
        b = body()
        prompt = b.get("prompt", "").strip()
        if not prompt:
            return err("prompt requerido")
        raw = groq_call(prompt, 500)
        if not raw:
            return err("No se pudo generar la pista", 503)
        return ok({"hint": raw.strip()})

    return err("Acción no válida")