'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const OWNER = '@codemeall/agent-fleet';
const MARKER = '.fleet-install.json';
const HARNESSES = { claude: '.claude', codex: '.agents', cursor: '.cursor' };
const source = path.join(__dirname, '..', 'skill');

function exists(file) {
  try { fs.lstatSync(file); return true; } catch (e) { if (e.code === 'ENOENT') return false; throw e; }
}

// Record the complete tree, including empty directories, so added user files are preserved.
function inventory(root, forSource = false) {
  const entries = {};
  function walk(dir, prefix = '') {
    for (const name of fs.readdirSync(dir).sort()) {
      if (!prefix && name === MARKER) continue;
      if (forSource && (name === '__pycache__' || name === '.DS_Store' || name.endsWith('.pyc'))) continue;
      const rel = prefix ? `${prefix}/${name}` : name;
      const file = path.join(dir, name);
      const stat = fs.lstatSync(file);
      if (stat.isSymbolicLink()) throw new Error(`Refusing symbolic link: ${file}`);
      if (stat.isDirectory()) { entries[rel] = 'directory'; walk(file, rel); }
      else if (stat.isFile()) entries[rel] = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') + `:${stat.mode & 0o777}`;
      else throw new Error(`Unsupported file: ${file}`);
    }
  }
  walk(root);
  return entries;
}

function assertOwned(target) {
  const stat = fs.lstatSync(target);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`Refusing unowned installation: ${target}`);
  const marker = path.join(target, MARKER);
  if (!exists(marker) || !fs.lstatSync(marker).isFile() || fs.lstatSync(marker).isSymbolicLink()) throw new Error(`Refusing unowned installation: ${target}`);
  let record;
  try { record = JSON.parse(fs.readFileSync(marker, 'utf8')); } catch { throw new Error(`Invalid ownership record: ${marker}`); }
  const actual = inventory(target);
  if (record.owner !== OWNER || record.format !== 1 || !record.files ||
      Object.keys(actual).length !== Object.keys(record.files).length ||
      Object.entries(actual).some(([name, digest]) => record.files[name] !== digest)) {
    throw new Error(`Preserving modified or unowned installation: ${target}. Back it up and move it aside before retrying.`);
  }
}

function assertParents(base, target) {
  let current = path.dirname(target);
  while (current !== base) {
    if (exists(current) && (fs.lstatSync(current).isSymbolicLink() || !fs.lstatSync(current).isDirectory())) {
      throw new Error(`Refusing non-directory or symbolic-link parent: ${current}`);
    }
    const parent = path.dirname(current);
    if (parent === current) throw new Error(`Target escapes installation root: ${target}`);
    current = parent;
  }
}

function install(target, files, quiet = false) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const staging = fs.mkdtempSync(path.join(path.dirname(target), '.fleet-stage-'));
  let backup;
  try {
    for (const [rel, digest] of Object.entries(files)) {
      const to = path.join(staging, rel);
      if (digest === 'directory') fs.mkdirSync(to, { recursive: true });
      else { fs.copyFileSync(path.join(source, rel), to); fs.chmodSync(to, fs.statSync(path.join(source, rel)).mode & 0o777); }
    }
    fs.writeFileSync(path.join(staging, MARKER), JSON.stringify({ owner: OWNER, format: 1, version: require('../package.json').version, files: inventory(staging) }, null, 2) + '\n');
    if (exists(target)) {
      assertOwned(target);
      backup = `${staging}-previous`;
      fs.renameSync(target, backup);
    }
    try { fs.renameSync(staging, target); }
    catch (error) { if (backup) fs.renameSync(backup, target); backup = undefined; throw error; }
    if (backup) fs.rmSync(backup, { recursive: true });
  } finally {
    if (exists(staging)) fs.rmSync(staging, { recursive: true });
  }
  if (!quiet) console.log(`installed ${target}`);
}

// Claude Code's status line shows Fleet's workers under the prompt. With the claude harness, setup sets it through
// the copied runtime (`fleet statusline --install`), which keeps an existing line by running it first and backs the
// settings file up; uninstall puts the old line back first. It points at the installed copy, never npx's cache, and
// goes in the settings file beside the skill: <base>/.claude/settings.local.json for project scope (wherever setup
// runs from), <base>/.claude/settings.json for --root, else the user's own. A failed install only warns: the skill
// is installed either way. A failed restore keeps the claude install, so the command the warning names still runs.
function statusline(action, target, opts, base) {
  const runtime = path.join(target, 'bin', 'fleet');
  const python = process.env.FLEET_PYTHON || 'python3';
  const settings = opts.scope === 'project' ? path.join(base, '.claude', 'settings.local.json')
    : opts.root ? path.join(base, '.claude', 'settings.json') : null;
  const args = [runtime, 'statusline', `--${action}`, ...(settings ? ['--settings', settings] : [])];
  const result = spawnSync(python, args, { encoding: 'utf8' });
  if (result.stdout) process.stdout.write(result.stdout);
  if (!result.error && result.status === 0) return true;
  const why = (result.error ? result.error.message : (result.stderr || '').trim().split('\n').pop()) || `exit ${result.status}`;
  const retry = `${python} ${args.map(a => (a === runtime || a === settings ? JSON.stringify(a) : a)).join(' ')}`;
  if (action === 'install') {
    console.warn(`warning: Fleet is installed, but Claude Code's status line was not set (${why}).`);
    console.warn(`Retry with: ${retry}`);
  } else {
    console.warn(`warning: Claude Code's status line was not restored (${why}), so ${target} is kept: its runtime restores it.`);
    console.warn(`Retry with: ${retry}`);
    console.warn('then run this uninstall again (--no-statusline removes the copy and leaves the settings as they are).');
  }
  return false;
}

function run(action, args) {
  if (args.includes('--help') || args.includes('-h')) {
    console.log(`fleet ${action} --harness claude|codex|cursor|all [--scope user|project] [--root /absolute/base] [--no-statusline]\nUser scope defaults to your home; project scope defaults to the current directory.\nCopies the skill and runtime. With the claude harness, setup also sets Claude Code's status line to show Fleet's\nworkers under the prompt, keeping a line you already have (backed up first); uninstall puts yours back.\n--no-statusline leaves Claude Code's settings alone. Does not change PATH or plugin registries.\nModified or unowned installations are preserved and produce an error.`);
    return;
  }
  const opts = { scope: 'user', statusline: !args.includes('--no-statusline') };
  args = args.filter(arg => arg !== '--no-statusline');
  for (let i = 0; i < args.length; i += 2) {
    const key = args[i].replace(/^--/, '');
    if (!['harness', 'scope', 'root'].includes(key) || args[i] !== `--${key}` || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error(`Invalid option: ${args[i]}. Use fleet ${action} --help.`);
    if (Object.hasOwn(opts, key) && key !== 'scope') throw new Error(`Repeated option: ${args[i]}`);
    opts[key] = args[i + 1];
  }
  if (!Object.hasOwn(HARNESSES, opts.harness) && opts.harness !== 'all') throw new Error('--harness must be claude, codex, cursor, or all');
  if (!['user', 'project'].includes(opts.scope)) throw new Error('--scope must be user or project');
  if (opts.root && !path.isAbsolute(opts.root)) throw new Error('--root must be absolute');
  const requestedBase = opts.root || (opts.scope === 'user' ? os.homedir() : process.cwd());
  // Resolve the explicit root itself (e.g. macOS /tmp), but never follow harness-directory links.
  const base = exists(requestedBase) ? fs.realpathSync(requestedBase) : path.resolve(requestedBase);
  const harnesses = opts.harness === 'all' ? Object.keys(HARNESSES) : [opts.harness];
  const targets = harnesses.map(name => path.join(base, HARNESSES[name], 'skills', 'fleet'));
  for (const target of targets) { assertParents(base, target); if (exists(target)) assertOwned(target); }
  const files = action === 'setup' ? inventory(source, true) : null;
  const claude = opts.statusline && harnesses.includes('claude') ? targets[harnesses.indexOf('claude')] : null;
  const kept = action !== 'setup' && claude && exists(claude) && !statusline('uninstall', claude, opts, base) ? claude : null;
  for (const target of targets) {
    if (action === 'setup') install(target, files);
    else if (target === kept) console.log(`kept ${target}`);
    else if (exists(target)) { assertOwned(target); fs.rmSync(target, { recursive: true }); console.log(`removed ${target}`); }
    else console.log(`absent ${target}`);
  }
  if (kept) process.exitCode = 1;
  if (action === 'setup' && claude) statusline('install', claude, opts, base);
  if (action === 'setup') {
    console.log('Restart your harness to discover Fleet. fleet is not added to PATH; use the copied runtime (or npx @codemeall/agent-fleet@preview):');
    console.log(path.join(targets[0], 'bin', 'fleet'));
  }
}
function buildPluginSkills() {
  const root = fs.realpathSync(path.join(__dirname, '..'));
  const legacy = path.join(root, 'skills', 'fleet');
  if (exists(legacy)) { assertOwned(legacy); fs.rmSync(legacy, { recursive: true }); }
  const pluginRoot = path.join(root, 'dist', 'codex', 'agent-fleet');
  const target = path.join(pluginRoot, 'skills', 'fleet');
  assertParents(root, target);
  if (exists(target)) assertOwned(target);
  install(target, inventory(source, true), true);
  const markerFile = path.join(target, MARKER);
  const record = JSON.parse(fs.readFileSync(markerFile, 'utf8'));
  record.files = inventory(target);
  fs.writeFileSync(markerFile, JSON.stringify(record, null, 2) + '\n');
  fs.mkdirSync(path.join(pluginRoot, '.codex-plugin'), { recursive: true });
  fs.copyFileSync(path.join(root, 'plugin-manifests', 'codex.json'), path.join(pluginRoot, '.codex-plugin', 'plugin.json'));
}
module.exports = { run, buildPluginSkills };
