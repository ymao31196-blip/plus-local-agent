'use strict';
// Modern App Core - V2
const $ = id => document.getElementById(id);
const invoke = (command, args) => window.__TAURI__.core.invoke(command, args);

let snapshot, busy = false, connectionEdited = false, workspaceEditName = null;

// Track connection form changes
for (const id of ['runtime-port', 'health-port', 'tunnel-id', 'secret']) {
  $(id).addEventListener('input', () => { connectionEdited = true; });
}

// Legacy detection (optional feature)
const legacy = document.createElement('div');
legacy.className = 'card mb-lg';
legacy.innerHTML = '<div class="card-header"><h2 class="card-title">检测已有源码安装（可选）</h2></div><div class="card-body"><p class="mb-md">填写已有 PLA 源码目录，仅检查安装布局与配置文件是否存在，不读取或导入密钥，不接管已有服务。</p><div class="form-group"><label class="form-label">现有 PLA 目录</label><input id="legacy-root" placeholder="D:\\AI_Tools\\plus-local-agent" class="form-input"></div></div><div class="card-actions"><button id="detect-legacy">检查已有安装</button></div>';
$('setup').prepend(legacy);

$('detect-legacy').onclick = () => action(async () => {
  const result = await rpc('detect_legacy', { path: $('legacy-root').value.trim() });
  notice(result.recognized_source_install
    ? '已识别 PLA 源码安装。配置和服务保持独立，不会自动导入或接管。'
    : '未识别 PLA 源码安装；该目录未被修改。');
});

// Notification state is independent of the page scroll position.
let noticeTimer = null;
let lastErrorNotice = '';
let lastBackendError = '';

function closeNotice() {
  clearTimeout(noticeTimer);
  noticeTimer = null;
  $('notice').hidden = true;
}
$('notice-close').onclick = closeNotice;

function notice(text, error = false) {
  const message = String(text);
  if (error && !$('notice').hidden && lastErrorNotice === message) return;
  if (error) lastErrorNotice = message;
  clearTimeout(noticeTimer);
  $('notice-text').textContent = message;
  $('notice').classList.toggle('error', error);
  $('notice').setAttribute('role', error ? 'alert' : 'status');
  $('notice').setAttribute('aria-live', error ? 'assertive' : 'polite');
  $('notice').hidden = false;
  noticeTimer = setTimeout(closeNotice, error ? 10000 : 4000);
}

// Friendly error messages
function friendlyError(error) {
  const text = String(error);
  const friendlyMessages = {
    'Stop services before changing configuration': '请先停止服务，再保存连接或浏览器配置。',
    'Start Runtime before connecting Tunnel': '请先启动 Runtime，再连接 Tunnel。',
    'Configure this machine\'s Tunnel ID and runtime API key': '请填写并保存 Tunnel ID 和 Runtime API key，再连接 Tunnel。',
    'Stop Runtime before changing workspace permissions': '请先停止 Runtime，再保存工作区授权。',
    'Runtime is stopped': '请先启动 Runtime，再运行本地工具验证。',
    'Root name already exists; choose a different name or edit the existing root': '这个 root 名称已存在。新增目录请使用不同名称；修改原目录请点击它的「编辑」。',
    'Root no longer exists; refresh the workspace list': '这个 root 已被移除，请刷新列表后重新添加。'
  };
  return friendlyMessages[text] || text;
}

// Action wrapper with loading state
async function action(fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    await fn();
  } catch (e) {
    notice(friendlyError(e), true);
  } finally {
    busy = false;
    document.querySelectorAll('button').forEach(b => b.disabled = false);
  }
}

// RPC wrapper
async function rpc(command, args = {}) {
  return invoke('manage', { command, args });
}

// CHAGENT.md: explicit local editor, read on demand, safe version-bound saves.
let routingGuideSnapshot = null;
const routingEditor = $('routing-guide-content');
const routingPanel = $('routing-guide-panel');
function routingGuideDirty() {
  return Boolean(routingGuideSnapshot && routingEditor.value !== routingGuideSnapshot.content);
}
function updateRoutingGuideState() {
  $('routing-guide-dirty').textContent = !routingGuideSnapshot ? '尚未读取'
    : routingGuideDirty() ? '有未保存的修改' : '当前内容已加载';
}
routingEditor.addEventListener('input', updateRoutingGuideState);
async function loadRoutingGuide() {
  if (routingGuideDirty() && !window.confirm('当前规则未保存，确定放弃修改并重新读取？')) return;
  const result = await rpc('routing_guide_read');
  routingGuideSnapshot = result;
  routingEditor.value = result.content;
  const validation = result.validation?.status === 'valid'
    ? ('已校验 · ' + result.validation.route_count + '条规则')
    : ('配置需修复：' + (result.validation?.reason || '无效格式'));
  $('routing-guide-state').textContent = (result.source === 'personal' ? '个人规则' : '默认规则') + ' · ' + validation;
  updateRoutingGuideState();
}
routingPanel.addEventListener('toggle', () => {
  if (routingPanel.open && !routingGuideSnapshot) action(loadRoutingGuide);
});
$('routing-guide-reload').onclick = () => action(loadRoutingGuide);
$('routing-guide-default').onclick = () => {
  if (!routingGuideSnapshot) return;
  routingEditor.value = routingGuideSnapshot.default_content;
  updateRoutingGuideState();
};
$('routing-guide-save').onclick = () => action(async () => {
  if (!routingGuideSnapshot) throw new Error('请先读取当前规则');
  const result = await rpc('routing_guide_save', {
    content: routingEditor.value, expected_sha256: routingGuideSnapshot.expected_sha256
  });
  routingGuideSnapshot = result;
  routingEditor.value = result.content;
  $('routing-guide-state').textContent = '个人规则 · 已校验 · ' + result.validation.route_count + '条规则';
  updateRoutingGuideState();
  notice('路由规则已保存，下一次组件查询立即生效');
});

// View switching
function view(name) {
  // Handle legacy provider view redirect
  if (name === 'skills') name = 'skill-list';
  if (name === 'providers') {
    name = 'component-management';
    currentComponentView = 'runtime';
  }

  document.querySelectorAll('.view').forEach(el => el.classList.toggle('hidden', el.id !== name));
  document.querySelectorAll('nav button').forEach(el => el.classList.toggle('selected', el.dataset.view === name));

  const titles = {
    overview: '运行概览',
    setup: '首次配置与连接',
    workspaces: '工作区',
    providers: 'MCP 插件',
    'component-management': '组件与 MCP 插件',
    'skill-list': 'Skill Library',
    logs: '日志诊断',
    about: '设置'
  };

  $('heading').textContent = titles[name] || name;
  window.scrollTo(0, 0);

  if (name === 'component-management') {
    renderComponentManagement();
  }
  if (name === 'overview') refreshActivity();
}

// Navigation setup
document.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => view(b.dataset.view)));
document.querySelectorAll('[data-link]').forEach(b => b.addEventListener('click', () => action(() => invoke('official_link', { topic: b.dataset.link }))));

// Update status display with modern badges
function updateStatusDisplay() {
  const runtimeStates = {
    ready: { text: 'MCP 已就绪', type: 'success' },
    starting: { text: '启动中', type: 'warning' },
    stopped: { text: '已停止', type: 'neutral' },
    exited: { text: '异常退出', type: 'error' }
  };

  const tunnelStates = {
    ready: { text: 'Tunnel 已就绪', type: 'success' },
    stopped: { text: '已停止', type: 'neutral' },
    exited: { text: '异常退出', type: 'error' },
    starting_or_disconnected: { text: '启动中或未连接', type: 'warning' },
    disconnected: { text: '连接失败', type: 'error' },
    local_ready_waiting_control_plane: { text: '本地就绪，远端连接待确认', type: 'warning' }
  };

  // Update Runtime status
  const runtimeState = runtimeStates[snapshot.runtime] || { text: '未知状态', type: 'neutral' };
  const runtimeBadge = $('runtime-status');
  runtimeBadge.textContent = runtimeState.text;
  runtimeBadge.className = `status-badge ${runtimeState.type}`;

  const runtimeCard = $('runtime-card');
  runtimeCard.className = `status-card ${runtimeState.type === 'success' ? 'ready' : runtimeState.type === 'error' ? 'error' : ''}`;

  // Update Tunnel status
  const tunnelState = tunnelStates[snapshot.tunnel] || { text: '连接待确认', type: 'neutral' };
  const tunnelBadge = $('tunnel-status');
  tunnelBadge.textContent = tunnelState.text;
  tunnelBadge.className = `status-badge ${tunnelState.type}`;

  const tunnelCard = $('tunnel-card');
  tunnelCard.className = `status-card ${tunnelState.type === 'success' ? 'ready' : tunnelState.type === 'error' ? 'error' : ''}`;

  // A verification is meaningful only for this Manager-owned Runtime session.
  renderVerification();

  // Update system components
  const browserStates = {
    disabled: '未启用',
    component_missing: '组件缺失',
    starting_or_unavailable: '启动中或暂不可用',
    unavailable: '暂不可用',
    edge_missing: '未检测到 Microsoft Edge',
    provider_ready: '组件已连接'
  };
  $('browser-status-display').textContent = browserStates[snapshot.browser] || '检测中';

  $('optional-status-display').textContent = '请在组件管理页面查看组件的真实状态';
}

// Main refresh function
async function refresh() {
  snapshot = await rpc('status');
  const c = snapshot.config;

  $('version').textContent = c.version;

  updateStatusDisplay();

  $('data-directory').textContent = c.data_directory;

  if (!connectionEdited) {
    $('runtime-port').value = c.runtime_port;
    $('health-port').value = c.health_port;
    $('tunnel-id').value = c.tunnel_id;
  }

  $('credential-status').textContent = c.credential_saved ? '已保存加密凭据' : '尚未保存凭据';
  const loginRegistered = await invoke('autostart_status');
  $('autostart').checked = c.autostart && loginRegistered;
  $('autostart-state').textContent = c.autostart && !loginRegistered
    ? '已保存的偏好与 Windows 启动项不一致；请重新勾选以修复。'
    : loginRegistered ? 'Windows 登录项已注册；下次登录将在后台启动服务。' : '未启用登录自启动。';
  $('browser-enabled').checked = c.browser_enabled;
  $('browser-port').value = c.browser_port;

  renderWorkspaces();

  const newError = snapshot.last_error ? friendlyError(snapshot.last_error) : '';
  if (newError && newError !== lastBackendError) notice(newError, true);
  lastBackendError = newError;
}

// Workspace rendering with modern cards
function renderWorkspaces() {
  const list = $('workspace-list');
  list.replaceChildren();
  const roots = snapshot.workspaces.roots;

  $('workspace-list-heading').textContent = `已授权目录（${Object.keys(roots).length} 个 root）`;

  if (!Object.keys(roots).length) {
    const emptyCard = document.createElement('div');
    emptyCard.className = 'card';
    emptyCard.innerHTML = '<div class="card-body"><p class="text-muted">尚未添加额外工作区。PLA 私有默认工作区仍可用于本地验证。</p></div>';
    list.append(emptyCard);
    return;
  }

  for (const [name, root] of Object.entries(roots)) {
    const card = document.createElement('div');
    card.className = 'card mb-md';

    const header = document.createElement('div');
    header.className = 'card-header';

    const title = document.createElement('h3');
    title.className = 'card-title';
    title.textContent = name;

    header.append(title);

    const body = document.createElement('div');
    body.className = 'card-body';

    const path = document.createElement('p');
    path.className = 'text-small mb-sm';
    path.textContent = root.path;

    const permissions = document.createElement('div');
    permissions.style.display = 'flex';
    permissions.style.gap = 'var(--space-md)';
    permissions.style.marginTop = 'var(--space-sm)';

    const createPermBadge = (label, granted) => {
      const badge = document.createElement('span');
      badge.className = `status-badge ${granted ? 'success' : 'neutral'}`;
      badge.textContent = `${label} ${granted ? '✓' : '–'}`;
      return badge;
    };

    permissions.append(
      createPermBadge('读取', root.read),
      createPermBadge('写入', root.write),
      createPermBadge('执行', root.execute)
    );

    body.append(path, permissions);

    const actions = document.createElement('div');
    actions.className = 'card-actions';

    const editBtn = document.createElement('button');
    editBtn.textContent = '编辑';
    editBtn.addEventListener('click', () => editWorkspace(name, root));

    const removeBtn = document.createElement('button');
    removeBtn.textContent = '移除授权';
    removeBtn.addEventListener('click', () => action(async () => {
      if (!window.confirm(`移除 ${name} 的授权？\n目录和文件保留，仅移除 PLA 访问权限。`)) return;
      await rpc('workspace_remove', { name, expected_sha256: snapshot.workspaces.config_sha256 });
      await refresh();
      notice('授权已移除，文件保留。');
    }));

    actions.append(editBtn, removeBtn);

    card.append(header, body, actions);
    list.append(card);
  }
}

// Workspace form management
function resetWorkspaceForm() {
  workspaceEditName = null;
  $('workspace-name').readOnly = false;
  $('workspace-name').value = '';
  $('workspace-path').value = '';
  $('allow-read').checked = true;
  $('allow-write').checked = false;
  $('allow-execute').checked = false;
  $('workspace-form-heading').textContent = '添加新的 root';
  $('save-workspace').textContent = '添加 root';
  $('cancel-workspace-edit').classList.add('hidden');
}

function editWorkspace(name, root) {
  workspaceEditName = name;
  $('workspace-name').value = name;
  $('workspace-name').readOnly = true;
  $('workspace-path').value = root.path;
  $('allow-read').checked = root.read;
  $('allow-write').checked = root.write;
  $('allow-execute').checked = root.execute;
  $('workspace-form-heading').textContent = `编辑 root：${name}`;
  $('save-workspace').textContent = '保存更改';
  $('cancel-workspace-edit').classList.remove('hidden');
  $('workspace-form-heading').scrollIntoView({ block: 'start', behavior: 'smooth' });
}

// Button handlers
$('refresh').onclick = () => action(refresh);

$('save-browser').onclick = () => action(async () => {
  await rpc('configure', {
    browser_enabled: $('browser-enabled').checked,
    browser_port: Number($('browser-port').value)
  });
  await refresh();
  notice('浏览器偏好已保存。启动 Runtime 后会检查真实 Provider 状态。');
});

function serviceAction(command) {
  return action(async () => {
    await rpc(command);
    await refresh();
    notice(command === 'connect' ? 'Tunnel 已启动，等待真实连接检测。' : '服务操作已完成。');
  });
}

for (const command of ['start', 'stop', 'restart', 'connect']) {
  $(command).onclick = () => serviceAction(command);
}

document.querySelectorAll('[data-command]').forEach(button =>
  button.onclick = () => serviceAction(button.dataset.command)
);

$('save-connection').onclick = () => action(async () => {
  await rpc('configure', {
    runtime_port: Number($('runtime-port').value),
    health_port: Number($('health-port').value),
    tunnel_id: $('tunnel-id').value.trim()
  });

  const secret = $('secret').value;
  if (secret) {
    await rpc('credential', { secret });
    $('secret').value = '';
  }

  connectionEdited = false;
  await refresh();
  notice('连接配置已保存。');
});

$('cancel-workspace-edit').onclick = resetWorkspaceForm;

$('save-workspace').onclick = () => action(async () => {
  const name = $('workspace-name').value.trim();
  const editing = workspaceEditName !== null;

  if (!editing && Object.hasOwn(snapshot.workspaces.roots, name)) {
    throw '这个 root 名称已存在。新增目录请使用不同名称；修改原目录请点击它的「编辑」。';
  }

  await rpc('workspace_save', {
    mode: editing ? 'update' : 'create',
    name: editing ? workspaceEditName : name,
    path: $('workspace-path').value.trim(),
    read: $('allow-read').checked,
    write: $('allow-write').checked,
    execute: $('allow-execute').checked,
    expected_sha256: snapshot.workspaces.config_sha256
  });

  resetWorkspaceForm();
  await refresh();
  notice(editing ? 'root 更改已保存，其他授权保持不变。' : '新的 root 已添加，已有授权保持不变。');
});

$('verify').onclick = () => action(async () => {
  const result = await rpc('verify');
  await refresh();
  notice(`真实本地 MCP 调用：${result.local_mcp}。Tunnel 链路及 ChatGPT 调用需分别验收。`);
});

$('complete-setup').onclick = () => action(async () => {
  if (!snapshot.local_mcp_verified) throw '请先完成真实本地工具验证。';
  await rpc('configure', { onboarding_complete: true });
  await refresh();
  view('overview');
  notice('本地配置向导已完成。服务继续运行，请在 ChatGPT 侧完成授权和端到端验收。');
});

$('load-logs').onclick = () => action(async () => {
  $('log-output').textContent = (await rpc('logs')).lines.join('\n') || '暂无日志';
});

$('diagnose').onclick = () => action(async () => {
  const report = await rpc('diagnose');
  $('log-output').textContent = JSON.stringify(report, null, 2);
  notice('诊断报告已保存：' + report.report_path);
});

$('autostart').onchange = () => action(async () => {
  try {
    await invoke('autostart', { enabled: $('autostart').checked });
  } finally {
    await refresh();
  }
  notice('登录启动偏好已保存。');
});

// Signed updater uses a fixed HTTPS endpoint and an embedded Tauri public key.
// Unsigned builds never expose the installation command.
async function checkDesktopUpdate(announce = false) {
  const label = $('desktop-update-state');
  const install = $('install-signed-update');
  install.hidden = true;
  label.textContent = '正在检查 GitHub 稳定版…';
  let signedError = '';
  try {
    const signed = await invoke('signed_update_check', {});
    if (signed.status === 'available') {
      label.textContent = '发现已签名更新 ' + signed.version + '，可在停止服务后安装。';
      install.hidden = false;
      localStorage.setItem('pla-desktop-last-update-check', String(Date.now()));
      if (announce) notice(label.textContent);
      return;
    }
    if (signed.status === 'latest') {
      label.textContent = '已是最新的签名稳定版。';
      localStorage.setItem('pla-desktop-last-update-check', String(Date.now()));
      if (announce) notice(label.textContent);
      return;
    }
  } catch (e) {
    signedError = String(e);
  }
  // Unconfigured unsigned builds and temporarily unavailable metadata fall
  // back to read-only release discovery, never to an unsigned install.
  try {
    const result = await rpc('update_check');
    const latest = result.latest_version || '';
    const messages = {
      latest: '已是最新公开稳定版（' + latest + '）。',
      available_manual: '发现新版本 ' + latest + '；请从官方 Releases 页面手动下载安装。',
      pending_installer: '已发现新版本 ' + latest + '，安装包尚未发布。',
      unavailable: result.message || '网络连接失败，无法检查更新。',
      invalid_release: '最新版本信息格式不符合 PLA 稳定版规则。',
      unsupported_version: '当前版本格式不受支持。'
    };
    label.textContent = messages[result.status] || '更新状态未知。';
    if (signedError && result.status === 'available_manual') {
      label.textContent += '（签名更新通道暂不可用）';
    }
    if (['latest', 'available_manual', 'pending_installer'].includes(result.status)) {
      localStorage.setItem('pla-desktop-last-update-check', String(Date.now()));
    }
    if (result.status === 'available_manual' || announce) notice(label.textContent);
  } catch (e) {
    label.textContent = '暂时无法检查更新，请确认网络或代理连接。';
    if (announce) notice(friendlyError(e), true);
  }
}
$('check-desktop-update').onclick = () => action(() => checkDesktopUpdate(true));
$('install-signed-update').onclick = () => action(async () => {
  if (!window.confirm('安装更新需要退出 PLA。请先停止 Runtime、Tunnel 和所有任务。确定继续吗？')) return;
  const gate = await rpc('update_preflight');
  if (!gate.ready) throw new Error('请先在总览页停止 PLA 服务，并结束运行中的任务，然后再安装更新。');
  $('desktop-update-state').textContent = '正在下载签名更新并校验，成功后将在 Windows 安装器中完成升级…';
  await invoke('signed_update_install', {});
});
setTimeout(() => {
  const previous = Number(localStorage.getItem('pla-desktop-last-update-check')) || 0;
  if (Date.now() - previous >= 24 * 60 * 60 * 1000) checkDesktopUpdate(false);
}, 10000);

$('quit-app').onclick = () => action(() => invoke('quit_app', {}));

// Component Manager Integration
let componentManagerInstance = null;
let currentComponentView = 'runtime';

function initComponentManager() {
  if (typeof ComponentManager !== 'undefined') {
    const appInterface = {
      invoke: async (command, args) => {
        return await rpc(command, args);
      },
      showNotification: (message, type) => {
        notice(message, type === 'error');
      },
      renderCurrentView: () => {
        renderComponentManagement();
      },
      get currentView() { return currentComponentView; },
      set currentView(value) {
        if (['runtime', 'repositories', 'marketplace'].includes(value)) currentComponentView = value;
      }
    };

    componentManagerInstance = new ComponentManager(appInterface);
    window.componentManager = componentManagerInstance;

    componentManagerInstance.init().then(() => {
      if (!$('component-management').classList.contains('hidden') && currentComponentView !== 'runtime') {
        renderComponentManagement();
      }
    }).catch(error => notice(friendlyError(error), true));
  }
}

function renderComponentManagement() {
  if (!componentManagerInstance) {
    initComponentManager();
  }

  const content = $('component-management-content');
  if (!content) return;

  const runtimeSelected = currentComponentView === 'runtime';
  $('providers').classList.toggle('hidden', !runtimeSelected);
  content.classList.toggle('hidden', runtimeSelected);

  for (const tab of ['runtime', 'repositories', 'marketplace']) {
    const selected = currentComponentView === tab;
    $('view-' + tab).classList.toggle('selected', selected);
    $('view-' + tab).setAttribute('aria-pressed', String(selected));
  }

  if (runtimeSelected) {
    if (typeof loadProviders === 'function') action(loadProviders);
    return;
  }

  if (!componentManagerInstance) return;
  content.replaceChildren();

  let viewContent;

  switch (currentComponentView) {
    case 'repositories':
      viewContent = componentManagerInstance.renderRepositoryManager();
      break;
    case 'marketplace':
      viewContent = componentManagerInstance.renderProviderMarketplace();
      break;
    default:
      viewContent = componentManagerInstance.renderProviderMarketplace();
  }

  content.appendChild(viewContent);
}

// Component management view switchers
$('view-runtime').onclick = () => {
  currentComponentView = 'runtime';
  renderComponentManagement();
};

if ($('view-repositories')) {
  $('view-repositories').onclick = () => {
    currentComponentView = 'repositories';
    renderComponentManagement();
  };
}

if ($('view-marketplace')) {
  $('view-marketplace').onclick = () => {
    currentComponentView = 'marketplace';
    renderComponentManagement();
  };
}

// Activity polling never invokes the expensive full status/probe path.
let activityLoading = false;
let lastActivityRows = [];
let lastComponentRows = [];
let lastActivitySuccess = null;
let activityResponse = null;
let activityExpanded = false;
const ACTIVITY_PREVIEW_COUNT = 6;
$('activity-filter').onchange = () => renderActivity(activityResponse?.available === true);
$('activity-more').onclick = () => {
  activityExpanded = !activityExpanded;
  renderActivity(activityResponse?.available === true);
};

function renderVerification() {
  const badge = $('verified-status');
  const card = $('verification-card');
  let label = '等待Runtime状态';
  let kind = 'neutral';

  if (!snapshot || snapshot.runtime === 'stopped' || snapshot.runtime === 'exited'
      || (activityResponse && !activityResponse.runtime_alive)) {
    label = '未连接';
  } else if (snapshot.runtime !== 'ready') {
    label = '等待Runtime就绪';
  } else if (!activityResponse || activityResponse.available !== true) {
    label = '活动状态不可用';
    kind = 'warning';
  } else {
    const start = Date.parse(activityResponse.runtime_started_at);
    // Never trust old events or a missing session marker.
    const session = Number.isFinite(start)
      ? activityResponse.activities.filter(entry => {
          const when = Date.parse(entry.started_at);
          return Number.isFinite(when) && when >= start;
        })
      : [];
    const latest = session[0];
    if (latest && latest.outcome === 'failed') {
      label = '最近调用失败';
      kind = 'error';
    } else if (latest && latest.outcome === 'success') {
      label = '本地调用已验证';
      kind = 'success';
    } else if (latest) {
      label = '调用进行中';
      kind = 'warning';
    } else if (snapshot.local_mcp_verified && Number.isFinite(start)) {
      // Keep the explicit diagnostic available as another valid proof.
      label = '本地诊断已通过';
      kind = 'success';
    } else {
      label = '等待工具调用';
    }
  }
  badge.textContent = label;
  badge.className = 'status-badge ' + kind;
  card.className = 'status-card ' + (
    kind === 'success' ? 'ready' : kind === 'error' ? 'error' : ''
  );
}

function activityClock(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleTimeString('zh-CN', { hour12: false });
}

function activityProviderName(provider) {
  const names = { browser: 'Browser', core: 'PLA Core', runtime: 'PLA Runtime',
    windows: 'Windows', drawio: 'draw.io', 'skill-library': 'Skill Library' };
  return names[provider] || provider;
}

function activityDescription(activity) {
  if (activity.source === 'capability') {
    const provider = activity.provider;
    const capability = activity.capability || activity.tool;
    const action = provider && capability.startsWith(provider + '.')
      ? capability.slice(provider.length + 1) : capability;
    return { title: provider ? activityProviderName(provider) : '组件能力',
      detail: action + ' · 组件调用' };
  }
  if (activity.tool === 'run_process') {
    return { title: activity.program ? '本地程序 · ' + activity.program : '本地程序执行',
      detail: 'run_process · ' + (activity.source === 'native_mcp' ? 'MCP工具' : '内部Action') };
  }
  return { title: activity.tool,
    detail: activity.source === 'native_mcp' ? 'MCP工具' : '内部Action' };
}

function activityDetailLine(container, name, value) {
  if (value === null || value === undefined || value === '') return;
  const line = document.createElement('div');
  line.className = 'activity-detail-line';
  const label = document.createElement('span');
  label.textContent = name;
  const content = document.createElement('span');
  content.textContent = String(value);
  line.append(label, content);
  container.append(line);
}

function renderActivity(available) {
  const list = $('activity-list');
  const opened = new Set([...list.querySelectorAll('details.activity-row[open]')]
    .map(el => el.dataset.activityId));
  list.replaceChildren();
  const filter = $('activity-filter').value;
  const filtered = (filter === 'capability' ? lastComponentRows : lastActivityRows)
    .filter(a => filter === 'all'
    || (filter === 'capability' && a.source === 'capability')
    || (filter === 'process' && a.source !== 'capability'));
  const visible = activityExpanded ? filtered : filtered.slice(0, ACTIVITY_PREVIEW_COUNT);
  if (!available && !lastActivityRows.length) {
    list.textContent = '活动数据暂不可用';
  } else if (!filtered.length) {
    list.textContent = '暂无符合条件的活动';
  } else {
    for (const activity of visible) {
      const row = document.createElement('details');
      row.className = 'activity-row';
      row.dataset.activityId = activity.id;
      row.open = opened.has(activity.id);
      const summary = document.createElement('summary');
      summary.className = 'activity-summary';
      const meta = document.createElement('div');
      meta.className = 'activity-meta';
      const tool = document.createElement('strong');
      tool.className = 'activity-tool';
      const description = activityDescription(activity);
      tool.textContent = description.title;
      const detail = document.createElement('span');
      detail.className = 'activity-subtitle';
      detail.textContent = description.detail;
      const when = document.createElement('small');
      when.className = 'text-muted';
      when.textContent = activityClock(activity.started_at);
      meta.append(tool, detail, when);
      const result = document.createElement('span');
      const age = Date.now() - new Date(activity.started_at).getTime();
      result.className = 'activity-outcome ' + activity.outcome;
      result.textContent = activity.outcome === 'success' ? '成功'
        : activity.outcome === 'failed' ? '失败'
        : age > 120000 ? '状态未知' : '进行中';
      summary.append(meta, result);
      const body = document.createElement('div');
      body.className = 'activity-details';
      activityDetailLine(body, '工具入口', activity.tool);
      activityDetailLine(body, '组件ID', activity.provider);
      activityDetailLine(body, '能力ID', activity.capability);
      activityDetailLine(body, '可执行程序', activity.program);
      activityDetailLine(body, '开始', activity.started_at);
      activityDetailLine(body, '结束', activity.finished_at);
      activityDetailLine(body, '记录ID', activity.id);
      row.append(summary, body);
      list.append(row);
    }
  }
  const more = $('activity-more');
  more.hidden = filtered.length <= ACTIVITY_PREVIEW_COUNT;
  more.textContent = activityExpanded ? '收起' : '查看更多（' + filtered.length + '条）';
  $('activity-fetched').textContent = lastActivitySuccess
    ? (available ? '最近读取：' : '数据未更新，最后成功读取：') + activityClock(lastActivitySuccess)
    : '尚未成功读取';
}

function renderConversationSummary(conversations) {
  const ready = conversations?.available === true;
  $('conversation-count').textContent = ready
    ? String(conversations.identified_count) + (conversations.truncated ? '+' : '')
    : '—';
  $('conversation-unknown').textContent = ready
    ? String(conversations.unattributed_calls ?? 0)
    : '—';
  $('conversation-period').textContent = ready ? '近60秒' : '统计不可用';
}

async function refreshActivity() {
  if (activityLoading || $('overview').classList.contains('hidden')) return;
  activityLoading = true;
  try {
    const response = await rpc('activity_recent');
    if (response && response.available === true && Array.isArray(response.activities)) {
      const conversations = response.conversations;
      renderConversationSummary(conversations);
      lastActivityRows = response.activities;
      lastComponentRows = Array.isArray(response.component_activities) ? response.component_activities : [];
      activityResponse = response;
      lastActivitySuccess = new Date().toISOString();
      renderActivity(true);
      renderVerification();
    } else {
      renderConversationSummary(null);
      activityResponse = null;
      renderActivity(false);
      renderVerification();
    }
  } catch (_error) {
    renderConversationSummary(null);
    activityResponse = null;
    renderActivity(false);
    renderVerification();
  } finally {
    activityLoading = false;
  }
}

// Initialize on load
if (typeof ComponentManager !== 'undefined') {
  initComponentManager();
}

// Initialize app
action(async () => {
  await refresh();
  if (!$('overview').classList.contains('hidden')) await refreshActivity();
  if (!snapshot.config.onboarding_complete) {
    view('setup');
  }
});

// Auto-refresh on overview page
setInterval(() => {
  if (!busy && !$('overview').classList.contains('hidden')) {
    action(refresh);
  }
}, 10000);

// Light, UI-only read. Do not run status probes or global button disabling.
setInterval(refreshActivity, 3000);
