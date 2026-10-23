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
  $('autostart').checked = c.autostart;
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
let lastActivitySuccess = null;
let activityResponse = null;

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

function renderActivity(available) {
  const list = $('activity-list');
  list.replaceChildren();
  if (!available && !lastActivityRows.length) {
    list.textContent = '活动数据暂不可用';
  } else if (!lastActivityRows.length) {
    list.textContent = '暂无近期工具活动';
  } else {
    for (const activity of lastActivityRows) {
      const row = document.createElement('div');
      row.className = 'activity-row';
      const meta = document.createElement('div');
      meta.className = 'activity-meta';
      const tool = document.createElement('strong');
      tool.className = 'activity-tool';
      tool.textContent = activity.tool;
      const when = document.createElement('small');
      when.className = 'text-muted';
      when.textContent = activityClock(activity.started_at) + ' · ' + (
        { native_mcp: 'MCP工具', local_action: '内部Action', capability: '组件能力' }[activity.source] || '本地执行'
      );
      meta.append(tool, when);
      const result = document.createElement('span');
      const age = Date.now() - new Date(activity.started_at).getTime();
      const state = activity.outcome === 'success' ? '成功'
        : activity.outcome === 'failed' ? '失败'
        : age > 120000 ? '结束状态未知' : '尚无结束记录';
      result.className = 'activity-outcome ' + activity.outcome;
      result.textContent = state;
      row.append(meta, result);
      list.append(row);
    }
  }
  $('activity-fetched').textContent = lastActivitySuccess
    ? (available ? '最近读取：' : '数据未更新，最后成功读取：') + activityClock(lastActivitySuccess)
    : '尚未成功读取';
}

async function refreshActivity() {
  if (activityLoading || $('overview').classList.contains('hidden')) return;
  activityLoading = true;
  try {
    const response = await rpc('activity_recent');
    if (response && response.available === true && Array.isArray(response.activities)) {
      lastActivityRows = response.activities;
      activityResponse = response;
      lastActivitySuccess = new Date().toISOString();
      renderActivity(true);
      renderVerification();
    } else {
      activityResponse = null;
      renderActivity(false);
      renderVerification();
    }
  } catch (_error) {
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
