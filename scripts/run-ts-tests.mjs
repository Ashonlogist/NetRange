// Dependency-free test runner for the lib/*.ts policy modules.
//
// The app's own toolchain (expo/metro) bundles TypeScript, but this repo has no
// JS test runner and no tsc binary installed, and the system Node is not built
// with --experimental-strip-types. So we transpile each .ts to .mjs in a temp
// dir with the system `typescript` package, then hand the emitted JS to
// node:test. No new dependency, no committed build output.
//
// Usage: node scripts/run-ts-tests.mjs [glob-ish substring filter]

import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readdirSync, rmSync, mkdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const filter = process.argv[2] ?? '';

// The system `typescript` lives outside the project (Debian's node-typescript).
const require_ = createRequire('/usr/share/nodejs/');
let ts;
try {
  ts = require_('typescript');
} catch {
  try {
    ts = createRequire(join(ROOT, 'package.json'))('typescript');
  } catch {
    console.error('SKIP: no `typescript` package available to transpile with.');
    process.exit(0);
  }
}

function walk(dir, out = []) {
  for (const e of readdirSync(dir)) {
    if (e === 'node_modules') continue;
    const p = join(dir, e);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (e.endsWith('.ts') && !e.endsWith('.d.ts')) out.push(p);
  }
  return out;
}

const outDir = mkdtempSync(join(tmpdir(), 'netrange-ts-'));
let emitted = 0;

try {
  for (const src of walk(join(ROOT, 'lib'))) {
    const rel = src.slice(ROOT.length + 1).replace(/\.ts$/, '');
    const dest = join(outDir, rel + '.mjs');
    mkdirSync(dirname(dest), { recursive: true });
    const js = ts.transpileModule(readFileSyncSafe(src), {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
      fileName: src,
    }).outputText;
    // Emitted files are .mjs, so relative specifiers must lose their .ts
    // extension or Node resolves to a file that does not exist.
    writeFileSync(dest, js.replace(/(['"])(\.{1,2}\/[^'"]+)\.ts\1/g, '$1$2.mjs$1'));
    emitted++;
  }

  if (!emitted) {
    console.log('no TypeScript sources under lib/ to test');
    process.exit(0);
  }

  const testDir = join(outDir, 'lib', '__tests__');
  const tests = readdirSync(testDir).filter((f) => f.endsWith('.test.mjs'));
  if (filter) {
    // only run the requested subset
    const keep = new Set(tests.filter((f) => f.includes(filter)));
    for (const t of tests) if (!keep.has(t)) rmSync(join(testDir, t));
  }
  const files = readdirSync(testDir)
    .filter((f) => f.endsWith('.test.mjs'))
    .map((f) => join(testDir, f));
  console.log(`transpiled ${emitted} file(s); running ${files.length} test file(s)\n`);
  if (!files.length) process.exit(0);
  const r = spawnSync(process.execPath, ['--test', ...files], { stdio: 'inherit' });
  process.exit(r.status ?? 1);
} finally {
  rmSync(outDir, { recursive: true, force: true });
}

function readFileSyncSafe(p) {
  return require_('node:fs').readFileSync(p, 'utf8');
}
