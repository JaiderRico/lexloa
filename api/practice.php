<?php
// ============================================================
//  practice.php — Práctica diaria + streak + edit
// ============================================================
require __DIR__ . '/config.php';

// ── Migrate: add practice_mode column if missing ─────────────
try {
    db()->exec("ALTER TABLE practice_log ADD COLUMN practice_mode VARCHAR(20) NULL AFTER direction");
} catch (Throwable $e) { /* already exists */ }

// ── SRS auto-update helper ────────────────────────────────────
function srs_update(int $uid, int $group_id, bool $correct): void {
    try {
        db()->exec("
            CREATE TABLE IF NOT EXISTS word_srs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL, group_id INT NOT NULL,
                easiness FLOAT NOT NULL DEFAULT 2.5,
                `interval` INT NOT NULL DEFAULT 1,
                repetitions INT NOT NULL DEFAULT 0,
                next_review DATE NOT NULL DEFAULT '2000-01-01',
                last_review DATE NULL,
                mastered TINYINT(1) NOT NULL DEFAULT 0,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_user_group (user_id, group_id),
                FOREIGN KEY (group_id) REFERENCES word_groups(id) ON DELETE CASCADE
            )
        ");
        db()->prepare("INSERT IGNORE INTO word_srs (user_id, group_id) VALUES (?, ?)")
            ->execute([$uid, $group_id]);
        $s = db()->prepare("SELECT easiness, `interval`, repetitions FROM word_srs WHERE user_id = ? AND group_id = ?");
        $s->execute([$uid, $group_id]);
        $srs = $s->fetch();
        $ef = (float)$srs['easiness']; $int = (int)$srs['`interval`']; $reps = (int)$srs['repetitions'];
        $quality = $correct ? 4 : 1; // simplified: 4=correct, 1=wrong
        if ($quality >= 3) {
            if ($reps === 0) $int = 1;
            elseif ($reps === 1) $int = 6;
            else $int = (int)round($int * $ef);
            $reps++;
            $ef = max(1.3, $ef + (0.1 - (5 - $quality) * (0.08 + (5 - $quality) * 0.02)));
        } else {
            $reps = 0; $int = 1; $ef = max(1.3, $ef - 0.2);
        }
        $int = min(365, max(1, $int));
        $next = date('Y-m-d', strtotime("+{$int} days"));
        $mastered = ($int >= 21 && $correct) ? 1 : 0;
        db()->prepare("UPDATE word_srs SET easiness=?,`interval`=?,repetitions=?,next_review=?,last_review=?,mastered=? WHERE user_id=? AND group_id=?")
            ->execute([$ef, $int, $reps, $next, date('Y-m-d'), $mastered, $uid, $group_id]);
    } catch (Throwable $e) { /* silently ignore SRS errors */ }
}

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';
$uid    = uid();

// ── GET ?action=random_ids&ids=1,2,3&seen=1,2 ────────────────
// Practice from a specific list of group IDs (used by SRS filter)
if ($method === 'GET' && $action === 'random_ids') {
    $ids  = array_values(array_filter(array_map('intval', explode(',', $_GET['ids'] ?? ''))));
    $seen = array_filter(array_map('intval', explode(',', $_GET['seen'] ?? '')));
    if (empty($ids)) err('No hay IDs', 400);

    $unseen = array_values(array_diff($ids, $seen));
    if (empty($unseen)) { $seen = []; $unseen = $ids; }

    $ph     = implode(',', array_fill(0, count($unseen), '?'));
    $params = array_merge([$uid], $unseen);

    $stmt = db()->prepare("
        SELECT g.id, g.spanish, g.created_at, g.example_sentence,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
               GROUP_CONCAT(w.is_hard ORDER BY w.id SEPARATOR '||') AS english_diffs
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        WHERE g.user_id = ? AND g.id IN ($ph)
        GROUP BY g.id
        ORDER BY RAND()
        LIMIT 1
    ");
    $stmt->execute($params);
    $row = $stmt->fetch();
    if (!$row) err('No hay palabras disponibles', 404);

    $row['english_words'] = explode('||', $row['english_words']);
    $row['english_diffs'] = explode('||', $row['english_diffs'] ?? '');
    $row['total_day']     = count($ids);
    ok($row);
}

// ── GET ?action=random&date=YYYY-MM-DD[&dates=d1,d2,...][&seen=1,2,3][&per_day=N] ──
if ($method === 'GET' && $action === 'random') {
    $seen    = array_filter(array_map('intval', explode(',', $_GET['seen'] ?? '')));
    $perDay  = max(1, (int)($_GET['per_day'] ?? 0)); // 0 = sin límite
    $pdo     = db();

    // Support multi-date: &dates=2024-01-01,2024-01-02  OR legacy single &date=
    if (!empty($_GET['dates'])) {
        $rawDates = array_filter(array_map('trim', explode(',', $_GET['dates'])));
        // Validate format
        $dates = array_values(array_filter($rawDates, fn($d) => preg_match('/^\d{4}-\d{2}-\d{2}$/', $d)));
    } else {
        $dates = [$_GET['date'] ?? date('Y-m-d')];
    }
    if (empty($dates)) err('Fechas inválidas', 400);

    // Build pool of eligible group IDs per date respecting per_day limit
    $eligibleIds = [];
    $totalCount  = 0;
    foreach ($dates as $d) {
        // Fetch IDs for this day ordered deterministically then shuffled server-side
        $stmt = $pdo->prepare("SELECT id FROM word_groups WHERE user_id = ? AND created_at = ? ORDER BY id ASC");
        $stmt->execute([$uid, $d]);
        $dayIds = $stmt->fetchAll(PDO::FETCH_COLUMN);
        if (empty($dayIds)) continue;
        // Apply per_day limit (slice after shuffle keeps randomness)
        if ($perDay > 0 && count($dayIds) > $perDay) {
            shuffle($dayIds);
            $dayIds = array_slice($dayIds, 0, $perDay);
        }
        $eligibleIds = array_merge($eligibleIds, $dayIds);
        $totalCount += count($dayIds);
    }

    if ($totalCount === 0) err('No hay palabras para los días seleccionados', 404);

    // Remove already-seen; reset if full cycle done
    $unseen = array_values(array_diff($eligibleIds, $seen));
    if (empty($unseen)) { $seen = []; $unseen = $eligibleIds; }

    $ph     = implode(',', array_fill(0, count($unseen), '?'));
    $params = array_merge([$uid], $unseen);

    $stmt = $pdo->prepare("
        SELECT g.id, g.spanish, g.created_at, g.example_sentence,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
               GROUP_CONCAT(w.is_hard ORDER BY w.id SEPARATOR '||') AS english_diffs
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        WHERE g.user_id = ? AND g.id IN ($ph)
        GROUP BY g.id
        ORDER BY RAND()
        LIMIT 20
    ");
    $stmt->execute($params);
    $all = $stmt->fetchAll();
    if (!$all) err('No hay palabras disponibles', 404);

    $pool = [];
    foreach ($all as $row) {
        $diffs  = explode('||', $row['english_diffs'] ?? '');
        $pool[] = $row;
        if (in_array('hard', $diffs)) $pool[] = $row;
    }
    $row = $pool[array_rand($pool)];
    $row['english_words'] = explode('||', $row['english_words']);
    $row['english_diffs'] = explode('||', $row['english_diffs'] ?? '');
    $row['total_day']     = $totalCount;
    ok($row);
}

// ── POST ?action=check ────────────────────────────────────────
if ($method === 'POST' && $action === 'check') {
    try {
        $b         = body();
        $group_id  = (int)($b['group_id'] ?? 0);
        $direction = $b['direction'] ?? '';
        $answer    = trim($b['answer'] ?? '');
        $question  = trim($b['question'] ?? '');
        $mode      = $b['mode'] ?? 'type';
        if (!$group_id || !$direction || !$answer || !$question) err('Faltan campos requeridos');

        $stmt = db()->prepare("
            SELECT g.spanish,
                   GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words
            FROM word_groups g
            JOIN words w ON w.group_id = g.id
            WHERE g.id = ? AND g.user_id = ?
            GROUP BY g.id
        ");
        $stmt->execute([$group_id, $uid]);
        $group = $stmt->fetch();
        if (!$group) err('Grupo no encontrado', 404);

        $english_list = explode('||', $group['english_words']);
        $spanish      = $group['spanish'];

        if ($direction === 'es_en') {
            $correct_str = implode(', ', $english_list);
            $prompt = "Palabra en español: \"$spanish\". El estudiante respondió: \"$answer\". Respuestas correctas: $correct_str. ¿Es correcta? Acepta sinónimos cercanos, ignora tildes menores. Responde SOLO JSON: {\"correct\":true/false,\"feedback\":\"explicación breve en español de máximo 20 palabras\"}";
        } else {
            $prompt = "La palabra en inglés era: \"$question\". El estudiante respondió en español: \"$answer\". Respuesta correcta: \"$spanish\". ¿Es correcta? Acepta sinónimos muy cercanos. Responde SOLO JSON: {\"correct\":true/false,\"feedback\":\"explicación breve en español de máximo 20 palabras\"}";
        }

        $groq_resp = groq_call($prompt);
        if (!$groq_resp) {
            $correct = strtolower($answer) === strtolower($direction === 'es_en' ? $english_list[0] : $spanish);
            $eval    = ['correct' => $correct, 'feedback' => $correct ? 'Correcto' : 'Incorrecto'];
        } else {
            $clean = preg_replace('/```json|```/', '', $groq_resp);
            $eval  = json_decode(trim($clean), true);
            if (!isset($eval['correct'])) {
                $correct = strtolower($answer) === strtolower($direction === 'es_en' ? $english_list[0] : $spanish);
                $eval    = ['correct' => $correct, 'feedback' => $correct ? 'Correcto' : 'Incorrecto'];
            }
        }

        db()->prepare("INSERT INTO practice_log (user_id, group_id, direction, answer, correct, feedback, practice_mode) VALUES (?, ?, ?, ?, ?, ?, ?)")
            ->execute([$uid, $group_id, $direction, $answer, (int)$eval['correct'], $eval['feedback'], $mode]);
        srs_update($uid, $group_id, (bool)$eval['correct']);

        ok(['correct'        => (bool)$eval['correct'],
            'feedback'       => $eval['feedback'],
            'correct_answer' => $direction === 'es_en' ? $english_list : [$spanish]]);
    } catch (Throwable $e) { err("Error interno: " . $e->getMessage(), 500); }
}

// ── POST ?action=check_multi — verifica que el user escribió TODOS los significados ──
// Body: { group_id, direction, answer, question, expected_count }
// answer: comma/slash-separated string of all expected English words (or all spanish)
if ($method === 'POST' && $action === 'check_multi') {
    try {
        $b              = body();
        $group_id       = (int)($b['group_id'] ?? 0);
        $direction      = $b['direction'] ?? '';
        $answer         = trim($b['answer'] ?? '');
        $question       = trim($b['question'] ?? '');
        $expected_count = (int)($b['expected_count'] ?? 1);
        $mode           = $b['mode'] ?? 'type';
        if (!$group_id || !$direction || !$answer || !$question) err('Faltan campos requeridos');

        $stmt = db()->prepare("
            SELECT g.spanish,
                   GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words
            FROM word_groups g
            JOIN words w ON w.group_id = g.id
            WHERE g.id = ? AND g.user_id = ?
            GROUP BY g.id
        ");
        $stmt->execute([$group_id, $uid]);
        $group = $stmt->fetch();
        if (!$group) err('Grupo no encontrado', 404);

        $english_list = explode('||', $group['english_words']);
        $spanish      = $group['spanish'];

        // Parse user's answer into individual words
        $user_parts = array_filter(array_map('trim', preg_split('/[,\/]/', $answer)));

        $correct_str  = implode(', ', $english_list);
        $user_answers = implode(', ', $user_parts);

        $prompt = "La palabra en español es \"$spanish\". Tiene los siguientes significados en inglés: $correct_str.\n" .
                  "El estudiante escribió: \"$answer\".\n" .
                  "¿Escribió TODOS los significados correctamente (en cualquier orden)? Acepta variaciones menores de ortografía y sinónimos muy cercanos.\n" .
                  "Responde SOLO JSON: {\"correct\":true/false,\"feedback\":\"qué faltó o estuvo mal, en español, máximo 25 palabras\"}";

        $groq_resp = groq_call($prompt, 150);
        $eval      = ['correct' => false, 'feedback' => 'Verifica que hayas escrito todos los significados'];

        if ($groq_resp) {
            $clean  = preg_replace('/```json|```/', '', $groq_resp);
            $parsed = json_decode(trim($clean), true);
            if (isset($parsed['correct'])) $eval = $parsed;
        } else {
            // Fallback: set comparison
            $user_lc     = array_map('strtolower', $user_parts);
            $expected_lc = array_map('strtolower', $english_list);
            $correct     = count(array_intersect($user_lc, $expected_lc)) === count($expected_lc)
                        && count($user_lc) === count($expected_lc);
            $eval = [
                'correct'  => $correct,
                'feedback' => $correct ? 'Correcto' : "Debes escribir: $correct_str"
            ];
        }

        // Log to practice_log
        db()->prepare("INSERT INTO practice_log (user_id, group_id, direction, answer, correct, feedback, practice_mode) VALUES (?, ?, ?, ?, ?, ?, ?)")
            ->execute([$uid, $group_id, $direction, $answer, (int)$eval['correct'], $eval['feedback'] ?? '', $mode]);
        srs_update($uid, $group_id, (bool)$eval['correct']);

        ok([
            'correct'        => (bool)$eval['correct'],
            'feedback'       => $eval['feedback'] ?? '',
            'correct_answer' => $direction === 'es_en' ? $english_list : [$spanish],
        ]);
    } catch (Throwable $e) { err("Error interno: " . $e->getMessage(), 500); }
}

// ── POST ?action=edit ─────────────────────────────────────────
if ($method === 'POST' && $action === 'edit') {
    $b       = body();
    $gid     = (int)($b['group_id'] ?? 0);
    $spanish = mb_strtolower(trim($b['spanish'] ?? ''), 'UTF-8');
    $english = array_values(array_unique(array_filter(array_map(
        fn($e) => mb_strtolower(trim($e), 'UTF-8'), $b['english'] ?? []
    ))));
    $difficulties = $b['difficulties'] ?? [];
    if (!$gid)            err('ID requerido');
    if (!$spanish)        err('El español es requerido');
    if (!count($english)) err('Al menos una palabra en inglés');

    $pdo = db();
    $exists = $pdo->prepare("SELECT id FROM word_groups WHERE id = ? AND user_id = ?");
    $exists->execute([$gid, $uid]);
    if (!$exists->fetch()) err('Grupo no encontrado', 404);

    $dup = $pdo->prepare("SELECT id FROM word_groups WHERE user_id = ? AND spanish = ? AND id != ? LIMIT 1");
    $dup->execute([$uid, $spanish, $gid]);
    if ($dup->fetch()) err("Ya existe \"$spanish\" en otro grupo", 409);

    $pdo->beginTransaction();
    try {
        $pdo->prepare("UPDATE word_groups SET spanish = ? WHERE id = ?")->execute([$spanish, $gid]);
        $pdo->prepare("DELETE FROM words WHERE group_id = ?")->execute([$gid]);
        $sw = $pdo->prepare("INSERT INTO words (group_id, english, is_hard) VALUES (?, ?, ?)");
        foreach ($english as $i => $en) {
            $diff = in_array($difficulties[$i] ?? '', ['normal','hard']) ? $difficulties[$i] : 'normal';
            $sw->execute([$gid, $en, $diff]);
        }
        $pdo->commit();
        ok(['group_id' => $gid]);
    } catch (Throwable $e) { $pdo->rollBack(); err('Error al editar: ' . $e->getMessage(), 500); }
}

// ── GET ?action=stats&date=YYYY-MM-DD ─────────────────────────
if ($method === 'GET' && $action === 'stats') {
    $date = $_GET['date'] ?? date('Y-m-d');
    $stmt = db()->prepare("
        SELECT COUNT(*) AS total_attempts, SUM(correct) AS correct_count
        FROM practice_log
        WHERE user_id = ? AND DATE(created_at) = ?
    ");
    $stmt->execute([$uid, $date]);
    ok($stmt->fetch());
}

// ── GET ?action=streak ────────────────────────────────────────
if ($method === 'GET' && $action === 'streak') {
    $stmt = db()->prepare("
        SELECT created_at FROM word_groups
        WHERE user_id = ?
        GROUP BY created_at
        ORDER BY created_at DESC
    ");
    $stmt->execute([$uid]);
    $days = $stmt->fetchAll(PDO::FETCH_COLUMN);

    $streak = 0;
    $check  = date('Y-m-d');
    foreach ($days as $day) {
        if ($day === $check) { $streak++; $check = date('Y-m-d', strtotime($check . ' -1 day')); }
        elseif ($day < $check) break;
    }
    $best = 0; $current = 1;
    for ($i = 1; $i < count($days); $i++) {
        $diff = (strtotime($days[$i-1]) - strtotime($days[$i])) / 86400;
        if ($diff === 1.0) { $current++; $best = max($best, $current); }
        else $current = 1;
    }
    $best = max($best, $streak);
    ok(['streak' => $streak, 'best' => $best]);
}

// ── GET ?action=word_accuracy ──────────────────────────────────
if ($method === 'GET' && $action === 'word_accuracy') {
    $stmt = db()->prepare("
        SELECT
            pl.group_id,
            g.spanish,
            GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
            COUNT(*)                                               AS total,
            SUM(pl.correct)                                        AS correct_count,
            ROUND(SUM(pl.correct) * 100.0 / COUNT(*), 0)          AS accuracy
        FROM practice_log pl
        JOIN word_groups g ON g.id = pl.group_id
        JOIN words w       ON w.group_id = pl.group_id
        WHERE pl.user_id = ?
        GROUP BY pl.group_id, g.spanish
        HAVING total >= 3
        ORDER BY accuracy ASC, total DESC
        LIMIT 50
    ");
    $stmt->execute([$uid]);
    $rows = $stmt->fetchAll();
    foreach ($rows as &$r) $r['english_words'] = explode('||', $r['english_words']);
    ok($rows);
}

// ── POST ?action=hint ─────────────────────────────────────────
if ($method === 'POST' && $action === 'hint') {
    $b      = body();
    $prompt = trim($b['prompt'] ?? '');
    if (!$prompt) err('prompt requerido');
    $raw = groq_call($prompt, 500);
    if (!$raw) err('No se pudo generar la pista', 503);
    ok(['hint' => trim($raw)]);
}

// ── GROQ ──────────────────────────────────────────────────────
function groq_call(string $prompt, int $max_tokens = 120): string|false {
    $payload = json_encode(['model' => GROQ_MODEL, 'messages' => [['role' => 'user', 'content' => $prompt]], 'max_tokens' => $max_tokens, 'temperature' => 0.1]);
    $ch = curl_init('https://api.groq.com/openai/v1/chat/completions');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $payload,
        CURLOPT_TIMEOUT        => 10,
        CURLOPT_HTTPHEADER     => [
            'Content-Type: application/json',
            'Authorization: Bearer ' . GROQ_KEY,
        ],
    ]);
    $raw = curl_exec($ch);
    curl_close($ch);
    if (!$raw) return false;
    $resp = json_decode($raw, true);
    return $resp['choices'][0]['message']['content'] ?? false;
}

err('Acción no válida');