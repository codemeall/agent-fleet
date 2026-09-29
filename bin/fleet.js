#!/usr/bin/env node
'use strict';
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const args = process.argv.slice(2);
if (['setup', 'uninstall'].includes(args[0])) {
  try { require('./setup.js').run(args[0], args.slice(1)); }
  catch (error) { console.error(`fleet: ${error.message}`); process.exitCode = 1; }
} else if (args[0] === '--version') {
  console.log(require('../package.json').version);
} else {
  if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
    console.log('Skill installation: fleet setup --harness claude|codex|cursor|all [--scope user|project] [--root /absolute/base]');
    console.log('Skill removal:      fleet uninstall (same options; preserves modified installations)\n');
  }
  const python = process.env.FLEET_PYTHON || 'python3';
  const probe = spawnSync(python, ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'], { encoding: 'utf8' });
  if (probe.error || probe.status !== 0) {
    console.error('fleet: Python 3.11+ is required. Install it or set FLEET_PYTHON to its executable path.');
    process.exitCode = 1;
  } else {
    const result = spawnSync(python, [path.join(__dirname, '..', 'skill', 'bin', 'fleet'), ...args], { stdio: 'inherit' });
    if (result.error) console.error(`fleet: ${result.error.message}`);
    if (result.signal) process.kill(process.pid, result.signal);
    else process.exitCode = result.status ?? 1;
  }
}
