const fs = require('fs');
const s = fs.readFileSync('C:/repos/bb/lexloa/script_final.js', 'utf8');

// Try a simple try-catch parsing approach to find exactly where it's failing
let parenDepth = 0;
let braceDepth = 0;
let lineNum = 1;
let lastErrorLine = 0;

for (let i = 0; i < s.length; i++) {
    const char = s[i];
    const next = s[i+1];
    
    // Skip comments
    if (char === '/' && next === '/') {
        while (i < s.length && s[i] !== '\n') i++;
        continue;
    }
    if (char === '/' && next === '*') {
        while (i < s.length && !(s[i] === '*' && s[i+1] === '/')) i++;
        continue;
    }
    
    // Skip strings
    if (char === '"' || char === "'" || char === '`') {
        const q = char;
        i++;
        while (i < s.length && s[i] !== q) {
            if (s[i] === '\\') i++;
            i++;
        }
        continue;
    }
    
    if (char === '(') parenDepth++;
    if (char === ')') { parenDepth--; if (parenDepth < 0) { console.log('Unmatched ) at line', lineNum, 'context:', s.slice(i-30,i+30)); break; } }
    if (char === '{') braceDepth++;
    if (char === '}') braceDepth--;
    if (char === '\n') lineNum++;
}

console.log('After full scan - parenDepth:', parenDepth, 'braceDepth:', braceDepth);
console.log('Last line:', lineNum);

// Now let's specifically check what's open at the end
// Show last few lines that have unmatched opens
console.log('\n--- Checking for the missing close ---');
const lines = s.split('\n');
parenDepth = 0;
braceDepth = 0;

for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // Count opens and closes on this line
    for (const c of line) {
        if (c === '(') parenDepth++;
        if (c === ')') parenDepth--;
        if (c === '{') braceDepth++;
        if (c === '}') braceDepth--;
    }
    // Print lines where there's positive depth (unclosed)
    if (parenDepth > 0 || braceDepth > 0) {
        console.log(`Line ${i+1}: parenDepth=${parenDepth}, braceDepth=${braceDepth} -> ${line.substring(0,80)}`);
    }
}