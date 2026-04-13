<?php
// ============================================================
//  notify.php — Preferencias de notificación por email (PHPMailer)
//  Requiere: composer require phpmailer/phpmailer
// ============================================================
require __DIR__ . '/config.php';

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';
$uid    = uid();

// ── GET ?action=prefs ─────────────────────────────────────────
if ($method === 'GET' && $action === 'prefs') {
    $s = db()->prepare("SELECT email, enabled, notify_hour FROM notification_prefs WHERE user_id = ?");
    $s->execute([$uid]);
    $row = $s->fetch();
    ok($row ?: ['email' => '', 'enabled' => false, 'notify_hour' => 8]);
}

// ── POST ?action=save ─────────────────────────────────────────
if ($method === 'POST' && $action === 'save') {
    $b    = body();
    $email   = trim($b['email'] ?? '');
    $enabled = (int)(bool)($b['enabled'] ?? false);
    $hour    = max(0, min(23, (int)($b['notify_hour'] ?? 8)));

    if ($enabled && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
        err('Email inválido');
    }

    db()->prepare("
        INSERT INTO notification_prefs (user_id, email, enabled, notify_hour)
        VALUES (?, ?, ?, ?)
        ON DUPLICATE KEY UPDATE email=?, enabled=?, notify_hour=?
    ")->execute([$uid, $email, $enabled, $hour, $email, $enabled, $hour]);

    ok(['saved' => true]);
}

// ── POST ?action=test ─────────────────────────────────────────
// Envía un email de prueba al usuario
if ($method === 'POST' && $action === 'test') {
    $b     = body();
    $email = trim($b['email'] ?? '');
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) err('Email inválido');

    // Count words due today
    $due = db()->prepare("SELECT COUNT(*) FROM word_srs WHERE user_id = ? AND next_review <= CURRENT_DATE AND mastered = 0");
    $due->execute([$uid]);
    $dueCount = (int)$due->fetchColumn();

    $result = sendReminderEmail($email, $dueCount, true);
    if ($result === true) ok(['sent' => true]);
    else err('No se pudo enviar: ' . $result);
}

// ── GET ?action=send_due (llamado por cron) ───────────────────
// Envía recordatorios a todos los usuarios con notificaciones activas
// Proteger con secret key en producción
if ($method === 'GET' && $action === 'send_due') {
    $secret = $_GET['secret'] ?? '';
    if ($secret !== (NOTIFY_SECRET ?? '')) { http_response_code(403); exit; }

    $hour = (int)date('G');
    $stmt = db()->prepare("
        SELECT np.user_id, np.email, np.notify_hour,
               COUNT(s.group_id) AS due_count
        FROM notification_prefs np
        JOIN word_srs s ON s.user_id = np.user_id
        WHERE np.enabled = 1
          AND np.email != ''
          AND (np.last_sent IS NULL OR np.last_sent < CURRENT_DATE)
          AND np.notify_hour = ?
          AND s.next_review <= CURRENT_DATE
          AND s.mastered = 0
        GROUP BY np.user_id, np.email, np.notify_hour
        HAVING due_count > 0
    ");
    $stmt->execute([$hour]);
    $recipients = $stmt->fetchAll();

    $sent = 0; $errors = [];
    foreach ($recipients as $r) {
        $result = sendReminderEmail($r['email'], (int)$r['due_count'], false);
        if ($result === true) {
            db()->prepare("UPDATE notification_prefs SET last_sent = CURRENT_DATE WHERE user_id = ?")
                ->execute([$r['user_id']]);
            $sent++;
        } else {
            $errors[] = $r['email'] . ': ' . $result;
        }
    }
    ok(['sent' => $sent, 'errors' => $errors]);
}

err('Acción no válida');

// ── Email sender ──────────────────────────────────────────────
function sendReminderEmail(string $toEmail, int $dueCount, bool $isTest): true|string {
    // Try to use PHPMailer if available, otherwise fallback to mail()
    $subject = $isTest
        ? '✉ Vocab — Email de prueba'
        : "📚 Vocab — Tienes {$dueCount} palabra" . ($dueCount > 1 ? 's' : '') . " para repasar hoy";

    $appUrl  = defined('APP_URL') ? APP_URL : 'https://anki.page.gd';
    $body    = buildEmailHtml($dueCount, $isTest, $appUrl);
    $plain   = $isTest
        ? "Este es un email de prueba de Vocab.\n\nTu app: $appUrl"
        : "Tienes {$dueCount} palabra" . ($dueCount > 1 ? 's' : '') . " pendiente" . ($dueCount > 1 ? 's' : '') . " de repasar hoy.\n\nAbrir app: $appUrl";

    // Check if PHPMailer is available
    $phpmailerPath = __DIR__ . '/../vendor/autoload.php';
    if (file_exists($phpmailerPath)) {
        require_once $phpmailerPath;
        try {
            $mail = new PHPMailer\PHPMailer\PHPMailer(true);
            $mail->isSMTP();
            $mail->Host       = defined('SMTP_HOST') ? SMTP_HOST : 'smtp.gmail.com';
            $mail->SMTPAuth   = true;
            $mail->Username   = defined('SMTP_USER') ? SMTP_USER : '';
            $mail->Password   = defined('SMTP_PASS') ? SMTP_PASS : '';
            $mail->SMTPSecure = PHPMailer\PHPMailer\PHPMailer::ENCRYPTION_STARTTLS;
            $mail->Port       = defined('SMTP_PORT') ? SMTP_PORT : 587;
            $mail->CharSet    = 'UTF-8';
            $mail->setFrom(defined('SMTP_FROM') ? SMTP_FROM : $mail->Username, 'Vocab App');
            $mail->addAddress($toEmail);
            $mail->isHTML(true);
            $mail->Subject = $subject;
            $mail->Body    = $body;
            $mail->AltBody = $plain;
            $mail->send();
            return true;
        } catch (Throwable $e) {
            return $e->getMessage();
        }
    }

    // Fallback: PHP mail()
    $headers  = "From: Lexlo <noreply@lexlo.com>\r\n";
    $headers .= "MIME-Version: 1.0\r\n";
    $headers .= "Content-Type: text/html; charset=UTF-8\r\n";
    $ok = mail($toEmail, $subject, $body, $headers);
    return $ok ? true : 'mail() falló — configura PHPMailer';
}

function buildEmailHtml(int $dueCount, bool $isTest, string $appUrl): string {
    $headline = $isTest ? 'Email de prueba ✉' : "Tienes {$dueCount} palabra" . ($dueCount > 1 ? 's' : '') . " para repasar";
    $sub      = $isTest ? 'Las notificaciones de Vocab están funcionando correctamente.' : 'Tu sesión de repaso de hoy te está esperando. Solo toma unos minutos.';
    return "<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body style='margin:0;padding:0;background:#0c0c0c;font-family:sans-serif;'>
<table width='100%' cellpadding='0' cellspacing='0'><tr><td align='center' style='padding:40px 20px;'>
<table width='480' cellpadding='0' cellspacing='0' style='background:#161616;border-radius:16px;border:1px solid #252525;overflow:hidden;'>
  <tr><td style='padding:32px 36px;text-align:center;border-bottom:1px solid #252525;'>
    <div style='font-size:22px;font-weight:800;color:#eeebe2;letter-spacing:-.03em;'>vocab<span style='color:#d4f04a;'>.</span></div>
  </td></tr>
  <tr><td style='padding:36px;text-align:center;'>
    " . ($isTest ? '' : "<div style='font-size:52px;font-weight:800;color:#d4f04a;line-height:1;margin-bottom:8px;'>{$dueCount}</div>") . "
    <div style='font-size:20px;font-weight:700;color:#eeebe2;margin-bottom:10px;'>{$headline}</div>
    <div style='font-size:14px;color:#555;margin-bottom:28px;'>{$sub}</div>
    <a href='{$appUrl}' style='display:inline-block;background:#d4f04a;color:#0c0c0c;font-weight:800;font-size:14px;padding:14px 32px;border-radius:8px;text-decoration:none;letter-spacing:.02em;'>Abrir Vocab →</a>
  </td></tr>
  <tr><td style='padding:20px 36px;text-align:center;border-top:1px solid #252525;'>
    <div style='font-size:11px;color:#333;'>Para desactivar estas notificaciones, ve a Progreso → Notificaciones en la app.</div>
  </td></tr>
</table></td></tr></table></body></html>";
}
