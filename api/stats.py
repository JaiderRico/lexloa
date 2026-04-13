"""
stats.py — Estadísticas + Export/Import (PostgreSQL)
"""
import json
from datetime import date, timedelta
from flask import Blueprint, request, g, Response
from config import (
    ok, err, body, db_fetchall, db_fetchone,
    db_exec, db_update, require_auth, get_db
)

stats_bp = Blueprint("stats", __name__)


@stats_bp.route("/stats", methods=["GET", "POST", "OPTIONS"])
@require_auth
def stats():
    if request.method == "OPTIONS":
        return "", 204

    uid = g.uid
    method = request.method
    action = request.args.get("action", "")

    if method == "GET" and action == "by_mode":
        rows = db_fetchall(
            """SELECT direction, COUNT(*) AS total, SUM(correct::int) AS correct_count,
                      ROUND(SUM(correct::int) * 100.0 / COUNT(*), 1) AS accuracy_pct
               FROM practice_log WHERE user_id = %s GROUP BY direction ORDER BY total DESC""",
            (uid,),
        )
        return ok(rows)

    if method == "GET" and action == "mode_breakdown":
        rows = db_fetchall(
            """SELECT COALESCE(practice_mode, 'type') AS mode,
                      COUNT(*) AS total, SUM(correct::int) AS correct_count,
                      ROUND(SUM(correct::int) * 100.0 / COUNT(*), 1) AS accuracy_pct,
                      MAX(DATE(created_at)) AS last_used
               FROM practice_log WHERE user_id = %s GROUP BY practice_mode ORDER BY total DESC""",
            (uid,),
        )
        labels = {
            "type": {"name": "Escribir", "icon": "⌨"},
            "multiple": {"name": "Opción múltiple", "icon": "◉"},
            "timer": {"name": "Contrarreloj", "icon": "⏱"},
            "scramble": {"name": "Ordenar letras", "icon": "🔀"},
            "match": {"name": "Emparejar", "icon": "🔗"},
        }
        for r in rows:
            m = r["mode"]
            r["label"] = labels.get(m, {}).get("name", m)
            r["icon"] = labels.get(m, {}).get("icon", "●")
            if r.get("last_used"): r["last_used"] = str(r["last_used"])
        return ok(rows)

    if method == "GET" and action == "heatmap":
        months = min(12, max(1, int(request.args.get("months", 6))))
        from_date = str(date.today() - timedelta(days=months * 30))
        practice_rows = db_fetchall(
            "SELECT DATE(created_at) AS day, COUNT(*) AS attempts, SUM(correct::int) AS correct FROM practice_log WHERE user_id = %s AND DATE(created_at) >= %s GROUP BY day",
            (uid, from_date),
        )
        practice = {str(r["day"]): r for r in practice_rows}
        added_rows = db_fetchall(
            "SELECT created_at AS day, COUNT(*) AS added FROM word_groups WHERE user_id = %s AND created_at >= %s GROUP BY created_at",
            (uid, from_date),
        )
        added = {str(r["day"]): int(r["added"]) for r in added_rows}
        all_days = sorted(set(list(practice.keys()) + list(added.keys())))
        result = [{"date": d, "attempts": int(practice.get(d, {}).get("attempts", 0)), "correct": int(practice.get(d, {}).get("correct", 0)), "added": added.get(d, 0)} for d in all_days]
        return ok(result)

    if method == "GET" and action == "export":
        rows = db_fetchall(
            """SELECT g.id, g.spanish, g.created_at, g.example_sentence,
                      STRING_AGG(w.english, '||' ORDER BY w.id) AS english_words,
                      STRING_AGG(w.is_hard::text, '||' ORDER BY w.id) AS english_diffs
               FROM word_groups g JOIN words w ON w.group_id = g.id
               WHERE g.user_id = %s GROUP BY g.id ORDER BY g.created_at, g.id""",
            (uid,),
        )
        export = []
        for r in rows:
            wlist = r["english_words"].split("||") if r["english_words"] else []
            diffs = r["english_diffs"].split("||") if r.get("english_diffs") else []
            en = [{"word": w, "difficulty": "hard" if diffs[i] in ("true","t") else "normal"} for i, w in enumerate(wlist)]
            export.append({"spanish": r["spanish"], "english": en, "created_at": str(r["created_at"])})
        payload = json.dumps({"version": 2, "exported_at": date.today().isoformat(), "words": export}, ensure_ascii=False, indent=2)
        return Response(payload, mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="vocab_export_{date.today()}.json"'})

    if method == "POST" and action == "import":
        b = body()
        data = b.get("data")
        if not data or not data.get("words"): return err("Datos de importación inválidos")

        import re
        conn = get_db()
        conn.autocommit = False
        added = 0; skipped = 0
        try:
            for entry in data["words"]:
                spanish = entry.get("spanish", "").strip().lower()
                if not spanish: skipped += 1; continue
                dup = db_fetchone("SELECT id FROM word_groups WHERE user_id = %s AND spanish = %s LIMIT 1", (uid, spanish))
                if dup: skipped += 1; continue
                english = []
                for e in entry.get("english", []):
                    word = e.get("word","").strip().lower() if isinstance(e,dict) else str(e).strip().lower()
                    diff = (e.get("difficulty","normal") if isinstance(e,dict) else "normal")
                    if word: english.append({"word": word, "difficulty": diff})
                if not english: skipped += 1; continue
                created_at = entry.get("created_at", str(date.today()))
                if not re.match(r"^\d{4}-\d{2}-\d{2}", created_at): created_at = str(date.today())
                created_at = created_at[:10]
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO word_groups (user_id, spanish, created_at, example_sentence) VALUES (%s, %s, %s, %s) RETURNING id", (uid, spanish, created_at, entry.get("example_sentence")))
                    gid = cur.fetchone()["id"]
                    for en in english:
                        cur.execute("INSERT INTO words (group_id, english, is_hard) VALUES (%s, %s, %s)", (gid, en["word"], en["difficulty"]=="hard"))
                added += 1
            conn.commit(); conn.autocommit = True
        except Exception as e:
            conn.rollback(); conn.autocommit = True
            return err(f"Error durante importación: {e}", 500)
        return ok({"added": added, "skipped": skipped, "total": len(data["words"])})

    if method == "GET" and action == "full_summary":
        total_row = db_fetchone("SELECT COUNT(*) AS cnt FROM word_groups WHERE user_id = %s", (uid,))
        sess_row = db_fetchone("SELECT COUNT(*) AS total, SUM(correct::int) AS correct FROM practice_log WHERE user_id = %s", (uid,))
        days = [str(r["created_at"]) for r in db_fetchall("SELECT created_at FROM word_groups WHERE user_id = %s GROUP BY created_at ORDER BY created_at DESC", (uid,))]
        streak = 0; check = str(date.today())
        for day in days:
            if day == check: streak += 1; check = str(date.fromisoformat(check) - timedelta(days=1))
            elif day < check: break
        return ok({"total_words": int(total_row["cnt"]) if total_row else 0, "total_attempts": int(sess_row["total"]) if sess_row else 0, "total_correct": int(sess_row["correct"] or 0) if sess_row else 0, "current_streak": streak})

    if method == "GET" and action == "session_history":
        days = min(365, max(7, int(request.args.get("days", 30))))
        from_date = str(date.today() - timedelta(days=days))
        rows = db_fetchall(
            """SELECT DATE(pl.created_at) AS session_date, COALESCE(pl.practice_mode, 'type') AS mode,
                      COUNT(*) AS total, SUM(pl.correct::int) AS correct,
                      ROUND(SUM(pl.correct::int)*100.0/COUNT(*),1) AS accuracy_pct
               FROM practice_log pl
               WHERE pl.user_id = %s AND DATE(pl.created_at) >= %s
               GROUP BY DATE(pl.created_at), COALESCE(pl.practice_mode,'type')
               ORDER BY session_date DESC, total DESC""",
            (uid, from_date),
        )
        added_rows = db_fetchall("SELECT created_at AS day, COUNT(*) AS words_added FROM word_groups WHERE user_id = %s AND created_at >= %s GROUP BY created_at ORDER BY created_at DESC", (uid, from_date))
        words_added = {str(r["day"]): int(r["words_added"]) for r in added_rows}
        by_date = {}
        for r in rows:
            d = str(r["session_date"])
            if d not in by_date:
                by_date[d] = {"date": d, "words_added": words_added.get(d, 0), "modes": [], "total": 0, "correct": 0}
            by_date[d]["modes"].append({"mode": r["mode"], "total": int(r["total"]), "correct": int(r["correct"]), "accuracy_pct": float(r["accuracy_pct"])})
            by_date[d]["total"] += int(r["total"]); by_date[d]["correct"] += int(r["correct"])
        result = list(by_date.values())
        for d in result:
            d["accuracy_pct"] = round(d["correct"] / d["total"] * 100, 1) if d["total"] > 0 else 0
        return ok(result)

    return err("Acción no válida", 400)