# Vocab · Anki — Instrucciones de instalación

## Estructura de archivos

```
/
├── index.html
├── schema.sql
└── api/
    ├── config.php
    ├── words.php
    ├── practice.php
    └── quiz.php
```

## Pasos

### 1. Base de datos MySQL
1. Entra al panel de InfinityFree → **MySQL Databases**
2. Crea una base de datos y anota: host, nombre, usuario, contraseña
3. Abre **phpMyAdmin**, selecciona tu base de datos
4. Ve a la pestaña **SQL**, pega el contenido de `schema.sql` y ejecuta

### 2. Configurar credenciales
Abre `api/config.php` y reemplaza:
```php
define('DB_HOST', 'sql200.infinityfree.com');  // tu host MySQL
define('DB_NAME', 'if0_xxxxxxxx_vocab');        // tu base de datos
define('DB_USER', 'if0_xxxxxxxx');              // tu usuario
define('DB_PASS', 'tu_contraseña');
define('GROQ_KEY', 'gsk_xxxxxxxxxxxxxxxxxxxx'); // tu API key de Groq
```

### 3. Configurar la URL de la API en el frontend
Abre `index.html` y cambia la línea:
```js
const API = 'https://tudominio.com/api';
```
Por tu dominio real, por ejemplo:
```js
const API = 'https://miapp.infinityfreeapp.com/api';
```

### 4. Subir archivos
Sube todos los archivos al **File Manager** de InfinityFree dentro de `htdocs/`:
```
htdocs/
├── index.html
└── api/
    ├── config.php
    ├── words.php
    ├── practice.php
    └── quiz.php
```

### 5. API Key de Groq
- Ve a https://console.groq.com
- Crea una cuenta gratuita
- En **API Keys** genera una nueva key
- Pégala en `config.php`

## Funciones

| Página | Qué hace |
|--------|----------|
| **Agregar** | Registra grupos: 1 español + N inglés. Filtra por día. |
| **Practicar** | Pregunta aleatoria del día, Groq evalúa tu respuesta. |
| **Semana** | Cuántas palabras por semana + historial de pruebas. |
| **Quiz Semanal** | Prueba todas las palabras de la semana, guarda el resultado. |
