<?php
// ============================================================
//  config.php — Configuración + helpers globales (SEGURO)
// ============================================================

// ── Variables de entorno obligatorias ────────────────────────
function env($key, $default = null) {
    $value = getenv($key);
    if ($value === false) {
        if ($default !== null) return $default;
        err("Falta variable de entorno: $key", 500);
    }
    return $value;
}

// ── Base de datos ────────────────────────────────────────────
define('DB_HOST', env('DB_HOST'));
define('DB_NAME', env('DB_NAME'));
define('DB_USER', env('DB_USER'));
define('DB_PASS', env('DB_PASS'));

// ── API ─────────────────────────────────────────────────────
define('GROQ_KEY', env('GROQ_API_KEY'));
define('GROQ_MODEL', env('GROQ_MODEL', 'llama-3.3-70b-versatile'));

// ── Email / Notificaciones ───────────────────────────────────
define('SMTP_HOST', env('SMTP_HOST', 'smtp.gmail.com'));
define('SMTP_USER', env('SMTP_USER'));
define('SMTP_PASS', env('SMTP_PASS'));
define('SMTP_FROM', env('SMTP_FROM'));
define('SMTP_PORT', (int) env('SMTP_PORT', 587));

// ── App ─────────────────────────────────────────────────────
define('APP_URL', env('APP_URL'));
define('NOTIFY_SECRET', env('NOTIFY_SECRET'));

// ── Sesión ──────────────────────────────────────────────────
if (session_status() === PHP_SESSION_NONE) {
    session_set_cookie_params([
        'lifetime' => 60 * 60 * 24 * 30,
        'path'     => '/',
        'samesite' => 'Lax',
        'httponly' => true,
    ]);
    session_start();
}

// ── CORS ────────────────────────────────────────────────────
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, DELETE, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, X-Session-Token');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

// ── DB ──────────────────────────────────────────────────────
function db(): PDO {
    static $pdo = null;
    if ($pdo) return $pdo;

    $pdo = new PDO(
        'mysql:host=' . DB_HOST . ';dbname=' . DB_NAME . ';charset=utf8mb4',
        DB_USER,
        DB_PASS,
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC
        ]
    );

    return $pdo;
}

// ── Usuario actual ───────────────────────────────────────────
function uid(): int {
    if (!empty($_SESSION['user_id'])) return (int)$_SESSION['user_id'];

    $token = $_GET['_t'] ?? ''
           ?: ($_SERVER['HTTP_X_SESSION_TOKEN'] ?? '')
           ?: ($_COOKIE['lexlo_token'] ?? '');

    if ($token) {
        $s = db()->prepare("SELECT user_id FROM session_tokens WHERE token = ? AND expires_at > NOW()");
        $s->execute([$token]);
        $row = $s->fetch();
        if ($row) return (int)$row['user_id'];
    }

    err('No autenticado', 401);
}

// ── Helpers ─────────────────────────────────────────────────
function ok($data): void {
    echo json_encode(['ok' => true, 'data' => $data]);
    exit;
}

function err(string $msg, int $code = 400): void {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $msg]);
    exit;
}

function body(): array {
    $ct = $_SERVER['CONTENT_TYPE'] ?? '';

    if (str_contains($ct, 'application/json')) {
        return json_decode(file_get_contents('php://input'), true) ?? [];
    }

    if (!empty($_POST)) return $_POST;

    $raw = file_get_contents('php://input');
    if ($raw) {
        $decoded = json_decode($raw, true);
        if ($decoded) return $decoded;

        parse_str($raw, $parsed);
        if ($parsed) return $parsed;
    }

    return [];
}