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
  // One primary click performs silent SHA-bound preview, shows one explicit
  // confirmation, then starts the job. The technical plan is optional.
  const calls = [];
  let jobState = 'idle';
  const h = setup(async (command, args = {}) => {
    calls.push({command, args});
    if (command === 'provider_install') {
      if (args.confirmed) {
        jobState='running';
        return {provider_id: args.provider_id, job: 'installer:docx', started: true};
      }
      return {provider_id: args.provider_id, skill_package: null, environment: 'TEST',
        sha256: 'reviewed-plan', specs: [{name:'docx.txt',content:'fastmcp==4.0.3'}]};
    }
    if (command === 'installation_status') return {
      jobs: jobState === 'idle' ? [] : [{job: 'installer:docx', provider_id: 'docx', pid: 123,
        state: jobState, exit_code: jobState === 'installed' ? 0 : null, logs: []}],
      installed_receipts: jobState === 'installed' ? ['docx'] : [],
    };
    if (command === 'provider_catalog') return {manifest_directory: 'TEST',
      runtime_observed: false, providers: []};
    throw Error('Unexpected RPC: ' + command);
  });
  h.$('install-provider').value = 'docx';
  await h.$('start-install').onclick();
  const installed = calls.filter(x => x.command === 'provider_install');
  assert.equal(installed.length, 2);
  assert.equal(installed[0].args.confirmed, false);
  assert.equal(installed[1].args.confirmed, true);
  assert.equal(installed[1].args.expected_sha256, 'reviewed-plan');
  // Installation status refresh replaces the initial 'started' message with
  // the live running job. Assert the real state, not stale UI copy.
  assert.match(h.$('install-live').textContent, /docx.+正在安装.*PID 123/);
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
  const quick = card.children.find(x => x.className === 'actions provider-quick');
  const advanced = card.children.find(x => x.tagName === 'DETAILS');
  const controls = advanced.children.find(x => x.className === 'actions');
  const labels = controls.children.map(x => x.textContent);
  assert.ok(quick.children.some(x => x.textContent === '安装 / 修复组件'));
  assert.ok(labels.includes('安装组件…'));
  assert.ok(!labels.includes('启用') && !labels.includes('重载工具'));
  assert.match(card.children[0].children.map(x => x.textContent).join(' '), /缺少运行组件/);

  // A configured WPS process with a failed MCP startup must not appear merely
  // disabled; the real error reason must be visible without expanding details.
  const failed = setup(async (command) => {
    if (command === 'provider_catalog') return {manifest_directory: 'TEST',
      runtime_observed: true, providers: [{provider_id: 'wps-office',
        runtime_kind: 'executable_stdio', command: 'node.exe', command_exists: true,
        directory_exists: true, execution_files_present: true,
        install_supported: true, requested_enabled: true,
        lifecycle: {state: 'error', enabled: true, tool_count: 0,
          error_message: 'Connection closed'}}]};
    if (command === 'installation_status') return {jobs: [], installed_receipts: []};
    throw Error('Unexpected failed-provider RPC: ' + command);
  });
  await failed.$('reload-providers').onclick();
  const failedCard=failed.$('provider-list').children[0];
  assert.match(failedCard.children[0].children.map(x=>x.textContent).join(' '), /连接失败/);
  assert.match(failedCard.children.map(x=>x.textContent).join(' '), /Connection closed/);
  const failedQuick=failedCard.children.find(x=>x.className==='actions provider-quick');
  assert.ok(failedQuick.children.some(x=>x.textContent==='重试连接'));
  assert.ok(failedQuick.children.some(x=>x.textContent==='修复依赖'));
  assert.ok(!failedQuick.children.some(x=>x.textContent==='启用并连接'));

  // Repair of a failed-but-enabled Provider performs one digest review, one
  // user confirmation, and disables the Provider before installing anything.
  const repairCalls = [];
  const repair = setup(async (command, args = {}) => {
    repairCalls.push({command, args});
    if (command === 'installation_status') return {jobs: [], installed_receipts: []};
    if (command === 'provider_install') return args.confirmed
      ? {provider_id: 'wps-office', job: 'installer:wps-office', started: true}
      : {provider_id: 'wps-office', sha256: 'wps-plan-sha', environment: 'TEST',
        skill_package: null, specs: [{name: 'wps-office.source.json', content: 'pinned-revision'}]};
    if (command === 'provider_catalog') return {providers: [{
      provider_id: 'wps-office', lifecycle: {enabled: true, state: 'error'}}],
      manifest_directory: 'TEST', runtime_observed: true};
    if (command === 'provider_action' && args.action === 'disable')
      return {status:'completed'};
    throw Error('Unexpected repair RPC: ' + command);
  });
  repair.$('install-provider').value = 'wps-office';
  await repair.$('start-install').onclick();
  const disableAt = repairCalls.findIndex(x => x.command === 'provider_action' && x.args.action === 'disable');
  const installAt = repairCalls.findIndex(x => x.command === 'provider_install' && x.args.confirmed);
  assert.ok(disableAt >= 0 && disableAt < installAt, 'Repair must disable before launching installer');
  assert.equal(repairCalls[installAt].args.expected_sha256, 'wps-plan-sha');

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
  // RC.7 reviewed 7-component bundle: one digest-bound confirmation
  // starts one real-install job, without silently enabling providers.
  const bundleCalls = [];
  const b = setup(async (command, args = {}) => {
    bundleCalls.push({command, args});
    if (command === 'provider_bundle') return args.confirmed
      ? {started: true, job: 'installer:starter-pack', providers: ['docx']}
      : {sha256: 'starter-hash', providers: ['docx'], started: false};
    if (command === 'installation_status') return {jobs: [], installed_receipts: []};
    throw Error('Unexpected bundle RPC: ' + command);
  });
  await b.$('start-bundle').onclick();
  assert.deepEqual(bundleCalls.filter(c => c.command === 'provider_bundle').map(c => c.args.confirmed), [false, true]);
  assert.equal(bundleCalls.find(c => c.command === 'provider_bundle' && c.args.confirmed).args.expected_sha256, 'starter-hash');

  // RC.7 custom MCP requirements: exact pins and explicit reviewed digest.
  const customCalls = [];
  const c = setup(async (command, args = {}) => {
    customCalls.push({command, args});
    if (command === 'provider_package') return args.confirmed
      ? {provider_id: args.provider_id, committed: true}
      : {provider_id: args.provider_id, package_kind: args.package_kind, packages: args.packages, sha256: 'custom-hash'};
    if (command === 'provider_catalog') return {manifest_directory: 'TEST', runtime_observed: false, providers: []};
    if (command === 'installation_status') return {jobs: [], installed_receipts: []};
    throw Error('Unexpected custom RPC: ' + command);
  });
  c.$('custom-package-id').value = 'my-mcp';
  c.$('custom-package-kind').value = 'python';
  c.$('custom-package-pins').value = 'fastmcp==4.0.3\nmcp==2.2.0';
  await c.$('custom-package-preview').onclick();
  await c.$('custom-package-save').onclick();
  assert.equal(customCalls.filter(x => x.command === 'provider_package').length, 2);
  assert.equal(customCalls.find(x => x.command === 'provider_package' && x.args.confirmed).args.expected_sha256, 'custom-hash');

  console.log('PASS: installer flow; WPS status; Skill list; RC7 starter bundle; RC7 custom MCP plans');
})().catch(error => {console.error(error); process.exitCode = 1;});
