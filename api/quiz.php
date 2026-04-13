<?php
// ============================================================
//  quiz.php — Quiz semanal con priorización SRS
// ============================================================
require __DIR__ . '/config.php';

$method = $_SERVER['REQUEST_METHOD'];
$action = $_GET['action'] ?? '';
$uid    = uid();

// ── GET ?action=questions&week_start=YYYY-MM-DD ───────────────
if ($method === 'GET' && $action === 'questions') {
    $ws = $_GET['week_start'] ?? date('Y-m-d', strtotime('monday this week'));
    $we = date('Y-m-d', strtotime($ws . ' +6 days'));

    $stmt = db()->prepare("
        SELECT g.id, g.spanish,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
               COALESCE(s.easiness, 2.5)      AS easiness,
               COALESCE(s.repetitions, 0)     AS repetitions,
               COALESCE(s.`interval`, 1)   AS `interval`,
               COALESCE(s.mastered, 0)        AS mastered,
               COALESCE(s.next_review, CURRENT_DATE) AS next_review
        FROM word_groups g
        JOIN words w ON w.group_id = g.id
        LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = ?
        WHERE g.user_id = ? AND DATE(g.created_at) BETWEEN ? AND ?
        GROUP BY g.id
    ");
    $stmt->execute([$uid, $uid, $ws, $we]);
    $rows = $stmt->fetchAll();
    if (empty($rows)) err('No hay palabras esta semana', 404);

    $today = date('Y-m-d');
    foreach ($rows as &$r) {
        $score = 0;
        $ef = (float)$r['easiness'];
        $reps = (int)$r['repetitions'];
        if ($r['next_review'] <= $today && !$r['mastered']) $score += 100;
        $score += (2.5 - $ef) * 30;
        $score += max(0, 5 - $reps) * 10;
        if ($r['mastered']) $score -= 50;
        $r['_score'] = $score + rand(0, 20);
    }
    unset($r);
    usort($rows, fn($a, $b) => $b['_score'] <=> $a['_score']);

    foreach ($rows as &$r) {
        $r['english_words'] = explode('||', $r['english_words']);
        $r['direction']     = rand(0, 1) ? 'es_en' : 'en_es';
        $r['question']      = $r['direction'] === 'en_es'
            ? $r['english_words'][array_rand($r['english_words'])]
            : $r['spanish'];
        $r['srs_level'] = match(true) {
            (bool)$r['mastered']          => 'dominada',
            (int)$r['repetitions'] == 0   => 'nueva',
            (int)$r['interval'] <= 3 => 'aprendiendo',
            (int)$r['interval'] <= 14=> 'repasando',
            default                       => 'consolidada',
        };
        unset($r['_score'], $r['easiness'], $r['repetitions'], $r['interval'], $r['next_review'], $r['mastered']);
    }
    ok(['week_start' => $ws, 'week_end' => $we, 'questions' => $rows]);
}

// ── GET ?action=check_done&week_start=YYYY-MM-DD ─────────────
if ($method === 'GET' && $action === 'check_done') {
    $ws   = $_GET['week_start'] ?? date('Y-m-d', strtotime('monday this week'));
    $stmt = db()->prepare("SELECT id, score, total FROM weekly_tests WHERE user_id = ? AND week_start = ? ORDER BY id DESC LIMIT 1");
    $stmt->execute([$uid, $ws]);
    $row  = $stmt->fetch();
    ok(['done' => (bool)$row, 'score' => $row ? (int)$row['score'] : null, 'total' => $row ? (int)$row['total'] : null]);
}

// ── POST ?action=submit ───────────────────────────────────────
if ($method === 'POST' && $action === 'submit') {
    $b          = body();
    $week_start = $b['week_start'] ?? date('Y-m-d', strtotime('monday this week'));
    $answers    = $b['answers']    ?? [];
    $duration   = (int)($b['duration_secs'] ?? 0);
    if (empty($answers)) err('Sin respuestas');

    $score = 0; $results = [];
    foreach ($answers as $ans) {
        $gid       = (int)($ans['group_id'] ?? 0);
        $direction = $ans['direction'] ?? 'es_en';
        $answer    = trim($ans['answer'] ?? '');
        $question  = trim($ans['question'] ?? '');

        $stmt = db()->prepare("
            SELECT g.spanish, GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words
            FROM word_groups g JOIN words w ON w.group_id = g.id
            WHERE g.id = ? AND g.user_id = ? GROUP BY g.id
        ");
        $stmt->execute([$gid, $uid]);
        $group = $stmt->fetch();
        if (!$group) continue;

        $english_list = explode('||', $group['english_words']);
        $spanish = $group['spanish'];
        $correct = false; $feedback = '';

        if ($answer !== '') {
            $prompt = $direction === 'es_en'
                ? "Palabra español: \"$spanish\". Respuesta: \"$answer\". Correctas: ".implode(', ',$english_list).". ¿Correcto? Acepta sinónimos. SOLO JSON: {\"correct\":true/false,\"feedback\":\"max 15 palabras\"}"
                : "Palabra inglés: \"$question\". Respuesta español: \"$answer\". Correcta: \"$spanish\". ¿Correcto? SOLO JSON: {\"correct\":true/false,\"feedback\":\"max 15 palabras\"}";
            $raw = groq_call($prompt);
            if ($raw) {
                $eval = json_decode(trim(preg_replace('/```json|```/','',$raw)), true);
                if (isset($eval['correct'])) { $correct = (bool)$eval['correct']; $feedback = $eval['feedback'] ?? ''; }
            }
            if (!$feedback) {
                $correct = $direction === 'es_en'
                    ? in_array(strtolower($answer), array_map('strtolower', $english_list))
                    : strtolower($answer) === strtolower($spanish);
                $feedback = $correct ? 'Correcto' : 'Incorrecto';
            }
        } else { $feedback = 'Sin respuesta'; }

        if ($correct) $score++;
        db()->prepare("INSERT INTO practice_log (user_id,group_id,direction,practice_mode,answer,correct,feedback) VALUES (?,?,'quiz',?,?,?,?)")
            ->execute([$uid, $gid, $direction, $answer, (int)$correct, "Quiz: $feedback"]);
        srs_update_quiz($uid, $gid, $correct ? 4 : ($answer !== '' ? 2 : 1));

        $results[] = ['group_id'=>$gid,'correct'=>$correct,'your_answer'=>$answer,
            'correct_answer'=>$direction==='es_en'?$english_list:[$spanish],
            'direction'=>$direction,'question'=>$question,'feedback'=>$feedback];
    }

    db()->prepare("INSERT INTO weekly_tests (user_id,week_start,score,total) VALUES (?,?,?,?)")
        ->execute([$uid, $week_start, $score, count($answers)]);
    try {
        db()->prepare("INSERT INTO session_history (user_id,session_date,practice_mode,total,correct,duration_secs) VALUES (?,?,'quiz',?,?,?)")
            ->execute([$uid, date('Y-m-d'), count($answers), $score, $duration ?: null]);
    } catch(Throwable $e){}

    ok(['score'=>$score,'total'=>count($answers),'results'=>$results]);
}

// ── GET ?action=history ───────────────────────────────────────
if ($method === 'GET' && $action === 'history') {
    $stmt = db()->prepare("SELECT * FROM weekly_tests WHERE user_id = ? ORDER BY created_at DESC LIMIT 10");
    $stmt->execute([$uid]);
    ok($stmt->fetchAll());
}

function srs_update_quiz(int $uid, int $group_id, int $quality): void {
    try {
        db()->prepare("INSERT IGNORE INTO word_srs (user_id,group_id) VALUES (?,?)")->execute([$uid,$group_id]);
        $s = db()->prepare("SELECT easiness,`interval`,repetitions FROM word_srs WHERE user_id=? AND group_id=?");
        $s->execute([$uid,$group_id]); $srs=$s->fetch();
        $ef=(float)$srs['easiness']; $int=(int)$srs['interval']; $reps=(int)$srs['repetitions'];
        if($quality>=3){ if($reps===0)$int=1; elseif($reps===1)$int=6; else $int=(int)round($int*$ef); $reps++;
            $ef=max(1.3,$ef+(0.1-(5-$quality)*(0.08+(5-$quality)*0.02)));
        } else { $reps=0; $int=1; $ef=max(1.3,$ef-0.2); }
        $int=min(365,max(1,$int));
        db()->prepare("UPDATE word_srs SET easiness=?,`interval`=?,repetitions=?,next_review=?,last_quality=?,mastered=? WHERE user_id=? AND group_id=?")
            ->execute([$ef,$int,$reps,date('Y-m-d',strtotime("+{$int} days")),date('Y-m-d'),($int>=21&&$quality>=4)?1:0,$uid,$group_id]);
    } catch(Throwable $e){}
}

function groq_call(string $prompt): string|false {
    $ch=curl_init('https://api.groq.com/openai/v1/chat/completions');
    curl_setopt_array($ch,[CURLOPT_RETURNTRANSFER=>true,CURLOPT_POST=>true,CURLOPT_TIMEOUT=>8,
        CURLOPT_POSTFIELDS=>json_encode(['model'=>GROQ_MODEL,'messages'=>[['role'=>'user','content'=>$prompt]],'max_tokens'=>80,'temperature'=>0.1]),
        CURLOPT_HTTPHEADER=>['Content-Type: application/json','Authorization: Bearer '.GROQ_KEY]]);
    $raw=curl_exec($ch); curl_close($ch);
    return $raw ? (json_decode($raw,true)['choices'][0]['message']['content']??false) : false;
}

// ── GET ?action=questions_n&n=20&src=all ──────────────────────
// Quiz by N words, not locked to a week
if ($method === 'GET' && $action === 'questions_n') {
    $n   = max(1, min(200, (int)($_GET['n'] ?? 20)));
    $src = $_GET['src'] ?? 'all';
    $today = date('Y-m-d');

    db()->prepare("INSERT IGNORE INTO word_srs (user_id, group_id)
        SELECT ?, g.id FROM word_groups g WHERE g.user_id = ?")->execute([$uid, $uid]);

    $base = "SELECT g.id, g.spanish,
               GROUP_CONCAT(w.english ORDER BY w.id SEPARATOR '||') AS english_words,
               COALESCE(s.easiness, 2.5)    AS easiness,
               COALESCE(s.repetitions, 0)   AS repetitions,
               COALESCE(s.`interval`, 1) AS `interval`,
               COALESCE(s.mastered, 0)      AS mastered,
               COALESCE(s.next_review, CURRENT_DATE) AS next_review,
               CASE WHEN s.mastered=1 THEN 'dominada'
                    WHEN s.repetitions=0 THEN 'nueva'
                    WHEN s.`interval`<=3 THEN 'aprendiendo'
                    WHEN s.`interval`<=14 THEN 'repasando'
                    ELSE 'consolidada' END AS srs_level
             FROM word_groups g
             JOIN words w ON w.group_id = g.id
             LEFT JOIN word_srs s ON s.group_id = g.id AND s.user_id = ?
             WHERE g.user_id = ?";

    switch ($src) {
        case 'due':
            $sql = $base . " AND (s.mastered = 0 OR s.mastered IS NULL) AND (s.next_review <= ? OR s.next_review IS NULL)";
            $params = [$uid, $uid, $today];
            break;
        case 'learning':
            $sql = $base . " AND s.mastered = 0 AND s.repetitions >= 1";
            $params = [$uid, $uid];
            break;
        case 'hard':
            $sql = $base . " AND (s.easiness < 2.2 OR w.is_hard = 1)";
            $params = [$uid, $uid];
            break;
        default:
            $sql = $base;
            $params = [$uid, $uid];
    }
    $sql .= " GROUP BY g.id ORDER BY RAND()";
    if ($n > 0) $sql .= " LIMIT $n";

    $stmt = db()->prepare($sql);
    $stmt->execute($params);
    $rows = $stmt->fetchAll();
    if (empty($rows)) err('No hay palabras disponibles', 404);

    foreach ($rows as &$r) {
        $r['english_words'] = explode('||', $r['english_words']);
        $r['direction']     = rand(0, 1) ? 'es_en' : 'en_es';
    }
    unset($r);

    ok(['questions' => $rows, 'total' => count($rows)]);
}

err('Acción no válida');
