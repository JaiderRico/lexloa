<?php
// ============================================================
//  stats.php — Estadísticas por modo + Export/Import
// ============================================================
require __DIR__ . '/config.php';

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';
$uid    = uid();

// ── GET ?action=by_mode ───────────────────────────────────────
if ($method === 'GET' && $action === 'by_mode') {
    // practice_log.direction encodes mode as "es_en|mode:timer" etc.
    // We log mode in a new column — but for backward compat, we also analyze existing logs
    $stmt = db()->prepare("
        SELECT
            direction,
            COUNT(*) AS total,
            SUM(correct) AS correct_count,
            ROUND(SUM(correct) * 100.0 / COUNT(*), 1) AS accuracy_pct
        FROM practice_log
        WHERE user_id = ?
        GROUP BY direction
        ORDER BY total DESC
    ");
    $stmt->execute([$uid]);
    ok($stmt->fetchAll());
}

// ── GET ?action=mode_breakdown ────────────────────────────────
if ($method === 'GET' && $action === 'mode_breakdown') {
    // Stats per practice mode (type/multiple/timer/scramble/match)
    $stmt = db()->prepare("
        SELECT
            COALESCE(practice_mode, 'type') AS mode,
            COUNT(*) AS total,
            SUM(correct) AS correct_count,
            ROUND(SUM(correct) * 100.0 / COUNT(*), 1) AS accuracy_pct,
            MAX(DATE(created_at)) AS last_used
        FROM practice_log
        WHERE user_id = ?
        GROUP BY practice_mode
        ORDER BY total DESC
    ");
    $stmt->execute([$uid]);
    $rows = $stmt->fetchAll();

    // Labels
    $labels = [
        'type'     => ['name' => 'Escribir',        'icon' => '⌨'],
        'multiple' => ['name' => 'Opción múltiple',  'icon' => '◉'],
        'timer'    => ['name' => 'Contrarreloj',     'icon' => '⏱'],
        'scramble' => ['name' => 'Ordenar letras',   'icon' => '🔀'],
        'match'    => ['name' => 'Emparejar',        'icon' => '🔗'],
    ];

    foreach ($rows as &$r) {
        $m = $r['mode'];
        $r['label'] = $labels[$m]['name'] ?? $m;
        $r['icon']  = $labels[$m]['icon'] ?? '●';
    }
    ok($rows);
}

// ── GET ?action=heatmap[&months=6] ───────────────────────────
if ($method === 'GET' && $action === 'heatmap') {
    $months = min(12, max(1, (int)($_GET['months'] ?? 6)));
    $from   = date('Y-m-d', strtotime("-{$months} months"));

    // Practice activity
    $stmt = db()->prepare("
        SELECT DATE(created_at) AS day, COUNT(*) AS attempts, SUM(correct) AS correct
        FROM practice_log
        WHERE user_id = ? AND DATE(created_at) >= ?
        GROUP BY day
    ");
    $stmt->execute([$uid, $from]);
    $practice = [];
    foreach ($stmt->fetchAll() as $r) $practice[$r['day']] = $r;

    // Words added
    $stmt2 = db()->prepare("
        SELECT created_at AS day, COUNT(*) AS added
        FROM word_groups
        WHERE user_id = ? AND created_at >= ?
        GROUP BY created_at
    ");
    $stmt2->execute([$uid, $from]);
    $added = [];
    foreach ($stmt2->fetchAll() as $r) $added[$r['day']] = (int)$r['added'];

    // Merge by day
    $allDays = array_unique(array_merge(array_keys($practice), array_keys($added)));
    sort($allDays);

    $result = [];
    foreach ($allDays as $day) {
        $p = $practice[$day] ?? ['attempts' => 0, 'correct' => 0];
        $result[] = [
            'date'     => $day,
            'attempts' => (int)$p['attempts'],
            'correct'  => (int)$p['correct'],
            'added'    => $added[$day] ?? 0,
        ];
    }
    ok($result);
}

// ── GET ?action=export ────────────────────────────────────────
if ($method === 'GET' && $action === 'export') {
    // Export all user words as JSON
    $stmt = db()->prepare("
        SELECT g.id, g.spanish, g.created_at, g.example_sentence,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
               GROUP_CONCAT(w.is_hard ORDER BY w.id SEPARATOR '||') AS english_diffs
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        WHERE g.user_id = ?
        GROUP BY g.id
        ORDER BY g.created_at, g.id
    ");
    $stmt->execute([$uid]);
    $rows = $stmt->fetchAll();

    $export = [];
    foreach ($rows as $r) {
        $words = explode('||', $r['english_words']);
        $diffs = explode('||', $r['english_diffs'] ?? '');
        $en = [];
        foreach ($words as $i => $w) {
            $en[] = ['word' => $w, 'difficulty' => $diffs[$i] ?? 'normal'];
        }
        $export[] = [
            'spanish'    => $r['spanish'],
            'english'    => $en,
            'created_at' => $r['created_at'],
        ];
    }

    // Return as downloadable JSON
    header('Content-Type: application/json; charset=utf-8');
    header('Content-Disposition: attachment; filename="vocab_export_' . date('Y-m-d') . '.json"');
    echo json_encode(['version' => 2, 'exported_at' => date('c'), 'words' => $export], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
    exit;
}

// ── POST ?action=import ───────────────────────────────────────
if ($method === 'POST' && $action === 'import') {
    $b    = body();
    $data = $b['data'] ?? null;  // parsed JSON object

    if (!$data || empty($data['words'])) err('Datos de importación inválidos');

    $words   = $data['words'];
    $pdo     = db();
    $added   = 0;
    $skipped = 0;
    $errors  = [];
    $group_ids = [];

    $pdo->beginTransaction();
    try {
        foreach ($words as $entry) {
            $spanish = mb_strtolower(trim($entry['spanish'] ?? ''), 'UTF-8');
            if (!$spanish) { $skipped++; continue; }

            // Check duplicate
            $dup = $pdo->prepare("SELECT id FROM word_groups WHERE user_id = ? AND spanish = ? LIMIT 1");
            $dup->execute([$uid, $spanish]);
            if ($dup->fetch()) { $skipped++; continue; }

            // Parse english entries
            $english = [];
            foreach ($entry['english'] ?? [] as $e) {
                $word = is_array($e) ? mb_strtolower(trim($e['word'] ?? ''), 'UTF-8') : mb_strtolower(trim($e), 'UTF-8');
                $diff = is_array($e) ? ($e['difficulty'] ?? 'normal') : 'normal';
                if ($word) $english[] = ['word' => $word, 'difficulty' => $diff];
            }
            if (empty($english)) { $skipped++; continue; }

            // Use original created_at if valid, else today
            $createdAt = $entry['created_at'] ?? date('Y-m-d');
            if (!preg_match('/^\d{4}-\d{2}-\d{2}/', $createdAt)) $createdAt = date('Y-m-d');
            $createdAt = substr($createdAt, 0, 10);

            $ins = $pdo->prepare("INSERT INTO word_groups (user_id, spanish, created_at, example_sentence) VALUES (?, ?, ?, ?)");
            $exampleSentence = $entry['example_sentence'] ?? null;
            $ins->execute([$uid, $spanish, $createdAt, $exampleSentence]);
            $gid = $pdo->lastInsertId();

            $sw = $pdo->prepare("INSERT INTO words (group_id, english, is_hard) VALUES (?, ?, ?)");
            foreach ($english as $en) $sw->execute([$gid, $en['word'], $en['difficulty']==='hard'?1:0]);

            $added++;
        }
        $pdo->commit();
    } catch (Throwable $e) {
        $pdo->rollBack();
        err('Error durante importación: ' . $e->getMessage(), 500);
    }

    ok(['added' => $added, 'skipped' => $skipped, 'total' => count($words)]);
}

// ── GET ?action=full_summary ──────────────────────────────────
if ($method === 'GET' && $action === 'full_summary') {
    $uid_p = $uid;

    // Total words
    $total = db()->prepare("SELECT COUNT(*) FROM word_groups WHERE user_id = ?");
    $total->execute([$uid_p]);

    // Total practice sessions
    $sessions = db()->prepare("SELECT COUNT(*), SUM(correct) FROM practice_log WHERE user_id = ?");
    $sessions->execute([$uid_p]);
    $sess = $sessions->fetch(PDO::FETCH_NUM);

    // Best streak
    $days = db()->prepare("SELECT created_at FROM word_groups WHERE user_id = ? GROUP BY created_at ORDER BY created_at DESC");
    $days->execute([$uid_p]);
    $dayList = $days->fetchAll(PDO::FETCH_COLUMN);

    $streak = 0; $check = date('Y-m-d');
    foreach ($dayList as $day) {
        if ($day === $check) { $streak++; $check = date('Y-m-d', strtotime($check . ' -1 day')); }
        elseif ($day < $check) break;
    }

    ok([
        'total_words'    => (int)$total->fetchColumn(),
        'total_attempts' => (int)($sess[0] ?? 0),
        'total_correct'  => (int)($sess[1] ?? 0),
        'current_streak' => $streak,
    ]);
}

// ── GET ?action=session_history[&days=30] ─────────────────────
if ($method === 'GET' && $action === 'session_history') {
    $days = min(365, max(7, (int)($_GET['days'] ?? 30)));
    $from = date('Y-m-d', strtotime("-{$days} days"));

    // Per-day summary from practice_log
    $stmt = db()->prepare("
        SELECT
            DATE(pl.created_at)                   AS session_date,
            COALESCE(pl.practice_mode, 'type')    AS mode,
            COUNT(*)                               AS total,
            SUM(pl.correct)                        AS correct,
            ROUND(SUM(pl.correct)*100.0/COUNT(*),1) AS accuracy_pct,
            MIN(pl.created_at)                     AS started_at,
            MAX(pl.created_at)                     AS ended_at
        FROM practice_log pl
        WHERE pl.user_id = ? AND DATE(pl.created_at) >= ?
        GROUP BY DATE(pl.created_at), COALESCE(pl.practice_mode,'type')
        ORDER BY session_date DESC, total DESC
    ");
    $stmt->execute([$uid, $from]);
    $rows = $stmt->fetchAll();

    // Also get words added per day
    $stmt2 = db()->prepare("
        SELECT created_at AS day, COUNT(*) AS words_added
        FROM word_groups WHERE user_id = ? AND created_at >= ?
        GROUP BY created_at ORDER BY created_at DESC
    ");
    $stmt2->execute([$uid, $from]);
    $wordsAdded = [];
    foreach ($stmt2->fetchAll() as $r) $wordsAdded[$r['day']] = (int)$r['words_added'];

    // Group by date
    $byDate = [];
    foreach ($rows as $r) {
        $d = $r['session_date'];
        if (!isset($byDate[$d])) {
            $byDate[$d] = [
                'date'        => $d,
                'words_added' => $wordsAdded[$d] ?? 0,
                'modes'       => [],
                'total'       => 0,
                'correct'     => 0,
            ];
        }
        $byDate[$d]['modes'][] = [
            'mode'         => $r['mode'],
            'total'        => (int)$r['total'],
            'correct'      => (int)$r['correct'],
            'accuracy_pct' => (float)$r['accuracy_pct'],
        ];
        $byDate[$d]['total']   += (int)$r['total'];
        $byDate[$d]['correct'] += (int)$r['correct'];
    }

    $result = array_values($byDate);
    foreach ($result as &$d) {
        $d['accuracy_pct'] = $d['total'] > 0 ? round($d['correct'] / $d['total'] * 100, 1) : 0;
    }

    ok($result);
}

err('Acción no válida', 400);