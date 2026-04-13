<?php
// ============================================================
//  words.php — CRUD de palabras + reset
// ============================================================
require __DIR__ . '/config.php';

// ── Migrate: add example_sentence column if missing ──────────
try { db()->exec("ALTER TABLE word_groups ADD COLUMN example_sentence TEXT NULL AFTER spanish"); } catch (Throwable $e) {}
// ── Migrate: add category column if missing ───────────────────
try { db()->exec("ALTER TABLE word_groups ADD COLUMN category VARCHAR(80) NOT NULL DEFAULT '' AFTER example_sentence"); } catch (Throwable $e) {}

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';
$uid    = uid();   // ← lanza 401 si no autenticado

// ── GET ?action=list&date=YYYY-MM-DD ────────────────────────
if ($method === 'GET' && $action === 'list') {
    $date = $_GET['date'] ?? date('Y-m-d');
    $stmt = db()->prepare("
        SELECT g.id, g.spanish, g.created_at, g.example_sentence,
               COALESCE(g.category,'') AS category,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
               GROUP_CONCAT(w.is_hard ORDER BY w.id SEPARATOR '||') AS english_diffs
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        WHERE g.user_id = ? AND DATE(g.created_at) = ?
        GROUP BY g.id
        ORDER BY g.id DESC
    ");
    $stmt->execute([$uid, $date]);
    $rows = $stmt->fetchAll();
    foreach ($rows as &$r) {
        $r['english_words'] = explode('||', $r['english_words']);
        $r['english_diffs'] = array_map(fn($v) => $v ? 'hard' : 'normal', explode('||', $r['english_diffs'] ?? ''));
    }
    ok($rows);
}

// ── GET ?action=dates ────────────────────────────────────────
if ($method === 'GET' && $action === 'dates') {
    $stmt = db()->prepare("
        SELECT DATE(created_at) AS date, COUNT(*) AS total
        FROM word_groups
        WHERE user_id = ?
        GROUP BY DATE(created_at)
        ORDER BY DATE(created_at) DESC
    ");
    $stmt->execute([$uid]);
    ok($stmt->fetchAll());
}

// ── GET ?action=week[&week_start=YYYY-MM-DD] ─────────────────
if ($method === 'GET' && $action === 'week') {
    $ws = $_GET['week_start'] ?? date('Y-m-d', strtotime('monday this week'));
    $we = date('Y-m-d', strtotime($ws . ' +6 days'));
    $stmt = db()->prepare("
        SELECT g.id, g.spanish, g.created_at, g.example_sentence,
               COALESCE(g.category,'') AS category,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        WHERE g.user_id = ? AND DATE(g.created_at) BETWEEN ? AND ?
        GROUP BY g.id
        ORDER BY g.created_at, g.id
    ");
    $stmt->execute([$uid, $ws, $we]);
    $rows = $stmt->fetchAll();
    foreach ($rows as &$r) $r['english_words'] = explode('||', $r['english_words']);
    ok($rows);
}

// ── GET ?action=weekly_stats ─────────────────────────────────
if ($method === 'GET' && $action === 'weekly_stats') {
    $stmt = db()->prepare("
        SELECT
            DATE_SUB(created_at, INTERVAL WEEKDAY(created_at) DAY) AS week_start,
            COUNT(*) AS total_groups,
            COUNT(DISTINCT created_at) AS days_active
        FROM word_groups
        WHERE user_id = ?
        GROUP BY week_start
        ORDER BY week_start DESC
        LIMIT 12
    ");
    $stmt->execute([$uid]);
    ok($stmt->fetchAll());
}

// ── GET ?action=categories — Lista de categorías del usuario ─
if ($method === 'GET' && $action === 'categories') {
    try { db()->exec("ALTER TABLE word_groups ADD COLUMN category VARCHAR(80) NOT NULL DEFAULT ''"); } catch (Throwable $e) {}
    $stmt = db()->prepare("
        SELECT COALESCE(category,'') AS category, COUNT(*) AS word_count
        FROM word_groups
        WHERE user_id = ? AND category != ''
        GROUP BY category
        ORDER BY word_count DESC
    ");
    $stmt->execute([$uid]);
    $cats = $stmt->fetchAll();
    ok($cats);
}

// ── GET ?action=by_category&category=X ────────────────────────
if ($method === 'GET' && $action === 'by_category') {
    $cat  = trim($_GET['category'] ?? '');
    if (!$cat) err('category requerido');
    $stmt = db()->prepare("
        SELECT g.id, g.spanish, g.created_at, COALESCE(g.category,'') AS category,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
               GROUP_CONCAT(w.is_hard ORDER BY w.id SEPARATOR '||') AS english_diffs
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        WHERE g.user_id = ? AND g.category = ?
        GROUP BY g.id
        ORDER BY g.id DESC
    ");
    $stmt->execute([$uid, $cat]);
    $rows = $stmt->fetchAll();
    foreach ($rows as &$r) {
        $r['english_words'] = explode('||', $r['english_words']);
        $r['english_diffs'] = array_map(fn($v) => $v ? 'hard' : 'normal', explode('||', $r['english_diffs'] ?? ''));
    }
    ok($rows);
}

// ── GET ?action=search&q=TEXTO[&lang=both|es|en] ─────────────
if ($method === 'GET' && $action === 'search') {
    $q    = trim($_GET['q'] ?? '');
    $lang = $_GET['lang'] ?? 'both';
    if ($q === '')            err('Parámetro q requerido');
    if (mb_strlen($q) < 2)   err('Mínimo 2 caracteres para buscar');
    $like = '%' . $q . '%';
    if ($lang === 'es')       { $where = 'g.spanish LIKE ?';                $params = [$uid, $like]; }
    elseif ($lang === 'en')   { $where = 'w.english LIKE ?';                $params = [$uid, $like]; }
    else                      { $where = '(g.spanish LIKE ? OR w.english LIKE ?)'; $params = [$uid, $like, $like]; }

    $stmt = db()->prepare("
        SELECT DISTINCT g.id, g.spanish, g.created_at,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        WHERE g.user_id = ? AND $where
        GROUP BY g.id
        ORDER BY g.created_at DESC, g.id DESC
        LIMIT 100
    ");
    $stmt->execute($params);
    $rows = $stmt->fetchAll();
    foreach ($rows as &$r) $r['english_words'] = explode('||', $r['english_words']);
    ok(['query' => $q, 'total' => count($rows), 'results' => $rows]);
}

// ── GET ?action=distractors&group_id=X&date=YYYY-MM-DD ───────
if ($method === 'GET' && $action === 'distractors') {
    $gid  = (int)($_GET['group_id'] ?? 0);
    $date = $_GET['date'] ?? date('Y-m-d');
    if (!$gid) err('group_id requerido');

    $stmt = db()->prepare("
        SELECT g.id, g.spanish,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        WHERE g.user_id = ? AND g.id != ?
        GROUP BY g.id
        ORDER BY ABS(DATEDIFF(g.created_at, ?)), RAND()
        LIMIT 3
    ");
    $stmt->execute([$uid, $gid, $date]);
    $rows = $stmt->fetchAll();
    foreach ($rows as &$r) $r['english_words'] = explode('||', $r['english_words']);
    ok($rows);
}

// ── POST ?action=validate — IA valida cada palabra individualmente ─
if ($method === 'POST' && $action === 'validate') {
    $b       = body();
    $spanish = trim($b['spanish'] ?? '');
    $english = array_values(array_filter(array_map('trim', $b['english'] ?? [])));
    if (!$spanish || !count($english)) err('Faltan campos');

    // Validate each English word individually against the Spanish meaning
    $word_results = [];
    $has_invalid  = false;

    foreach ($english as $en_word) {
        $prompt = "Eres un asistente de vocabulario estricto. El estudiante quiere registrar: español=\"$spanish\", inglés=\"$en_word\".\n" .
                  "Determina si esta traducción específica es correcta o razonablemente válida.\n" .
                  "REGLAS:\n" .
                  "- valid=false si la palabra inglesa es inventada, sin sentido, o claramente incorrecta.\n" .
                  "- valid=false si no hay relación semántica entre \"$spanish\" y \"$en_word\".\n" .
                  "- valid=true solo si la traducción es correcta o es un sinónimo cercano reconocido.\n" .
                  "Responde SOLO JSON sin markdown: {\"valid\":true/false,\"warning\":\"\",\"suggestion\":\"\"}\n" .
                  "- warning: vacío si válida, descripción del error en español (máx 15 palabras)\n" .
                  "- suggestion: traducción correcta en inglés si hay error, sino vacío";

        $raw    = groq_validate($prompt);
        $result = ['word' => $en_word, 'valid' => true, 'warning' => '', 'suggestion' => ''];

        if ($raw) {
            $clean  = preg_replace('/```json|```/', '', $raw);
            $parsed = json_decode(trim($clean), true);
            if (isset($parsed['valid'])) {
                $result['valid']      = (bool)$parsed['valid'];
                $result['warning']    = $parsed['warning']    ?? '';
                $result['suggestion'] = $parsed['suggestion'] ?? '';
            }
        }

        $word_results[] = $result;
        if (!$result['valid']) $has_invalid = true;
    }

    // Build summary response
    if (!$has_invalid) {
        ok(['valid' => true, 'warning' => '', 'suggestion' => '', 'word_results' => $word_results]);
    } else {
        // Collect invalid words for summary message
        $invalid_words  = array_filter($word_results, fn($r) => !$r['valid']);
        $invalid_labels = array_map(fn($r) => "\"{$r['word']}\"", $invalid_words);
        $warning        = count($invalid_labels) === 1
            ? "La palabra " . implode(', ', $invalid_labels) . " parece incorrecta"
            : "Las palabras " . implode(', ', $invalid_labels) . " parecen incorrectas";
        ok(['valid' => false, 'warning' => $warning, 'suggestion' => '', 'word_results' => $word_results]);
    }
}

// ── POST ?action=add ─────────────────────────────────────────
if ($method === 'POST' && $action === 'add') {
    $b        = body();
    $spanish  = mb_strtolower(trim($b['spanish'] ?? ''), 'UTF-8');
    $category = trim($b['category'] ?? '');
    $rawEnglish = $b['english'] ?? [];
    $english = [];
    foreach ($rawEnglish as $entry) {
        if (is_array($entry)) {
            $word = mb_strtolower(trim($entry['word'] ?? ''), 'UTF-8');
            $diff = in_array($entry['difficulty'] ?? '', ['normal','hard']) ? $entry['difficulty'] : 'normal';
        } else {
            $word = mb_strtolower(trim($entry), 'UTF-8');
            $diff = 'normal';
        }
        if ($word) $english[] = ['word' => $word, 'difficulty' => $diff];
    }
    $seen = []; $english = array_values(array_filter($english, function($e) use (&$seen) {
        if (in_array($e['word'], $seen)) return false;
        $seen[] = $e['word']; return true;
    }));
    if (!$spanish)           err('El español es requerido');
    if (!count($english))    err('Al menos una palabra en inglés');

    $pdo = db();
    $dup = $pdo->prepare("SELECT id, created_at FROM word_groups WHERE user_id = ? AND spanish = ? LIMIT 1");
    $dup->execute([$uid, $spanish]);
    $existing = $dup->fetch();
    if ($existing) ok(['duplicate' => true, 'group_id' => (int)$existing['id']]);

    $words_only   = array_column($english, 'word');
    $placeholders = implode(',', array_fill(0, count($words_only), '?'));
    $dupEn = $pdo->prepare("
        SELECT w.english FROM words w
        JOIN word_groups g ON g.id = w.group_id
        WHERE g.user_id = ? AND w.english IN ($placeholders)
    ");
    $dupEn->execute(array_merge([$uid], $words_only));
    $duplicatesEn = $dupEn->fetchAll(PDO::FETCH_COLUMN);
    if ($duplicatesEn) ok(['duplicate' => true, 'duplicate_en' => $duplicatesEn]);

    $pdo->beginTransaction();
    try {
        $today = date('Y-m-d');
        $s = $pdo->prepare("INSERT INTO word_groups (user_id, spanish, created_at, category) VALUES (?, ?, ?, ?)");
        $s->execute([$uid, $spanish, $today, $category]);
        $gid = $pdo->lastInsertId();
        $sw  = $pdo->prepare("INSERT INTO words (group_id, english, is_hard) VALUES (?, ?, ?)");
        foreach ($english as $en) $sw->execute([$gid, $en['word'], $en['difficulty']==='hard'?1:0]);
        $pdo->commit();

        // Generate and save example sentence asynchronously (non-blocking on failure)
        try {
            $en_list   = implode(', ', array_column($english, 'word'));
            $prompt    = "You are an English tutor. Create ONE short natural English sentence (10-15 words) using the word \"{$english[0]['word']}\". The sentence should clearly show the word's meaning (Spanish: \"$spanish\"). Reply ONLY with a JSON object, no markdown: {\"sentence\":\"the English sentence\",\"translation\":\"Spanish translation of the sentence\"}";
            $sentence_raw = groq_validate($prompt);
            if ($sentence_raw) {
                $clean = preg_replace('/```json|```/', '', $sentence_raw);
                $parsed = json_decode(trim($clean), true);
                if (!empty($parsed['sentence'])) {
                    $example = $parsed['sentence'];
                    if (!empty($parsed['translation'])) $example .= ' — ' . $parsed['translation'];
                    $pdo->prepare("UPDATE word_groups SET example_sentence = ? WHERE id = ?")->execute([$example, $gid]);
                }
            }
        } catch (Throwable $ignored) {}

        ok(['group_id' => (int)$gid]);
    } catch (Throwable $e) { $pdo->rollBack(); err('Error al guardar: ' . $e->getMessage(), 500); }
}

// ── POST ?action=set_word_diff ───────────────────────────────
if ($method === 'POST' && $action === 'set_word_diff') {
    $b          = body();
    $gid        = (int)($b['group_id']   ?? 0);
    $word_index = (int)($b['word_index'] ?? 0);
    $difficulty = in_array($b['difficulty'] ?? '', ['normal','hard']) ? $b['difficulty'] : 'normal';
    if (!$gid) err('group_id requerido');

    $check = db()->prepare("SELECT id FROM word_groups WHERE id = ? AND user_id = ?");
    $check->execute([$gid, $uid]);
    if (!$check->fetch()) err('Grupo no encontrado', 404);

    $stmt = db()->prepare("SELECT id FROM words WHERE group_id = ? ORDER BY id ASC LIMIT 1 OFFSET ?");
    $stmt->execute([$gid, $word_index]);
    $word = $stmt->fetch();
    if (!$word) err('Palabra no encontrada', 404);

    db()->prepare("UPDATE words SET is_hard = ? WHERE id = ?")->execute([$difficulty==='hard'?1:0, $word['id']]);
    ok(['word_id' => $word['id'], 'difficulty' => $difficulty, 'is_hard' => $difficulty==='hard'?1:0]);
}

// ── DELETE ?action=delete&id=5 ───────────────────────────────
if ($method === 'DELETE' && $action === 'delete') {
    $id = (int)($_GET['id'] ?? 0);
    if (!$id) err('ID requerido');
    $stmt = db()->prepare("DELETE FROM word_groups WHERE id = ? AND user_id = ?");
    $stmt->execute([$id, $uid]);
    if ($stmt->rowCount() === 0) err('Grupo no encontrado', 404);
    ok(null);
}

// ── POST ?action=reset ───────────────────────────────────────
if ($method === 'POST' && $action === 'reset') {
    $b     = body();
    $scope = $b['scope'] ?? '';
    $value = $b['value'] ?? '';
    $pdo   = db();

    if ($scope === 'all') {
        $pdo->prepare("DELETE FROM word_groups  WHERE user_id = ?")->execute([$uid]);
        $pdo->prepare("DELETE FROM practice_log WHERE user_id = ?")->execute([$uid]);
        $pdo->prepare("DELETE FROM weekly_tests WHERE user_id = ?")->execute([$uid]);
        ok(['deleted' => 'all']);

    } elseif ($scope === 'week' && $value) {
        $we = date('Y-m-d', strtotime($value . ' +6 days'));
        $ids = $pdo->prepare("SELECT id FROM word_groups WHERE user_id = ? AND created_at BETWEEN ? AND ?");
        $ids->execute([$uid, $value, $we]);
        $gids = $ids->fetchAll(PDO::FETCH_COLUMN);
        if ($gids) {
            $ph = implode(',', array_fill(0, count($gids), '?'));
            $pdo->prepare("DELETE FROM word_groups WHERE id IN ($ph)")->execute($gids);
        }
        $pdo->prepare("DELETE FROM weekly_tests WHERE user_id = ? AND week_start = ?")->execute([$uid, $value]);
        ok(['deleted' => 'week', 'week_start' => $value, 'groups_removed' => count($gids ?? [])]);

    } elseif ($scope === 'date' && $value) {
        $ids = $pdo->prepare("SELECT id FROM word_groups WHERE user_id = ? AND created_at = ?");
        $ids->execute([$uid, $value]);
        $gids = $ids->fetchAll(PDO::FETCH_COLUMN);
        if ($gids) {
            $ph = implode(',', array_fill(0, count($gids), '?'));
            $pdo->prepare("DELETE FROM word_groups WHERE id IN ($ph)")->execute($gids);
        }
        ok(['deleted' => 'date', 'date' => $value, 'groups_removed' => count($gids ?? [])]);

    } else {
        err('scope inválido. Usa: all | week (con value=YYYY-MM-DD) | date (con value=YYYY-MM-DD)');
    }
}

// ── POST ?action=add_synonym ──────────────────────────────────
if ($method === 'POST' && $action === 'add_synonym') {
    $b        = body();
    $group_id = (int)($b['group_id'] ?? 0);
    $word     = trim($b['word'] ?? '');
    if (!$group_id || !$word) err('Faltan campos');
    // Verify group belongs to user
    $st = db()->prepare("SELECT id FROM word_groups WHERE id = ? AND user_id = ?");
    $st->execute([$group_id, $uid]);
    if (!$st->fetch()) err('Grupo no encontrado', 404);
    // Check not duplicate
    $st2 = db()->prepare("SELECT id FROM words WHERE group_id = ? AND LOWER(english) = LOWER(?)");
    $st2->execute([$group_id, $word]);
    if ($st2->fetch()) ok(['added' => false, 'message' => 'Ya existe']);
    // Add
    db()->prepare("INSERT INTO words (group_id, english, is_hard) VALUES (?, ?, 0)")->execute([$group_id, $word]);
    ok(['added' => true]);
}

// ── Groq helper ───────────────────────────────────────────────
function groq_validate(string $prompt): string|false {
    $payload = json_encode([
        'model'       => GROQ_MODEL,
        'messages'    => [['role' => 'user', 'content' => $prompt]],
        'max_tokens'  => 100,
        'temperature' => 0.1,
    ]);
    $ch = curl_init('https://api.groq.com/openai/v1/chat/completions');
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $payload,
        CURLOPT_TIMEOUT        => 8,
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