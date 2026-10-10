// Self-contained smoke verification of the Desktop component UI wiring.
// Run: node desktop/verification/components-ux-smoke.cjs
// Uses a fake DOM and mocked management responses. Real installed WebView2 E2E
// must still run separately; this test does not claim MCP installation success.
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../ui/components.js'), 'utf8');

function setup(rpc) {
  const els = new Map();
  function create(tag = 'div', id) {
    return {
      tagName: tag.toUpperCase(), id, value: '', textContent: '', children: [],
      classList: {contains: () => false, add() {}, remove() {}},
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = [...children]; },
      scrollIntoView() {}, addEventListener() {},
      querySelectorAll: () => [],
    };
  }
  function $(id) {
    if (!els.has(id)) els.set(id, create('div', id));
    return els.get(id);
  }
  const document = {
    createElement: (tag) => create(tag),
    querySelectorAll: () => [],
  };
  const scope = {
    $, rpc, document, window: {confirm: () => true},
    action: fn => Promise.resolve().then(fn), view: () => {},
    friendlyError: e => String(e), notice: () => {}, setInterval: () => 0,
  };
  vm.runInNewContext(source, scope, {filename: 'components.js', timeout: 4000});
  return {els, $};
}

(async () => {
  // Installation must perform actual preview followed by a confirmed job,
  // and expose the running status; the first click alone is NOT installation.
  const calls = [];
  let jobState = 'running';
  const h = setup(async (command, args = {}) => {
    calls.push({command, args});
    if (command === 'provider_install') {
      return args.confirmed
        ? {provider_id: args.provider_id, job: 'installer:docx', started: true}
        : {provider_id: args.provider_id, skill_package: null, environment: 'TEST', sha256: 'reviewed-plan'};
    }
    if (command === 'installation_status') return {
      jobs: [{job: 'installer:docx', provider_id: 'docx', pid: 123,
        state: jobState, exit_code: jobState === 'installed' ? 0 : null, logs: []}],
      installed_receipts: jobState === 'installed' ? ['docx'] : [],
    };
    if (command === 'provider_catalog') return {manifest_directory: 'TEST',
      runtime_observed: false, providers: []};
    throw Error('Unexpected RPC: ' + command);
  });
  h.$('install-provider').value = 'docx';
  await h.$('preview-install').onclick();
  assert.equal(calls.filter(x => x.command === 'provider_install').length, 1);
  assert.equal(calls[0].args.confirmed, false);
  assert.match(h.$('install-live').textContent, /尚未安装/);
  await h.$('start-install').onclick();
  const installed = calls.filter(x => x.command === 'provider_install');
  assert.equal(installed.length, 2);
  assert.equal(installed[1].args.confirmed, true);
  assert.equal(installed[1].args.expected_sha256, 'reviewed-plan');
  assert.match(h.$('install-live').textContent, /正在安装/);
  jobState = 'installed';
  await h.$('installation-refresh').onclick();
  assert.match(h.$('install-live').textContent, /已安装/);

  // Missing WPS working directory must be visible, not enableable, and show
  // its reviewed install route without claiming a live MCP connection.
  const w = setup(async (command) => {
    if (command === 'provider_catalog') return {manifest_directory: 'TEST',
      runtime_observed: false, providers: [{provider_id: 'wps-office',
        runtime_kind: 'executable_stdio', command: 'node.exe',
        command_exists: true, directory_exists: false,
        execution_files_present: false, install_supported: true,
        requested_enabled: false}]};
    if (command === 'installation_status') return {jobs: [], installed_receipts: []};
    throw Error('Unexpected RPC: ' + command);
  });
  await w.$('reload-providers').onclick();
  const card = w.$('provider-list').children[0];
  assert.ok(card, 'Expected a WPS provider card');
  const controls = card.children.find(x => x.className === 'actions');
  const labels = controls.children.map(x => x.textContent);
  assert.ok(labels.includes('安装组件…'));
  assert.ok(!labels.includes('启用') && !labels.includes('重载工具'));
  assert.match(card.children.map(x => x.textContent).join(' '), /未安装/);

  // Skill setup must register, sync and query the cached Skill list in order.
  const skillCalls = [];
  const s = setup(async (command, args = {}) => {
    if (command !== 'skill_action') throw Error('Unexpected RPC: ' + command);
    skillCalls.push(args.action);
    if (args.action === 'manage') return {data: {action: 'added'}};
    if (args.action === 'sync') return {data: {results: {demo: {status: 'updated'}}}};
    if (args.action === 'states') return {data: {skills: [{
      source_id: 'demo', name: 'demo-skill', ref: 'demo:demo-skill',
      effective_enabled: true,
    }]}};
    if (args.action === 'sources') return {data: {sources: [{
      id: 'demo', kind: 'local', location: 'D:\\MySkills', enabled: true,
    }]}};
    throw Error('Unexpected Skill operation');
  });
  s.$('skill-quick-id').value = 'demo';
  s.$('skill-quick-path').value = 'D:\\MySkills';
  await s.$('skill-quick-add').onclick();
  assert.deepEqual(skillCalls, ['manage', 'sync', 'states', 'sources']);
  assert.match(s.$('skill-list-status').textContent, /共 1 个已缓存 Skill/);
  console.log('PASS: preview→confirm→install-status; WPS gating; Skill source→sync→list');
})().catch(error => {console.error(error); process.exitCode = 1;});
