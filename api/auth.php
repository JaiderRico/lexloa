<?php
// ============================================================
//  auth.php — Registro · Login · Logout · Me
// ============================================================
require __DIR__ . '/config.php';

// ── Crear tablas si no existen ───────────────────────────────
db()->exec("
    CREATE TABLE IF NOT EXISTS users (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        username   VARCHAR(30) NOT NULL UNIQUE,
        password   VARCHAR(255) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
");

db()->exec("
    CREATE TABLE IF NOT EXISTS session_tokens (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id    INT          NOT NULL,
        token      VARCHAR(64)  NOT NULL UNIQUE,
        expires_at DATETIME     NOT NULL,
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
");

// ── Todas las tablas del sistema se crean aquí ────────────────
db()->exec("
    CREATE TABLE IF NOT EXISTS word_groups (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id    INT          NOT NULL,
        spanish    VARCHAR(120) NOT NULL,
        created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
");

db()->exec("
    CREATE TABLE IF NOT EXISTS words (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        group_id   INT          NOT NULL,
        english    VARCHAR(120) NOT NULL,
        is_hard    TINYINT(1)   NOT NULL DEFAULT 0,
        FOREIGN KEY (group_id) REFERENCES word_groups(id) ON DELETE CASCADE
    )
");

db()->exec("
    CREATE TABLE IF NOT EXISTS practice_log (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        user_id       INT          NOT NULL,
        group_id      INT          NOT NULL,
        direction     VARCHAR(10)  NOT NULL,
        practice_mode VARCHAR(20)  NULL,
        answer        TEXT         NOT NULL,
        correct       TINYINT(1)   NOT NULL DEFAULT 0,
        feedback      TEXT         NULL,
        created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id)  REFERENCES users(id)        ON DELETE CASCADE,
        FOREIGN KEY (group_id) REFERENCES word_groups(id)  ON DELETE CASCADE
    )
");

db()->exec("
    CREATE TABLE IF NOT EXISTS weekly_tests (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id    INT  NOT NULL,
        week_start DATE NOT NULL,
        score      INT  NOT NULL DEFAULT 0,
        total      INT  NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
");

db()->exec("
    CREATE TABLE IF NOT EXISTS word_srs (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        user_id       INT   NOT NULL,
        group_id      INT   NOT NULL,
        easiness      FLOAT NOT NULL DEFAULT 2.5,
        `interval` INT   NOT NULL DEFAULT 1,
        repetitions   INT   NOT NULL DEFAULT 0,
        next_review   DATE  NOT NULL DEFAULT '2000-01-01',
        last_review   DATE  NULL,
        mastered      TINYINT(1) NOT NULL DEFAULT 0,
        updated_at    DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_user_group (user_id, group_id),
        FOREIGN KEY (user_id)  REFERENCES users(id)       ON DELETE CASCADE,
        FOREIGN KEY (group_id) REFERENCES word_groups(id) ON DELETE CASCADE
    )
");

db()->exec("
    CREATE TABLE IF NOT EXISTS session_history (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        user_id       INT         NOT NULL,
        session_date  DATE        NOT NULL,
        practice_mode VARCHAR(20) NOT NULL DEFAULT 'type',
        total         INT         NOT NULL DEFAULT 0,
        correct       INT         NOT NULL DEFAULT 0,
        duration_secs INT         NULL,
        created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
");

db()->exec("
    CREATE TABLE IF NOT EXISTS notification_prefs (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        user_id    INT          NOT NULL UNIQUE,
        email      VARCHAR(120) NOT NULL DEFAULT '',
        enabled    TINYINT(1)   NOT NULL DEFAULT 0,
        notify_hour INT         NOT NULL DEFAULT 8,
        last_sent  DATE         NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
");

// ── Migrations silenciosas (columnas nuevas) ──────────────────
try { db()->exec("ALTER TABLE practice_log ADD COLUMN practice_mode VARCHAR(20) NULL AFTER direction"); } catch(Throwable $e){}
try { db()->exec("ALTER TABLE users ADD COLUMN email VARCHAR(120) NULL AFTER username"); } catch(Throwable $e){}

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';

// ── POST ?action=register ────────────────────────────────────
if ($method === 'POST' && $action === 'register') {
    $b        = body();
    $username = trim(strtolower($b['username'] ?? ''));
    $password = trim($b['password'] ?? '');

    if (strlen($username) < 3)  err('El usuario debe tener al menos 3 caracteres');
    if (strlen($username) > 30) err('El usuario no puede superar 30 caracteres');
    if (!preg_match('/^[a-z0-9_]+$/', $username)) err('Solo letras, números y guión bajo');
    if (strlen($password) < 6)  err('La contraseña debe tener al menos 6 caracteres');

    $check = db()->prepare("SELECT id FROM users WHERE username = ?");
    $check->execute([$username]);
    if ($check->fetch()) err('Ese nombre de usuario ya existe', 409);

    $hash = password_hash($password, PASSWORD_BCRYPT);
    $ins  = db()->prepare("INSERT INTO users (username, password) VALUES (?, ?)");
    $ins->execute([$username, $hash]);
    $uid = (int)db()->lastInsertId();

    ok(['user_id' => $uid, 'username' => $username, 'token' => makeToken($uid)]);
}

// ── POST ?action=login ───────────────────────────────────────
if ($method === 'POST' && $action === 'login') {
    $b        = body();
    $username = trim(strtolower($b['username'] ?? ''));
    $password = trim($b['password'] ?? '');

    if (!$username || !$password) err('Credenciales requeridas');

    $s = db()->prepare("SELECT id, password FROM users WHERE username = ?");
    $s->execute([$username]);
    $user = $s->fetch();

    if (!$user || !password_verify($password, $user['password'])) {
        err('Usuario o contraseña incorrectos', 401);
    }

    $uid = (int)$user['id'];
    $_SESSION['user_id'] = $uid;

    ok(['user_id' => $uid, 'username' => $username, 'token' => makeToken($uid)]);
}

// ── POST ?action=logout ──────────────────────────────────────
if ($method === 'POST' && $action === 'logout') {
    $token = $_GET['_t'] ?? ''
           ?: ($_SERVER['HTTP_X_SESSION_TOKEN'] ?? '')
           ?: (body()['token'] ?? '')
           ?: ($_COOKIE['lexlo_token'] ?? '');
    if ($token) {
        db()->prepare("DELETE FROM session_tokens WHERE token = ?")->execute([$token]);
    }
    session_destroy();
    ok(null);
}

// ── GET ?action=me ───────────────────────────────────────────
if ($method === 'GET' && $action === 'me') {
    $token = $_GET['_t'] ?? ''
           ?: ($_SERVER['HTTP_X_SESSION_TOKEN'] ?? '')
           ?: ($_GET['token'] ?? '')
           ?: ($_COOKIE['lexlo_token'] ?? '');
    if (!$token) err('Sin token', 401);

    $s = db()->prepare("
        SELECT u.id, u.username, st.expires_at
        FROM session_tokens st
        JOIN users u ON u.id = st.user_id
        WHERE st.token = ? AND st.expires_at > NOW()
    ");
    $s->execute([$token]);
    $row = $s->fetch();
    if (!$row) err('Sesión inválida o expirada', 401);

    ok(['user_id' => (int)$row['id'], 'username' => $row['username']]);
}

err('Acción no válida');

// ── Helper: generar token persistente ───────────────────────
function makeToken(int $uid): string {
    $token   = bin2hex(random_bytes(32));
    $expires = date('Y-m-d H:i:s', strtotime('+30 days'));
    db()->prepare("INSERT INTO session_tokens (user_id, token, expires_at) VALUES (?, ?, ?)")
        ->execute([$uid, $token, $expires]);
    return $token;
}