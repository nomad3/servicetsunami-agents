#!/usr/bin/env node
/**
 * §3.1 dead-code check — React pages not referenced by the router.
 *
 * For every apps/web/src/pages/*.js (and *.jsx), check whether its default-export
 * identifier (or the file stem) is referenced anywhere in apps/web/src/App.js or
 * any file with `Routes`/`Route` JSX nearby.
 *
 * Emits the JSON contract defined by scripts/smell/_findings.py.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const PAGES_DIR = 'apps/web/src/pages';
const APP_FILE = 'apps/web/src/App.js';
const SRC_DIR = 'apps/web/src';

function listPages(dir) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  const walk = (d) => {
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (/\.(js|jsx|tsx)$/.test(entry.name) && !/\.test\./.test(entry.name)) out.push(p);
    }
  };
  walk(dir);
  return out.sort();
}

function findRouterFiles(srcDir) {
  // Any file containing <Route ... element=  or <Routes>
  const matches = [];
  const walk = (d) => {
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules') continue;
        walk(p);
      } else if (/\.(js|jsx|tsx)$/.test(entry.name)) {
        try {
          const txt = fs.readFileSync(p, 'utf8');
          if (/<Routes\b|<Route\b/.test(txt)) matches.push(p);
        } catch { /* ignore */ }
      }
    }
  };
  walk(srcDir);
  return matches;
}

function pageIdentifier(filePath) {
  // Heuristic: filename stem without extension (works for default-named-export pages)
  return path.basename(filePath).replace(/\.(js|jsx|tsx)$/, '');
}

function main() {
  const preflight = {
    commands_attempted: [],
    containers_seen: [],
    input_set: PAGES_DIR,
    exit_summary: 'ok',
  };

  if (!fs.existsSync(PAGES_DIR)) {
    preflight.exit_summary = 'degraded';
    preflight.commands_attempted.push({ cmd: `ls ${PAGES_DIR}`, exit: 2, lines: 0 });
    process.stdout.write(JSON.stringify({ preflight, findings: [], method_notes: 'pages dir missing' }, null, 2) + '\n');
    return;
  }

  const routerFiles = findRouterFiles(SRC_DIR);
  preflight.commands_attempted.push({ cmd: `walk ${SRC_DIR} for <Route>`, exit: 0, lines: routerFiles.length });
  const routerText = routerFiles.map(f => fs.readFileSync(f, 'utf8')).join('\n');

  const pages = listPages(PAGES_DIR);
  preflight.commands_attempted.push({ cmd: `walk ${PAGES_DIR}`, exit: 0, lines: pages.length });

  const findings = [];
  let n = 0;
  for (const p of pages) {
    const ident = pageIdentifier(p);
    const re = new RegExp(`\\b${ident}\\b`);
    if (!re.test(routerText)) {
      n += 1;
      findings.push({
        id: `F1.unroutedpage.${n}`,
        title: `unrouted page: ${ident}`,
        where: p,
        evidence: `identifier '${ident}' not referenced in any file containing <Route> / <Routes>`,
        reproducer: 'node scripts/smell/unrouted_pages.js',
        why_it_smells: 'React page module is never mounted in the router → dead',
        suggested_action: 'delete',
        effort: 'S',
        risk: 'low',
        blast_radius: 'small',
      });
    }
  }

  process.stdout.write(JSON.stringify({
    preflight,
    findings,
    method_notes: `${pages.length} pages, ${routerFiles.length} router files`,
  }, null, 2) + '\n');
}

main();
