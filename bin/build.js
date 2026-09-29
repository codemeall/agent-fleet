#!/usr/bin/env node
'use strict';
// Codex's plugin contract expects skills/<name>/SKILL.md. Keep skill/ canonical.
try { require('./setup.js').buildPluginSkills(); }
catch (error) { console.error(`fleet build: ${error.message}`); process.exitCode = 1; }
