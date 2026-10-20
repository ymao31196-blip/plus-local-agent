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

function setup(rpc, localStorage) {
  const els = new Map();
  function create(tag = 'div', id) {
    let ownText = '';
    const listeners = new Map();
    const element = {
      tagName: tag.toUpperCase(), id, value: '', children: [],
      classList: {contains: () => false, add() {}, remove() {}},
      append(...children) { this.children.push(...children); },
      replaceChildren(...children) { this.children = [...children]; ownText=''; },
      scrollIntoView() {},
      addEventListener(event, listener) {listeners.set(event, [...(listeners.get(event)||[]), listener]);},
      fire(event) {for(const listener of listeners.get(event)||[]) listener();},
      querySelectorAll: () => [],
    };
    Object.defineProperty(element, 'textContent', {
      get() { return ownText + this.children.map(child=>child.textContent||'').join(''); },
      set(text) { ownText=String(text); this.children=[]; },
    });
    return element;
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
    $, rpc, document, window: {confirm: () => true, localStorage},
    action: fn => Promise.resolve().then(fn), view: () => {},
    friendlyError: e => String(e), notice: () => {}, setInterval: () => 0,
  };
  vm.runInNewContext(source, scope, {filename: 'components.js', timeout: 4000});
  return {els, $, scope};
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

  // Skill Library installs with no user source path. The confirmation must
  // preserve the bundled plan identity instead of reclassifying it as local.
  const bundledSkillCalls = [];
  let bundledStarted = false;
  const bundled = setup(async (command, args = {}) => {
    bundledSkillCalls.push({command, args});
    if (command === 'provider_install') {
      if (args.confirmed) {
        bundledStarted = true;
        return {provider_id: 'skill-library', started: true, job: 'installer:skill-library'};
      }
      return {provider_id: 'skill-library', skill_package: 'C:/resources/skill-library.whl',
        skill_package_origin: 'bundled', sha256: 'built-in-skill-hash',
        environment: 'isolated', specs: [{name: 'skill-library.txt', content: 'fastmcp==4.0.3'}]};
    }
    if (command === 'provider_catalog') return {providers: []};
    if (command === 'installation_status') return {jobs: bundledStarted ? [{
      job: 'installer:skill-library', provider_id: 'skill-library', state: 'running', pid: 44
    }] : [], installed_receipts: []};
    throw Error('Unexpected bundled Skill Library RPC: ' + command);
  });
  bundled.$('install-provider').value = 'skill-library';
  await bundled.$('start-install').onclick();
  const bundledInstall = bundledSkillCalls.filter(x => x.command === 'provider_install');
  assert.equal(bundledInstall.length, 2);
  assert.equal(bundledInstall[0].args.skill_package, null);
  assert.equal(bundledInstall[1].args.skill_package, null);
  assert.equal(bundledInstall[1].args.expected_sha256, 'built-in-skill-hash');
  assert.match(bundled.$('install-live').textContent, /正在安装/);

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
  const quick = card.children.find(x => x.className === 'provider-quick-actions');
  const advanced = card.children.find(x => x.tagName === 'DETAILS');
  const content = advanced.children.find(x => x.className === 'provider-advanced-content');
  const controls = content.children.find(x => x.className === 'provider-advanced-actions');
  const labels = controls.children.map(x => x.textContent);
  assert.ok(quick.children.some(x => x.textContent === '安装组件'));
  assert.ok(labels.includes('安装组件'));
  assert.ok(!labels.includes('启用') && !labels.includes('重载工具'));
  const statusText=card=>card.children[0].children[1].children.map(x=>x.textContent).join(' ');
  assert.match(statusText(card), /连接：缺少运行组件/);
  assert.match(statusText(card), /启停：未启用/);

  // The Runtime may retain state='ready' from discovery even after a
  // user temporarily disables the Provider. Never advertise it as connected.
  const stoppedSkill = setup(async (command, args = {}) => {
    if (command === 'provider_catalog') return {manifest_directory:'TEST',
      runtime_observed:true, providers:[{provider_id:'skill-library',
        runtime_kind:'isolated_python_stdio',execution_files_present:true,
        install_supported:true,temporarily_disabled:true,
        lifecycle:{state:'ready',enabled:false,tool_count:17}}]};
    if (command === 'installation_status') return {jobs:[],installed_receipts:['skill-library']};
    if (command === 'skill_action' && args.action === 'states')
      throw Error('Capability is unavailable: skill-library.states');
    throw Error('Unexpected stopped Skill RPC: '+command);
  });
  await stoppedSkill.$('reload-providers').onclick();
  const stoppedCard=stoppedSkill.$('provider-list').children[0];
  assert.match(statusText(stoppedCard), /启停：已停用/);
  assert.match(statusText(stoppedCard), /连接：未连接/);
  assert.doesNotMatch(statusText(stoppedCard), /MCP 已连接/);
  const stoppedQuick=stoppedCard.children.find(x=>x.className==='provider-quick-actions');
  assert.ok(stoppedQuick.children.some(x=>x.textContent==='启用并连接'));
  // Individual Skill Library UI belongs to skill-quick-source-smoke.cjs.

  // Every installed Provider shows BOTH enablement and real connection
  // at a glance, with a one-click action to reverse its current enable state.
  let toggleEnabled=true;
  const toggleCalls=[];
  const toggle = setup(async (command, args={}) => {
    if (command === 'provider_catalog') return {
      manifest_directory:'TEST',runtime_observed:true,providers:[{
        provider_id:'pdf',runtime_kind:'isolated_python_stdio',
        execution_files_present:true,install_supported:true,
        requested_enabled:toggleEnabled,
        lifecycle:{state:'ready',enabled:toggleEnabled,tool_count:8}
      }]};
    if (command === 'installation_status') return {jobs:[],installed_receipts:['pdf']};
    if (command === 'provider_action') {
      toggleCalls.push(args);
      toggleEnabled=args.action==='enable';
      return {status:'completed'};
    }
    throw Error('Unexpected toggle RPC: '+command);
  });
  const toggleCard=()=>toggle.$('provider-list').children[0];
  const toggleQuick=()=>toggleCard().children.find(x=>x.className==='provider-quick-actions');
  await toggle.$('reload-providers').onclick();
  assert.match(statusText(toggleCard()), /启停：已启用/);
  assert.match(statusText(toggleCard()), /连接：MCP 已连接/);
  assert.ok(toggleQuick().children.some(x=>x.textContent==='停用'));
  await toggleQuick().children.find(x=>x.textContent==='停用').onclick();
  assert.equal(toggleCalls[0].action,'disable');
  assert.match(statusText(toggleCard()), /启停：已停用/);
  assert.doesNotMatch(statusText(toggleCard()), /连接：MCP 已连接/);
  assert.ok(toggleQuick().children.some(x=>x.textContent==='启用并连接'));
  await toggleQuick().children.find(x=>x.textContent==='启用并连接').onclick();
  assert.equal(toggleCalls[1].action,'enable');
  assert.match(statusText(toggleCard()), /启停：已启用/);
  assert.match(statusText(toggleCard()), /连接：MCP 已连接/);

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
  assert.match(statusText(failedCard), /启停：已启用/);
  assert.match(statusText(failedCard), /连接：连接失败/);
  assert.match(failedCard.children.map(x=>x.textContent).join(' '), /Connection closed/);
  const failedQuick=failedCard.children.find(x=>x.className==='provider-quick-actions');
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

  // Skill-specific GUI coverage moved to skill-quick-source-smoke.cjs.

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

  // Repository jobs must preserve review digests, wait for completion, and
  // treat cancellation as cancellation rather than successful installation.
  const repositoryCalls=[];
  let approve=true, repositoryJobState='completed';
  const repoScope={module:{exports:{}}, Error, console, setTimeout,
    confirm:()=>approve};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../ui/component-manager.js'),'utf8'),repoScope);
  const RepoManager=repoScope.module.exports;
  const repoManager=new RepoManager({showNotification(){},invoke:async(command,args)=>{
    repositoryCalls.push({command,args});
    if(command==='provider_installed_list')return {providers:[]};
    if(command==='installation_status')return {jobs:[{job:'installer:repository-echo',state:repositoryJobState,logs:['owned worker failed']}]};
    if(command.endsWith('_remote'))return args.confirmed
      ? {started:true,job:'installer:repository-echo'}
      : {sha256:'complete-package-review',spec:{dependencies:{python_packages:['mcp==2.2.0']}}};
    throw Error('Unexpected repository RPC');
  }});
  await repoManager.installProvider('echo','file:///test');
  assert.equal(repositoryCalls.find(c=>c.command==='provider_install_remote'&&c.args.confirmed).args.expected_sha256,'complete-package-review');
  await repoManager.uninstallProvider('echo');
  assert.equal(repositoryCalls.find(c=>c.command==='provider_uninstall_remote'&&c.args.confirmed).args.expected_sha256,'complete-package-review');
  const count=repositoryCalls.filter(c=>c.args?.confirmed).length;
  approve=false;
  assert.equal(await repoManager.upgradeProvider('echo','file:///test'),null);
  assert.equal(repositoryCalls.filter(c=>c.args?.confirmed).length,count);
  approve=true;repositoryJobState='failed';
  await assert.rejects(()=>repoManager.installProvider('echo','file:///test'),/owned worker failed/);
  console.log('PASS: installer flow; WPS status; Skill list; starter bundle; custom plans; repository SHA jobs/cancellation/failure');
})().catch(error => {console.error(error); process.exitCode = 1;});
