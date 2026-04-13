<?php
// ============================================================
//  srs.php — Spaced Repetition System (algoritmo SM-2 simplificado)
//  Tablas necesarias: word_srs
// ============================================================
require __DIR__ . '/config.php';

// Capturar errores fatales y retornar JSON
set_exception_handler(function(Throwable $e) {
    http_response_code(500);
    echo json_encode([
        'ok'    => false,
        'error' => $e->getMessage(),
        'file'  => basename($e->getFile()),
        'line'  => $e->getLine(),
    ]);
    exit;
});

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';
$uid    = uid();  // lanza 401 si no autenticado — antes de tocar la DB

// Compatibilidad MySQL 5.7 ONLY_FULL_GROUP_BY
try { db()->exec("SET SESSION sql_mode = (SELECT REPLACE(@@SESSION.sql_mode,'ONLY_FULL_GROUP_BY',''))"); } catch (Throwable $e) {}


// ── GET ?action=debug ─────────────────────────────────────────
// Diagnóstico temporal — REMOVER EN PRODUCCIÓN
if ($method === 'GET' && $action === 'debug') {
    $steps = [];
    // Step 1: tabla word_srs existe?
    try {
        $r = db()->query("SELECT COUNT(*) FROM word_srs WHERE user_id = $uid");
        $steps['word_srs_count'] = $r->fetchColumn();
    } catch (Throwable $e) { $steps['word_srs_error'] = $e->getMessage(); }
    // Step 2: tabla word_groups existe?
    try {
        $r = db()->query("SELECT COUNT(*) FROM word_groups WHERE user_id = $uid");
        $steps['word_groups_count'] = $r->fetchColumn();
    } catch (Throwable $e) { $steps['word_groups_error'] = $e->getMessage(); }
    // Step 3: tabla words existe?
    try {
        $r = db()->query("SELECT COUNT(*) FROM words");
        $steps['words_count'] = $r->fetchColumn();
    } catch (Throwable $e) { $steps['words_error'] = $e->getMessage(); }
    // Step 4: probar el INSERT IGNORE
    try {
        db()->prepare("INSERT IGNORE INTO word_srs (user_id, group_id) SELECT ?, g.id FROM word_groups g WHERE g.user_id = ?")->execute([$uid, $uid]);
        $steps['insert_ok'] = true;
    } catch (Throwable $e) { $steps['insert_error'] = $e->getMessage(); }
    // Step 5: probar la query principal
    try {
        $stmt = db()->prepare("SELECT g.id FROM word_srs s JOIN word_groups g ON g.id = s.group_id JOIN words w ON w.group_id = g.id WHERE s.user_id = ? AND s.mastered = 0 LIMIT 1");
        $stmt->execute([$uid]);
        $steps['main_query_ok'] = true;
    } catch (Throwable $e) { $steps['main_query_error'] = $e->getMessage(); }
    ok($steps);
}


// Ensure word_srs table exists (safe to run every request, after auth)
try {
    db()->exec("
        CREATE TABLE IF NOT EXISTS word_srs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            group_id INT NOT NULL,
            easiness FLOAT NOT NULL DEFAULT 2.5,
            `interval` INT NOT NULL DEFAULT 1,
            repetitions INT NOT NULL DEFAULT 0,
            next_review DATE NOT NULL DEFAULT '2000-01-01',
            last_quality INT NULL,
            mastered TINYINT(1) NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_user_group (user_id, group_id),
            FOREIGN KEY (group_id) REFERENCES word_groups(id) ON DELETE CASCADE
        )
    ");
} catch (Throwable $e) { /* already exists or unsupported syntax — ignore */ }

// Fix any rows where next_review is null or zero date
try {
    db()->exec("UPDATE word_srs SET next_review = CURDATE() WHERE next_review IS NULL OR next_review = '0000-00-00' OR next_review = '2000-01-01'");
} catch (Throwable $e) {}

// Fix DEFAULT on next_review column for MySQL 5.7 compatibility
try {
    db()->exec("ALTER TABLE word_srs ALTER COLUMN next_review SET DEFAULT '2025-01-01'");
} catch (Throwable $e) {}

// ── GET ?action=due[&date=YYYY-MM-DD][&limit=N] ───────────────
// Devuelve palabras que toca repasar hoy según SRS
if ($method === 'GET' && $action === 'due') {
    $date  = $_GET['date']  ?? date('Y-m-d');
    $limit = min(100, max(1, (int)($_GET['limit'] ?? 20)));

    // Inicializar entradas SRS para palabras que no tienen todavía
    try {
        db()->prepare("
            INSERT IGNORE INTO word_srs (user_id, group_id)
            SELECT ?, g.id FROM word_groups g WHERE g.user_id = ?
        ")->execute([$uid, $uid]);
    } catch (Throwable $e) { /* word_groups vacío o FK issue — continuar */ }

    $stmt = db()->prepare("
        SELECT
            g.id, g.spanish, g.created_at,
            GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
            GROUP_CONCAT(w.is_hard ORDER BY w.id SEPARATOR '||') AS english_diffs,
            s.easiness, s.`interval`, s.repetitions, s.next_review,
            s.mastered
        FROM word_srs s
        JOIN word_groups g ON g.id = s.group_id
        JOIN words w       ON w.group_id = g.id
        WHERE s.user_id = ? AND s.next_review <= ? AND s.mastered = 0
        GROUP BY g.id, g.spanish, g.created_at, s.easiness, s.`interval`, s.repetitions,
                 s.next_review, s.mastered
        ORDER BY s.next_review ASC, s.easiness ASC
        LIMIT ?
    ");
    $stmt->execute([$uid, $date, $limit]);
    $rows = $stmt->fetchAll();

    foreach ($rows as &$r) {
        $r['english_words'] = explode('||', $r['english_words']);
        $r['english_diffs'] = array_map(fn($v) => $v ? 'hard' : 'normal', explode('||', $r['english_diffs'] ?? ''));
        $r['easiness']      = (float)$r['easiness'];
        $r['interval'] = (int)$r['interval'];
        $r['repetitions']   = (int)$r['repetitions'];
        $r['mastered']      = (bool)$r['mastered'];
    }

    // Conteo global pendiente hoy
    $countStmt = db()->prepare("
        SELECT COUNT(*) FROM word_srs
        WHERE user_id = ? AND next_review <= ? AND mastered = 0
    ");
    $countStmt->execute([$uid, $date]);
    $totalDue = (int)$countStmt->fetchColumn();

    ok(['due' => $rows, 'total_due' => $totalDue]);
}

// ── POST ?action=review ───────────────────────────────────────
// Body: { group_id, quality }  quality: 0-5
//   5 = perfecto, 4 = correcto con duda leve, 3 = correcto esforzado
//   2 = incorrecto fácil de recordar, 1 = incorrecto difícil, 0 = no recuerdo
if ($method === 'POST' && $action === 'review') {
    $b        = body();
    $group_id = (int)($b['group_id'] ?? 0);
    $quality  = max(0, min(5, (int)($b['quality'] ?? 0)));
    if (!$group_id) err('group_id requerido');

    // Ensure SRS record exists
    try { db()->prepare("
        INSERT IGNORE INTO word_srs (user_id, group_id)
        VALUES (?, ?)
    ")->execute([$uid, $group_id]); } catch (Throwable $e) {}

    $stmt = db()->prepare("
        SELECT easiness, `interval`, repetitions
        FROM word_srs WHERE user_id = ? AND group_id = ?
    ");
    $stmt->execute([$uid, $group_id]);
    $srs = $stmt->fetch();
    if (!$srs) err('SRS record not found', 500);

    $ef   = (float)$srs['easiness'];
    $int  = (int)$srs['interval'];
    $reps = (int)$srs['repetitions'];

    // SM-2 algorithm
    if ($quality >= 3) {
        // Correct response
        if ($reps === 0)      $int = 1;
        elseif ($reps === 1)  $int = 6;
        else                  $int = (int)round($int * $ef);
        $reps++;
        // Update easiness factor
        $ef = $ef + (0.1 - (5 - $quality) * (0.08 + (5 - $quality) * 0.02));
        $ef = max(1.3, $ef); // minimum EF = 1.3
    } else {
        // Incorrect — reset repetitions, short interval
        $reps = 0;
        $int  = 1;
        // EF decreases on failure
        $ef = max(1.3, $ef - 0.2);
    }

    // Cap interval at 365 days
    $int = min(365, max(1, $int));

    $nextReview = date('Y-m-d', strtotime("+{$int} days"));
    $today      = date('Y-m-d');

    // Mark as mastered if interval >= 21 days and quality was perfect 3+ times
    $mastered = ($int >= 21 && $quality === 5) ? 1 : 0;

    db()->prepare("
        UPDATE word_srs
        SET easiness = ?, `interval` = ?, repetitions = ?,
            next_review = ?, last_quality = ?, mastered = ?
        WHERE user_id = ? AND group_id = ?
    ")->execute([$ef, $int, $reps, $nextReview, $quality, $mastered, $uid, $group_id]);

    ok([
        'easiness'     => round($ef, 2),
        'interval'=> $int,
        'repetitions'  => $reps,
        'next_review'  => $nextReview,
        'mastered'     => (bool)$mastered,
    ]);
}

// ── POST ?action=mark_mastered ────────────────────────────────
if ($method === 'POST' && $action === 'mark_mastered') {
    $b        = body();
    $group_id = (int)($b['group_id'] ?? 0);
    $mastered = (int)(bool)($b['mastered'] ?? true);
    if (!$group_id) err('group_id requerido');

    db()->prepare("
        INSERT INTO word_srs (user_id, group_id, mastered, next_review)
        VALUES (?, ?, ?, '9999-12-31')
        ON DUPLICATE KEY UPDATE mastered = ?, next_review = IF(? = 1, '9999-12-31', CURRENT_DATE)
    ")->execute([$uid, $group_id, $mastered, $mastered, $mastered]);

    ok(['group_id' => $group_id, 'mastered' => (bool)$mastered]);
}

// ── GET ?action=word_status&group_id=X ───────────────────────
if ($method === 'GET' && $action === 'word_status') {
    $gid = (int)($_GET['group_id'] ?? 0);
    if (!$gid) err('group_id requerido');

    $stmt = db()->prepare("
        SELECT easiness, `interval`, repetitions, next_review, mastered
        FROM word_srs WHERE user_id = ? AND group_id = ?
    ");
    $stmt->execute([$uid, $gid]);
    $row = $stmt->fetch();
    ok($row ?: ['easiness' => 2.5, 'interval' => 1, 'repetitions' => 0,
                'next_review' => date('Y-m-d'), 'last_quality' => null, 'mastered' => false]);
}

// ── GET ?action=overview ──────────────────────────────────────
// Dashboard de estado general del SRS del usuario
if ($method === 'GET' && $action === 'overview') {
    // Sync: init SRS for all words not yet registered
    try { db()->prepare("
        INSERT IGNORE INTO word_srs (user_id, group_id)
        SELECT ?, g.id FROM word_groups g WHERE g.user_id = ?
    ")->execute([$uid, $uid]); } catch (Throwable $e) { /* continuar */ }

    $today = date('Y-m-d');

    $stmt = db()->prepare("
        SELECT
            COUNT(*) AS total,
            SUM(mastered = 1) AS mastered,
            SUM(mastered = 0 AND next_review <= ?) AS due_today,
            SUM(mastered = 0 AND next_review > ?) AS scheduled,
            SUM(repetitions = 0) AS new_words,
            SUM(repetitions >= 1 AND mastered = 0) AS learning,
            ROUND(AVG(easiness), 2) AS avg_easiness
        FROM word_srs
        WHERE user_id = ?
    ");
    $stmt->execute([$today, $today, $uid]);
    $ov = $stmt->fetch();

    // Next 7 days forecast
    $forecast = [];
    for ($i = 0; $i < 7; $i++) {
        $day = date('Y-m-d', strtotime("+{$i} days"));
        $s   = db()->prepare("SELECT COUNT(*) FROM word_srs WHERE user_id = ? AND next_review = ? AND mastered = 0");
        $s->execute([$uid, $day]);
        $forecast[] = ['date' => $day, 'count' => (int)$s->fetchColumn()];
    }

    // Mastery levels
    $levels = db()->prepare("
        SELECT
            CASE
                WHEN mastered = 1              THEN 'dominada'
                WHEN repetitions = 0           THEN 'nueva'
                WHEN `interval` <= 3        THEN 'aprendiendo'
                WHEN `interval` <= 14       THEN 'repasando'
                ELSE 'consolidada'
            END AS level,
            COUNT(*) AS cnt
        FROM word_srs WHERE user_id = ?
        GROUP BY level
    ");
    $levels->execute([$uid]);

    ok([
        'overview' => $ov,
        'forecast' => $forecast,
        'levels'   => $levels->fetchAll(),
    ]);
}

// ── GET ?action=word_progress ─────────────────────────────────
// Devuelve todas las palabras con su estado SRS y accuracy
if ($method === 'GET' && $action === 'word_progress') {
    try { db()->prepare("
        INSERT IGNORE INTO word_srs (user_id, group_id)
        SELECT ?, g.id FROM word_groups g WHERE g.user_id = ?
    ")->execute([$uid, $uid]); } catch (Throwable $e) {}

    $filter = $_GET['filter'] ?? 'all'; // all | due | mastered | learning
    $today  = date('Y-m-d');

    $whereExtra = '';
    if ($filter === 'due')      $whereExtra = "AND s.next_review <= '$today' AND s.mastered = 0";
    elseif ($filter === 'mastered') $whereExtra = "AND s.mastered = 1";
    elseif ($filter === 'learning') $whereExtra = "AND s.repetitions >= 1 AND s.mastered = 0";
    elseif ($filter === 'new')  $whereExtra = "AND s.repetitions = 0";

    $stmt = db()->prepare("
        SELECT
            g.id, g.spanish, g.created_at,
            GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
            s.easiness, s.`interval`, s.repetitions,
            s.next_review, s.mastered,
            COALESCE(acc.total, 0) AS practice_total,
            COALESCE(acc.correct, 0) AS practice_correct,
            CASE
                WHEN s.mastered = 1         THEN 'dominada'
                WHEN s.repetitions = 0      THEN 'nueva'
                WHEN s.`interval` <= 3   THEN 'aprendiendo'
                WHEN s.`interval` <= 14  THEN 'repasando'
                ELSE 'consolidada'
            END AS srs_level
        FROM word_srs s
        JOIN word_groups g ON g.id = s.group_id
        JOIN words w       ON w.group_id = g.id
        LEFT JOIN (
            SELECT group_id, COUNT(*) AS total, SUM(correct) AS correct
            FROM practice_log WHERE user_id = ?
            GROUP BY group_id
        ) acc ON acc.group_id = g.id
        WHERE s.user_id = ? $whereExtra
        GROUP BY g.id, g.spanish, g.created_at, s.easiness, s.`interval`, s.repetitions,
                 s.next_review, s.mastered,
                 acc.total, acc.correct
        ORDER BY s.mastered ASC, s.next_review ASC, s.easiness ASC
        LIMIT 200
    ");
    $stmt->execute([$uid, $uid]);
    $rows = $stmt->fetchAll();

    foreach ($rows as &$r) {
        $r['english_words']      = explode('||', $r['english_words']);
        $r['easiness']           = round((float)$r['easiness'], 2);
        $r['interval']      = (int)$r['interval'];
        $r['repetitions']        = (int)$r['repetitions'];
        $r['mastered']           = (bool)$r['mastered'];
        $r['practice_total']     = (int)$r['practice_total'];
        $r['practice_correct']   = (int)$r['practice_correct'];
        $r['accuracy_pct']       = $r['practice_total'] > 0
            ? round($r['practice_correct'] / $r['practice_total'] * 100)
            : null;
    }

    ok($rows);
}

// ── POST ?action=reset_progress ───────────────────────────────
// Borra todo el historial SRS del usuario SIN borrar las palabras
if ($method === 'POST' && $action === 'reset_progress') {
    $pdo = db();
    $pdo->prepare("DELETE FROM word_srs WHERE user_id = ?")->execute([$uid]);
    $pdo->prepare("DELETE FROM practice_log WHERE user_id = ?")->execute([$uid]);
    try { $pdo->prepare("DELETE FROM weekly_tests WHERE user_id = ?")->execute([$uid]); } catch(Throwable $e){}
    ok(['reset' => 'srs_and_practice_log']);
}

// ── GET ?action=words_by_filter ────────────────────────────────
if ($method === 'GET' && $action === 'words_by_filter') {
    $filter = $_GET['filter'] ?? 'all';
    $today  = date('Y-m-d');

    // Init SRS records for any words missing them
    try { db()->prepare("INSERT IGNORE INTO word_srs (user_id, group_id)
        SELECT ?, g.id FROM word_groups g WHERE g.user_id = ?")->execute([$uid, $uid]); } catch (Throwable $e) {}

    switch ($filter) {
        case 'due':
            $st = db()->prepare("
                SELECT g.id, g.spanish FROM word_groups g
                LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                WHERE g.user_id = ?
                  AND (s.id IS NULL OR (s.mastered = 0 AND s.next_review <= ?))
            ");
            $st->execute([$uid, $today]);
            break;
        case 'learning':
            $st = db()->prepare("
                SELECT g.id, g.spanish FROM word_groups g
                JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                WHERE g.user_id = ? AND s.mastered = 0 AND s.repetitions >= 1
            ");
            $st->execute([$uid]);
            break;
        case 'mastered':
            $st = db()->prepare("
                SELECT g.id, g.spanish FROM word_groups g
                JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                WHERE g.user_id = ? AND s.mastered = 1
            ");
            $st->execute([$uid]);
            break;
        case 'new':
            $st = db()->prepare("
                SELECT g.id, g.spanish FROM word_groups g
                LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = g.user_id
                WHERE g.user_id = ?
                  AND (s.id IS NULL OR s.repetitions = 0)
            ");
            $st->execute([$uid]);
            break;
        default:
            $st = db()->prepare("SELECT g.id, g.spanish FROM word_groups g WHERE g.user_id = ?");
            $st->execute([$uid]);
    }

    $rows = $st->fetchAll();
    if (empty($rows)) err('No hay palabras en este filtro', 404);
    ok($rows);
}

err('Acción no válida');