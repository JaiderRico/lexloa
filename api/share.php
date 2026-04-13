<?php
// ============================================================
//  share.php — Compartir paquetes de palabras entre usuarios
// ============================================================
require __DIR__ . '/config.php';

// ── Migrar tabla de paquetes compartidos si no existe ───────
try {
    db()->exec("
        CREATE TABLE IF NOT EXISTS shared_packs (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            token       VARCHAR(32) NOT NULL UNIQUE,
            user_id     INT NOT NULL,
            label       VARCHAR(120) NOT NULL DEFAULT '',
            category    VARCHAR(80)  NOT NULL DEFAULT '',
            is_public   TINYINT(1)   NOT NULL DEFAULT 0,
            words_json  MEDIUMTEXT NOT NULL,
            word_count  INT NOT NULL DEFAULT 0,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at  DATETIME NULL DEFAULT NULL,
            import_count INT NOT NULL DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ");
} catch (Throwable $e) {}
// Migrar columnas si ya existía la tabla
try { db()->exec("ALTER TABLE shared_packs ADD COLUMN category VARCHAR(80) NOT NULL DEFAULT ''"); } catch(Throwable $e) {}
try { db()->exec("ALTER TABLE shared_packs ADD COLUMN is_public TINYINT(1) NOT NULL DEFAULT 0"); } catch(Throwable $e) {}

try { db()->exec("ALTER TABLE shared_packs ADD COLUMN words_json MEDIUMTEXT NOT NULL DEFAULT ''"); } catch(Throwable $e) {}
try { db()->exec("ALTER TABLE shared_packs ADD COLUMN word_count INT NOT NULL DEFAULT 0"); } catch(Throwable $e) {}
try { db()->exec("ALTER TABLE shared_packs ADD COLUMN import_count INT NOT NULL DEFAULT 0"); } catch(Throwable $e) {}
try { db()->exec("ALTER TABLE shared_packs ADD COLUMN expires_at DATETIME NULL DEFAULT NULL"); } catch(Throwable $e) {}
try { db()->exec("ALTER TABLE shared_packs ADD COLUMN label VARCHAR(120) NOT NULL DEFAULT ''"); } catch(Throwable $e) {}

// Capturar errores fatales y retornar JSON en vez de HTML
set_exception_handler(function(Throwable $e) {
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => $e->getMessage()]);
    exit;
});

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';

// ════════════════════════════════════════════════════════════
//  POST ?action=create — Crear un paquete compartido
//  Requiere auth. Body: { label, group_ids[] | date | dates[] }
// ════════════════════════════════════════════════════════════
if ($method === 'POST' && $action === 'create') {
    $uid  = uid();
    $b    = body();
    $label     = trim($b['label']    ?? '');
    $category  = trim($b['category']  ?? '');
    $is_public = (int)(bool)($b['is_public'] ?? false);
    $pdo  = db();

    // Obtener los grupos a compartir
    $groupIds = $b['group_ids'] ?? [];
    $date     = $b['date'] ?? null;
    $dates    = $b['dates'] ?? [];

    if ($date) $dates = [$date];

    $words = [];

    $wordsDirect = $b['_words_direct'] ?? [];

    if (!empty($wordsDirect)) {
        // Palabras pasadas directamente (desde importación JSON)
        foreach ($wordsDirect as $w) {
            $sp = trim($w['spanish'] ?? '');
            $en = array_values(array_filter(array_map('trim', (array)($w['english'] ?? []))));
            if ($sp && $en) $words[] = ['spanish' => $sp, 'english' => $en];
        }
    } elseif (!empty($groupIds)) {
        // Por IDs específicos
        $ph   = implode(',', array_fill(0, count($groupIds), '?'));
        $stmt = $pdo->prepare("
            SELECT g.id, g.spanish,
                   GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
                   GROUP_CONCAT(w.is_hard ORDER BY w.id SEPARATOR '||') AS english_diffs
            FROM word_groups g
            JOIN words w ON w.group_id = g.id
            WHERE g.user_id = ? AND g.id IN ($ph)
            GROUP BY g.id
        ");
        $stmt->execute(array_merge([$uid], $groupIds));
        $rows = $stmt->fetchAll();
        foreach ($rows as $r) {
            $words[] = ['spanish' => $r['spanish'], 'english' => explode('||', $r['english_words'])];
        }
    } elseif (!empty($dates)) {
        // Por fecha(s)
        $ph   = implode(',', array_fill(0, count($dates), '?'));
        $stmt = $pdo->prepare("
            SELECT g.id, g.spanish,
                   GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
                   GROUP_CONCAT(w.is_hard ORDER BY w.id SEPARATOR '||') AS english_diffs
            FROM word_groups g
            JOIN words w ON w.group_id = g.id
            WHERE g.user_id = ? AND DATE(g.created_at) IN ($ph)
            GROUP BY g.id
        ");
        $stmt->execute(array_merge([$uid], $dates));
        $rows = $stmt->fetchAll();
        foreach ($rows as $r) {
            $words[] = ['spanish' => $r['spanish'], 'english' => explode('||', $r['english_words'])];
        }
    } else {
        err('Debes especificar group_ids, date, dates o _words_direct');
    }

    if (!$words) err('No hay palabras para compartir');

    if (!$label) $label = count($words) . ' palabras';

    $token = bin2hex(random_bytes(16));
    $pdo->prepare("
        INSERT INTO shared_packs (token, user_id, label, category, is_public, words_json, word_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ")->execute([$token, $uid, $label, $category, $is_public, json_encode($words), count($words)]);

    ok(['token' => $token, 'word_count' => count($words), 'label' => $label, 'category' => $category, 'is_public' => (bool)$is_public,
        'url' => APP_URL . '/?share=' . $token]);
}

// ════════════════════════════════════════════════════════════
//  GET ?action=get&token=XXX — Ver un paquete (sin auth)
// ════════════════════════════════════════════════════════════
if ($method === 'GET' && $action === 'get') {
    $token = trim($_GET['token'] ?? '');
    if (!$token) err('Token requerido');

    $stmt = db()->prepare("SELECT * FROM shared_packs WHERE token = ?");
    $stmt->execute([$token]);
    $pack = $stmt->fetch();
    if (!$pack) err('Paquete no encontrado o expirado', 404);

    // Obtener username del dueño
    $uStmt = db()->prepare("SELECT username FROM users WHERE id = ?");
    $uStmt->execute([$pack['user_id']]);
    $owner = $uStmt->fetch();

    ok([
        'token'        => $pack['token'],
        'label'        => $pack['label'],
        'word_count'   => (int)$pack['word_count'],
        'owner'        => $owner['username'] ?? 'usuario',
        'import_count' => (int)$pack['import_count'],
        'created_at'   => $pack['created_at'],
        'words'        => json_decode($pack['words_json'], true),
    ]);
}

// ════════════════════════════════════════════════════════════
//  POST ?action=import&token=XXX — Importar paquete a mi cuenta
// ════════════════════════════════════════════════════════════
if ($method === 'POST' && $action === 'import') {
    $uid   = uid();
    $token = trim($_GET['token'] ?? '');
    if (!$token) err('Token requerido');

    $pdo  = db();
    $stmt = $pdo->prepare("SELECT * FROM shared_packs WHERE token = ?");
    $stmt->execute([$token]);
    $pack = $stmt->fetch();
    if (!$pack) err('Paquete no encontrado', 404);

    $words  = json_decode($pack['words_json'], true) ?? [];
    $added  = 0;
    $skipped = 0;
    $groupIds = [];

    $pdo->beginTransaction();
    try {
        foreach ($words as $w) {
            $spanish = trim($w['spanish'] ?? '');
            $english = array_filter(array_map('trim', $w['english'] ?? []));
            if (!$spanish || !$english) { $skipped++; continue; }

            // Check dup
            $dup = $pdo->prepare("SELECT id FROM word_groups WHERE user_id = ? AND spanish = ? LIMIT 1");
            $dup->execute([$uid, $spanish]);
            if ($dup->fetch()) { $skipped++; continue; }

            $ins = $pdo->prepare("INSERT INTO word_groups (user_id, spanish, created_at) VALUES (?, ?, NOW())");
            $ins->execute([$uid, $spanish]);
            $gid = $pdo->lastInsertId();
            $groupIds[] = (int)$gid;

            $insW = $pdo->prepare("INSERT INTO words (group_id, english, is_hard) VALUES (?, ?, 0)");
            foreach ($english as $en) $insW->execute([$gid, $en]);

            $added++;
        }
        $pdo->commit();
        // Increment import counter
        $pdo->prepare("UPDATE shared_packs SET import_count = import_count + 1 WHERE token = ?")->execute([$token]);
    } catch (Throwable $e) {
        $pdo->rollBack();
        err('Error al importar: ' . $e->getMessage(), 500);
    }

    ok(['added' => $added, 'skipped' => $skipped, 'group_ids' => $groupIds]);
}

// ════════════════════════════════════════════════════════════
//  GET ?action=public_packs[&category=X] — Packs públicos (sin auth)
// ════════════════════════════════════════════════════════════
if ($method === 'GET' && $action === 'public_packs') {
    // Asegurar columnas existen antes de consultarlas
    try { db()->exec("ALTER TABLE shared_packs ADD COLUMN category VARCHAR(80) NOT NULL DEFAULT ''"); } catch(Throwable $e) {}
    try { db()->exec("ALTER TABLE shared_packs ADD COLUMN is_public TINYINT(1) NOT NULL DEFAULT 0"); } catch(Throwable $e) {}

    $cat    = trim($_GET['category'] ?? '');
    $where  = $cat ? "WHERE COALESCE(sp.is_public,0) = 1 AND sp.category = ?" : "WHERE COALESCE(sp.is_public,0) = 1";
    $params = $cat ? [$cat] : [];

    try {
        $stmt = db()->prepare("
            SELECT sp.token, sp.label, COALESCE(sp.category,'') AS category,
                   sp.word_count, sp.import_count, u.username AS owner
            FROM shared_packs sp
            JOIN users u ON u.id = sp.user_id
            $where
            ORDER BY sp.import_count DESC, sp.created_at DESC
            LIMIT 50
        ");
        $stmt->execute($params);
        $packs = $stmt->fetchAll();
        $cats = db()->query("SELECT DISTINCT category FROM shared_packs WHERE COALESCE(is_public,0)=1 AND category != '' ORDER BY category")->fetchAll(PDO::FETCH_COLUMN);
    } catch (Throwable $e) {
        $packs = []; $cats = [];
        // Debug: log error (remove after fix)
        error_log('share.php public_packs error: ' . $e->getMessage());
    }

    ok(['packs' => $packs, 'categories' => $cats]);
}

// ════════════════════════════════════════════════════════════
//  GET ?action=mine — Mis paquetes compartidos
// ════════════════════════════════════════════════════════════
if ($method === 'GET' && $action === 'mine') {
    $uid  = uid();
    try {
        $stmt = db()->prepare("
            SELECT token, label, COALESCE(category,'') AS category,
                   COALESCE(is_public,0) AS is_public, word_count, import_count, created_at
            FROM shared_packs
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 20
        ");
        $stmt->execute([$uid]);
        ok($stmt->fetchAll());
    } catch (Throwable $e) {
        // Columnas nuevas no existen aún — fallback sin ellas
        $stmt = db()->prepare("SELECT token, label, word_count, import_count, created_at FROM shared_packs WHERE user_id = ? ORDER BY created_at DESC LIMIT 20");
        $stmt->execute([$uid]);
        $rows = $stmt->fetchAll();
        foreach ($rows as &$r) { $r['category'] = ''; $r['is_public'] = false; }
        ok($rows);
    }
}

// ════════════════════════════════════════════════════════════
//  DELETE ?action=delete&token=XXX — Borrar un paquete
// ════════════════════════════════════════════════════════════
if ($method === 'DELETE' && $action === 'delete') {
    $uid   = uid();
    $token = trim($_GET['token'] ?? '');
    if (!$token) err('Token requerido');
    $stmt = db()->prepare("DELETE FROM shared_packs WHERE token = ? AND user_id = ?");
    $stmt->execute([$token, $uid]);
    if ($stmt->rowCount() === 0) err('Paquete no encontrado', 404);
    ok(null);
}

err('Acción no válida', 400);