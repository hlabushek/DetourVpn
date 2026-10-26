import { readdir, readFile } from 'node:fs/promises';
import { dirname, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { config } from '../tgcloud/lib/config.js';
import { validateConfig } from '../tgcloud/lib/menu.js';
import { scanFiles, findStrayFiles } from '../node_modules/@tgcloud/cli/src/core/scanner.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const runtime = resolve(root, 'tgcloud');
const discovered = [];
async function scan(folder) {
  for (const entry of await readdir(folder, { withFileTypes: true })) {
    const file = resolve(folder, entry.name);
    if (entry.isSymbolicLink()) throw new Error('Runtime symlinks are forbidden');
    if (entry.isDirectory()) await scan(file);
    else {
      const name = relative(runtime, file).split(sep).join('/');
      if (!/^(?:schema\.js|(?:lib\/(?:[a-z_]+\/)*[a-z_]+|handlers\/(?:message|callback_query)|endpoints\/runtimeProbe)\.js)$/.test(name)) throw new Error('Unexpected runtime file');
      const source = await readFile(file, 'utf8');
      if (/\b(?:console|process|Buffer)\s*\.|\brequire\s*\(|\beval\s*\(|\bimport\s*\(|TEST ONLY|test-only-/i.test(source)) throw new Error('Forbidden runtime API or test marker');
      for (const match of source.matchAll(/\bfrom\s+['"]([^'"]+)['"]/g)) {
        const spec = match[1];
        if (['sdk', 'sdk/db'].includes(spec)) continue;
        const target = resolve(dirname(file), spec);
        if (!spec.startsWith('.') || !spec.endsWith('.js') || !target.startsWith(runtime + sep)) throw new Error('Unsupported runtime import');
        await readFile(target);
      }
      const checked = spawnSync(process.execPath, ['--check', file], { stdio: 'pipe' });
      if (checked.status !== 0) throw new Error('Runtime syntax invalid');
      discovered.push(name);
    }
  }
}
validateConfig(config);
const manifest = JSON.parse(await readFile(resolve(root, 'package.json'), 'utf8'));
if (manifest.dependencies || manifest.devDependencies['@tgcloud/cli'] !== '0.2.0') throw new Error('Unexpected dependency contract');
const hosting = JSON.parse(await readFile(resolve(root, 'tgcloud.jsonc'), 'utf8'));
if ('static' in hosting) throw new Error('Mini App hosting must not be modified by this bot task');
await scan(runtime);
// The official CLI scanner is local; no login/status/push or provider calls.
process.chdir(root);
const official = scanFiles().sort();
if (findStrayFiles().length || JSON.stringify(official) !== JSON.stringify(discovered.sort())) throw new Error('CLI module discovery mismatch');
console.log(`Runtime check passed: ${discovered.length} modules; no Node APIs, test adapters, static hosting or credentials.`);
