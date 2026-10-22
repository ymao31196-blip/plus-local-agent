'use strict';
// Modern Provider Card Rendering - V2
const node = (tag,text,className) => {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
const button = (text,fn) => {const el=node('button',text);el.onclick=()=>action(fn);return el;};
const output = (id,value) => {$(id).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);};
const unwrap = value => value?.data ?? value;

let providerCatalog, importPreview, installPreview;
let watchedInstallJob=null;
let customPackagePreview=null;
let starterBundlePreview=null;
let providerEditVersion, providerEditPreview;
let skillInventory=[];
// Modern status badge creator
function createStatusBadge(text, type = 'neutral') {
  const badge = node('span', text, `status-badge ${type}`);
  return badge;
}

// Provider status analyzer - returns semantic status information
function analyzeProviderStatus(row) {
  const lifecycle = row.lifecycle;
  const installedFiles = row.execution_files_present === true;
  const disabled = lifecycle?.enabled === false || row.temporarily_disabled === true;
  const live = lifecycle?.state === 'ready' && !disabled && lifecycle?.enabled === true;
  const failed = !disabled && ['error', 'failed'].includes(lifecycle?.state);

  let installStatus, enableStatus, connectionStatus;
  let primaryAction = null;
  let warnings = [];
  let errors = [];

  // Analyze installation
  if (!installedFiles) {
    installStatus = { type: 'error', text: '未安装', detail: '缺少运行组件' };
    errors.push(row.provider_id === 'skill-library'
      ? 'Skill Library尚未安装，可使用随Desktop提供的审核安装包一键安装。'
      : row.directory_exists === false
        ? 'MCP 源码目录缺失，需要安装依赖。'
        : '缺少运行文件，需要先安装或检查系统前提。');
    primaryAction = { label: '安装组件', type: 'install' };
  } else {
    installStatus = { type: 'success', text: '已安装', detail: row.repository_installation ? `v${row.repository_installation.version}` : null };
  }

  // Analyze enable status
  if (!installedFiles) {
    enableStatus = { type: 'neutral', text: '未启用', detail: '需先安装' };
  } else if (disabled) {
    enableStatus = { type: 'neutral', text: '已停用', detail: null };
    if (!primaryAction) primaryAction = { label: '启用并连接', type: 'enable' };
  } else if (lifecycle?.enabled === true) {
    enableStatus = { type: 'success', text: '已启用', detail: null };
  } else {
    enableStatus = { type: 'warning', text: '待连接', detail: null };
    if (!primaryAction) primaryAction = { label: '启用并连接', type: 'enable' };
  }

  // Analyze connection status
  if (!installedFiles) {
    connectionStatus = { type: 'neutral', text: '缺少运行组件', detail: '需先安装' };
  } else if (disabled) {
    connectionStatus = { type: 'neutral', text: '未连接', detail: '已停用' };
    warnings.push('Provider 已停用；历史工具发现记录不代表当前可调用。请点击「启用并连接」，无需重新安装依赖。');
  } else if (live) {
    connectionStatus = { type: 'success', text: 'MCP 已连接', detail: lifecycle?.tool_count ? `${lifecycle.tool_count} 工具` : null };
    if (!primaryAction) primaryAction = { label: '停用', type: 'disable' };
  } else if (failed) {
    connectionStatus = { type: 'error', text: '连接失败', detail: null };
    errors.push(`MCP 启动失败：${lifecycle?.error_message || '连接未建立'}。可尝试重连；持续失败请停用后修复依赖。`);
    primaryAction = { label: '重试连接', type: 'reload' };
  } else {
    connectionStatus = { type: 'warning', text: '待连接', detail: null };
  }

  if (row.manifest_missing) {
    errors.push('安装记录仍在，组件清单已缺失；请检查安装完整性后处理。');
  }

  return { installStatus, enableStatus, connectionStatus, primaryAction, warnings, errors, installedFiles, disabled, live, failed };
}

// A managed Desktop companion shares the components page but does not expose
// ChatGPT MCP tools. Lifecycle is owned by Desktop Runtime, never by AI calls.
function renderPetCompanion(row) {
  const card = node('div', undefined, 'provider-card');
  card.dataset.companion = 'agent-pet';
  const header = node('div', undefined, 'provider-card-header');
  header.append(node('h3', 'PLA Agent Pet · 桌宠'));
  const badges = node('div', undefined, 'provider-statuses');
  const stateText = {
    running: '运行中', waiting_runtime: '等待 Runtime', disabled: '已停用',
    not_installed: '缺少素材', not_running: '已关闭',
  };
  badges.append(
    createStatusBadge('本地伴随组件', 'neutral'),
    createStatusBadge(stateText[row.state] || '未知状态', row.state === 'running' ? 'success' : 'neutral')
  );
  header.append(badges);
  card.append(header);
  card.append(node('p', 'Q版角色实时展示PLA工具调用；不占用ChatGPT工具名额。', 'provider-description'));
  if (!row.installed) {
    card.append(node('div', '首次启用前需准备自有或获授权的32张PNG素材，详见桌宠使用说明；不会从网络自动下载。', 'provider-warning'));
  }
  if (row.error) card.append(node('div', row.error, 'provider-warning'));
  const actions = node('div', undefined, 'provider-quick-actions');
  const label = row.enabled ? '停用桌宠' : '启用桌宠';
  const toggle = button(label, async () => {
    const enabled = !row.enabled;
    if (!window.confirm(enabled
      ? '启用PLA桌宠？Runtime运行时将在桌面显示宠物；不会启动额外的ChatGPT工具。'
      : '停用PLA桌宠？将关闭由PLA管理的桌宠窗口。')) return;
    const result = await rpc('pet_action', { enabled, confirmed: true });
    output('provider-result', result);
    await loadProviders();
    notice(result.state === 'waiting_runtime' ? '桌宠已启用，启动Runtime后会自动出现。'
      : result.state === 'running' ? '桌宠已启动。'
      : '桌宠已停用。');
  });
  toggle.disabled = !row.enabled && !row.installed;
  if (!row.enabled && row.installed) toggle.classList.add('primary');
  actions.append(toggle);
  card.append(actions);
  return card;
}

// Render modern provider card
function renderProviderCard(row) {
  const status = analyzeProviderStatus(row);
  const card = node('div', undefined, 'provider-card');

  // Card Header
  const header = node('div', undefined, 'provider-card-header');
  const title = node('h3', row.provider_id);

  const statuses = node('div', undefined, 'provider-statuses');
  statuses.append(
    createStatusBadge(`连接：${status.connectionStatus.text}`, status.connectionStatus.type),
    createStatusBadge(`启停：${status.enableStatus.text}`, status.enableStatus.type === 'success' ? 'success' : 'neutral')
  );

  header.append(title, statuses);
  card.append(header);

  // Repository info
  if (row.repository_installation) {
    const repoInfo = node('div', undefined, 'provider-repo-info');
    repoInfo.append(
      node('span', '仓库安装'),
      node('code', `v${row.repository_installation.version}`)
    );
    card.append(repoInfo);
  }

  // Tool count
  if (status.live && row.lifecycle?.tool_count) {
    const toolCount = node('div', undefined, 'provider-tool-count');
    toolCount.textContent = `${row.lifecycle.tool_count} 个工具可用`;
    card.append(toolCount);
  }

  // Errors
  for (const error of status.errors) {
    card.append(node('div', error, 'provider-error'));
  }

  // Warnings
  for (const warning of status.warnings) {
    card.append(node('div', warning, 'provider-warning'));
  }

  // Quick Actions
  const quickActions = node('div', undefined, 'provider-quick-actions');

  if (status.primaryAction) {
    const btn = button(status.primaryAction.label, async () => {
      await handlePrimaryAction(row, status.primaryAction.type, status);
    });
    if (status.primaryAction.type === 'enable' || status.primaryAction.type === 'install') {
      btn.classList.add('primary');
    }
    quickActions.append(btn);
  }

  // A failed, still enabled provider must always offer a visible disable
  // action in addition to retry. Repeated retries alone may be ineffective.
  if (status.failed && row.lifecycle?.enabled === true) {
    quickActions.append(button('停用', async () => handlePrimaryAction(row, 'disable', status)));
    if (row.install_supported) {
      quickActions.append(button('修复依赖', async () => {
        $('install-provider').value = row.provider_id;
        if (row.provider_id !== 'skill-library') $('skill-package').value = '';
        $('install-panel').scrollIntoView({ block: 'start' });
        await installSelectedComponent();
      }));
    }
  }

  // Repository actions
  if (row.repository_installation) {
    quickActions.append(
      button('检查完整性', async () => {
        await window.componentManager.handleHealthCheck({ provider_id: row.provider_id, name: row.provider_id });
      }),
      button('卸载', async () => {
        const result = await window.componentManager.uninstallProvider(row.provider_id);
        if (result) {
          await loadProviders();
          notice(`${row.provider_id} 已卸载；未登记文件按原规则保留。`);
        }
      })
    );
  }

  card.append(quickActions);

  // Advanced section
  const advanced = node('details', undefined, 'provider-advanced');
  advanced.append(node('summary', '详细信息与高级管理'));

  const advancedContent = node('div', undefined, 'provider-advanced-content');

  // Technical info
  const techInfo = node('div', undefined, 'provider-tech-info');
  techInfo.append(
    createTechInfoRow('运行类型', row.runtime_kind),
    createTechInfoRow('启动偏好', row.requested_enabled ? '启用' : '停用'),
    createTechInfoRow('已发现工具', row.lifecycle?.tool_count?.toString() ?? '未检测')
  );

  if (row.url || row.python || row.command) {
    techInfo.append(createTechInfoRow('执行命令', row.url || row.python || row.command));
  }

  advancedContent.append(techInfo);

  // Advanced actions
  const advancedActions = node('div', undefined, 'provider-advanced-actions');

  if (status.installedFiles) {
    advancedActions.append(
      button('工具与参数', async () => {
        const result = await rpc('provider_details', { provider_id: row.provider_id });
        renderTools(result);
        output('provider-result', result);
        $('provider-tools-panel').scrollIntoView({ block: 'start' });
      }),
      button('查看 / 编辑清单', async () => {
        providerEditVersion = await rpc('provider_configuration', { provider_id: row.provider_id, content: null, confirmed: false, expected_sha256: null, expected_content_sha256: null });
        providerEditPreview = null;
        $('edit-provider-id').textContent = row.provider_id;
        $('edit-manifest-content').value = providerEditVersion.content;
        output('edit-manifest-preview', providerEditVersion.sha256);
        $('edit-manifest-panel').classList.remove('hidden');
        $('edit-manifest-panel').scrollIntoView({ block: 'start' });
      })
    );
  }

  if (row.install_supported) {
    advancedActions.append(
      button(status.installedFiles ? '检查 / 修复依赖' : '安装组件', async () => {
        $('install-provider').value = row.provider_id;
        if (row.provider_id !== 'skill-library') $('skill-package').value = '';
        $('install-panel').scrollIntoView({ block: 'start' });
        $('install-live').textContent = '点击「安装 / 修复」会自动校验安装计划，并在确认窗口展示来源和目标目录。';
      })
    );
  } else if (!status.installedFiles) {
    advancedContent.append(node('div', '此清单暂无受控安装规格。可在下方接入自选 MCP 依赖，或检查外部软件前提。', 'provider-info'));
  }

  advancedContent.append(advancedActions);
  advanced.append(advancedContent);
  card.append(advanced);

  return card;
}

function createTechInfoRow(label, value) {
  const row = node('div', undefined, 'provider-tech-info-row');
  row.append(
    node('span', label, 'provider-tech-info-label'),
    node('span', value, 'provider-tech-info-value')
  );
  return row;
}

async function handlePrimaryAction(row, actionType, status) {
  switch (actionType) {
    case 'install':
      $('install-provider').value = row.provider_id;
      if (row.provider_id !== 'skill-library') $('skill-package').value = '';
      $('install-panel').scrollIntoView({ block: 'start' });
      await installSelectedComponent();
      break;

    case 'enable':
      if (!window.confirm(`启用 ${row.provider_id} 并连接真实 MCP？\n此组件可能访问本机资源，请确认其工具权限。`)) return;
      const enableResult = await rpc('provider_action', { action: 'enable', provider_id: row.provider_id, confirmed: true });
      output('provider-result', enableResult);
      await loadProviders();
      notice(enableResult.status === 'selection_saved' ? '启用偏好已保存；启动 Runtime 后才会连接。' : `${row.provider_id} 已发起连接；请以实际 MCP 状态为准。`);
      break;

    case 'disable':
      if (!window.confirm(`停用 ${row.provider_id}？\n工具调用将立即变为不可用；安装文件和 Skill 缓存保留。`)) return;
      const disableResult = await rpc('provider_action', { action: 'disable', provider_id: row.provider_id, confirmed: true });
      output('provider-result', disableResult);
      await loadProviders();
      notice(`${row.provider_id} 已执行停用，实际状态见组件卡片。`);
      break;

    case 'reload':
      if (!window.confirm(`重新发现 ${row.provider_id} 的 MCP 工具？\n${row.lifecycle?.error_message || '此前连接失败'}`)) return;
      const reloadResult = await rpc('provider_action', { action: 'reload', provider_id: row.provider_id, confirmed: true });
      output('provider-result', reloadResult);
      await loadProviders();
      break;
  }
}

async function loadProviders() {
  providerCatalog = await rpc('provider_catalog');
  const list = $('provider-list');
  list.replaceChildren();
  $('provider-path').textContent = providerCatalog.manifest_directory;
  $('provider-observation').textContent = providerCatalog.runtime_observed
    ? '状态来自当前 Runtime 的真实 MCP 发现'
    : 'Runtime 未运行；以下仅表示配置和依赖存在情况';

  for (const row of providerCatalog.providers) {
    list.append(renderProviderCard(row));
  }
  // A desktop-only companion is intentionally not a fake MCP provider.
  try {
    list.append(renderPetCompanion(await rpc('pet_status')));
  } catch (error) {
    console.warn('Agent Pet component status unavailable', error);
  }

  await refreshInstallationStatus();
}

async function refreshInstallationStatus() {
  const jobs = await rpc('installation_status');
  output('install-jobs', jobs.jobs.length ? jobs : `当前无执行中的安装任务。已有安装记录：${(jobs.installed_receipts || []).join('、') || '无'}`);

  const running = jobs.jobs.find(job => job.state === 'running');
  const finished = watchedInstallJob && jobs.jobs.find(job => job.job === watchedInstallJob && job.state !== 'running');

  if (finished) {
    watchedInstallJob = null;
    $('install-live').textContent = finished.state === 'installed'
      ? `${finished.provider_id} 的依赖已安装；请检查文件后单独启用，验证真实 MCP 工具。`
      : `${finished.provider_id} 安装失败（退出码 ${finished.exit_code}）；查看下方日志，可修复后重试。`;
    $('install-live').className = finished.state === 'installed' ? 'installation-status success' : 'installation-status error';
    await loadProviders();
    return;
  }

  if (running) {
    $('install-live').textContent = `${running.provider_id} 正在安装（PID ${running.pid}）…请查看任务日志，安装过程中勿重复启动。`;
    $('install-live').className = 'installation-status';
  }
}

function renderTools(result) {
  const list = $('provider-tools');
  list.replaceChildren();

  if (!result.capabilities || !result.capabilities.length) {
    list.append(node('p', '未发现工具', 'text-muted'));
    return;
  }

  for (const tool of result.capabilities) {
    const detail = node('details');
    const summary = node('summary');

    const badge = createStatusBadge(tool.available ? '可用' : '不可用', tool.available ? 'success' : 'neutral');
    const riskBadge = node('span', tool.risk_level, 'status-badge neutral');

    summary.append(
      node('strong', tool.id),
      document.createTextNode(' '),
      badge,
      document.createTextNode(' '),
      riskBadge
    );

    detail.append(
      summary,
      node('p', tool.description),
      node('pre', JSON.stringify(tool, null, 2))
    );
    list.append(detail);
  }
}

async function previewInstall({ detail = true } = {}) {
  installPreview = null;
  try {
    const provider_id = $('install-provider').value.trim();
    const skill_package = $('skill-package').value.trim() || null;

    if (!provider_id) throw '请选择要安装的组件。';
    if (provider_id !== 'skill-library' && skill_package) throw '非 Skill Library 组件请清空本地包路径。';

    $('install-live').textContent = `正在核对 ${provider_id} 的版本、来源与安装条件…`;
    $('install-live').className = 'installation-status';

    installPreview = await rpc('provider_install', { provider_id, skill_package, confirmed: false, expected_sha256: null });
    output('install-preview', installPreview);

    if (detail) {
      $('install-live').textContent = `${provider_id} 依赖已验证。点击「安装 / 修复」将展示一次安装确认，无须再打开技术计划。`;
      $('install-live').className = 'installation-status success';
    }
    return installPreview;
  } catch (error) {
    $('install-live').textContent = '安装条件不满足：' + friendlyError(error);
    $('install-live').className = 'installation-status error';
    throw error;
  }
}

// Bundle management
$('preview-bundle').onclick = () => action(async () => {
  starterBundlePreview = null;
  starterBundlePreview = await rpc('provider_bundle', { confirmed: false, expected_sha256: null });
  output('bundle-preview', starterBundlePreview);
  $('bundle-status').textContent = '已审核 7 项常用组件的锁定依赖；尚未安装。';
  $('bundle-status').className = 'installation-status';
});

$('start-bundle').onclick = () => action(async () => {
  const running = await rpc('installation_status');
  if ((running.jobs || []).some(job => job.state === 'running')) throw '已有组件正在安装，请等待完成。';

  starterBundlePreview = await rpc('provider_bundle', { confirmed: false, expected_sha256: null });
  output('bundle-preview', starterBundlePreview);

  const source = starterBundlePreview.plans?.map(row => row.provider_id).join('、') || starterBundlePreview.providers?.join('、');

  if (!window.confirm(`确认一次性准备 7 个常用 MCP？\n${source}\n\n已审核依赖规格；可能联网下载较多文件。安装后仍需单独启用。\n计划 SHA-256：${starterBundlePreview.sha256}`)) return;

  const result = await rpc('provider_bundle', { confirmed: true, expected_sha256: starterBundlePreview.sha256 });
  starterBundlePreview = null;
  watchedInstallJob = result.job || null;

  $('bundle-status').textContent = result.started ? '已启动常用组件准备任务；请查看下面的安装任务状态。' : '没有启动，请查看安装日志。';
  $('bundle-status').className = result.started ? 'installation-status success' : 'installation-status error';
  output('bundle-preview', result);
  $('install-panel').scrollIntoView({ block: 'start' });
  await refreshInstallationStatus();
});

$('reload-providers').onclick = () => action(loadProviders);
$('rescan-providers').onclick = () => action(async () => {
  if (!window.confirm('重扫并应用已选择的 Provider 清单变化？Runtime 主服务保持运行。')) return;
  output('provider-result', await rpc('provider_action', { action: 'rescan', provider_id: null, confirmed: true }));
  await loadProviders();
});

// Manifest import
$('preview-import').onclick = () => action(async () => {
  importPreview = await rpc('provider_import', { content: $('manifest-content').value, confirmed: false, expected_sha256: null });
  output('manifest-preview', importPreview);
});

$('apply-import').onclick = () => action(async () => {
  if (!importPreview) throw '请先预览当前清单。';
  if (!window.confirm(`保存 ${importPreview.provider_id} 的这份已审阅清单？\nSHA-256 ${importPreview.sha256}\n保存后仍需单独启用。`)) return;
  output('manifest-preview', await rpc('provider_import', { content: $('manifest-content').value, confirmed: true, expected_sha256: importPreview.sha256 }));
  importPreview = null;
  await loadProviders();
});

// Manifest editing
$('preview-manifest-edit').onclick = () => action(async () => {
  if (!providerEditVersion) throw '请先选择要编辑的组件。';
  providerEditPreview = await rpc('provider_configuration', {
    provider_id: providerEditVersion.provider_id,
    content: $('edit-manifest-content').value,
    confirmed: false,
    expected_sha256: providerEditVersion.sha256,
    expected_content_sha256: null
  });
  output('edit-manifest-preview', providerEditPreview);
});

$('save-manifest-edit').onclick = () => action(async () => {
  if (!providerEditPreview) throw '请先预览当前修改。';
  if (!window.confirm(`保存 ${providerEditVersion.provider_id} 的已审阅配置？\n${providerEditPreview.content_sha256}\n保存后需单独重载或重扫。`)) return;
  output('edit-manifest-preview', await rpc('provider_configuration', {
    provider_id: providerEditVersion.provider_id,
    content: $('edit-manifest-content').value,
    confirmed: true,
    expected_sha256: providerEditVersion.sha256,
    expected_content_sha256: providerEditPreview.content_sha256
  }));
  providerEditVersion = null;
  providerEditPreview = null;
  await loadProviders();
});

// Custom package management
function customPackageArgs() {
  return {
    provider_id: $('custom-package-id').value.trim(),
    package_kind: $('custom-package-kind').value,
    packages: $('custom-package-pins').value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
    confirmed: false,
    expected_sha256: null
  };
}

$('custom-package-preview').onclick = () => action(async () => {
  const args = customPackageArgs();
  customPackagePreview = null;
  customPackagePreview = await rpc('provider_package', args);
  output('custom-package-result', customPackagePreview);
});

$('custom-package-save').onclick = () => action(async () => {
  const args = customPackageArgs();
  if (!customPackagePreview || args.provider_id !== customPackagePreview.provider_id ||
      args.package_kind !== customPackagePreview.package_kind ||
      JSON.stringify(args.packages) !== JSON.stringify(customPackagePreview.packages))
    throw '依赖清单已改变，请重新审核。';
  if (!window.confirm(`登记 ${args.provider_id} 的 ${args.package_kind} 依赖？\n${args.packages.join('\n')}\n\n第三方依赖包含可执行代码，安装后仍需单独启用 MCP。`)) return;
  const result = await rpc('provider_package', { ...args, confirmed: true, expected_sha256: customPackagePreview.sha256 });
  customPackagePreview = null;
  output('custom-package-result', result);
  $('install-provider').value = result.provider_id;
  $('skill-package').value = '';
  await loadProviders();
  $('install-panel').scrollIntoView({ block: 'start' });
  $('install-live').textContent = '自定义依赖已登记。点击「安装 / 修复」，系统会自动复核依赖并让你确认一次。';
  $('install-live').className = 'installation-status success';
});

$('preview-install').onclick = () => action(previewInstall);

function installReviewText(plan, needsDisable = false) {
  const specs = (plan.specs || []).map(item => `${item.name}\n${(item.content || '').trim()}`).join('\n\n');
  const disconnect = needsDisable ? '当前组件已启用（包括连接失败的状态）。确认后将先停用该组件，再安装依赖。\n\n' : '';
  return `确认安装 / 修复 ${plan.provider_id}？\n\n${disconnect}来源：${plan.skill_package_origin === 'bundled' ? '随 Desktop 提供的审核 Skill Library Wheel' : plan.skill_package || plan.source || '经审阅的内置组件'}\n目标：${plan.environment}\n\n固定依赖：\n${specs.slice(0, 2200)}${specs.length > 2200 ? '…（详细清单可在下方查看）' : ''}\n\n计划 SHA-256：${plan.sha256}\n\n可能需要联网和运行第三方程序。安装完成后仍需单独启用 MCP。`;
}

async function installSelectedComponent() {
  try {
    const jobs = await rpc('installation_status');
    if ((jobs.jobs || []).some(job => job.state === 'running')) throw '已有组件正在安装，完成后再重试。';

    const plan = await previewInstall({ detail: false });
    const catalog = await rpc('provider_catalog');
    const active = catalog.providers?.find(row => row.provider_id === plan.provider_id);
    const needsDisable = active?.lifecycle?.enabled === true;

    if (!window.confirm(installReviewText(plan, needsDisable))) {
      $('install-live').textContent = '已取消安装，组件及配置保持不变。';
      $('install-live').className = 'installation-status';
      return;
    }

    if (needsDisable) {
      $('install-live').textContent = `正在停用 ${plan.provider_id}，准备修复依赖…`;
      $('install-live').className = 'installation-status';
      await rpc('provider_action', { action: 'disable', provider_id: plan.provider_id, confirmed: true });
    }

    const result = await rpc('provider_install', {
      provider_id: plan.provider_id,
      skill_package: plan.skill_package_origin === 'bundled' ? null : plan.skill_package || null,
      confirmed: true,
      expected_sha256: plan.sha256
    });

    output('install-preview', result);
    installPreview = null;
    watchedInstallJob = result.job || null;

    $('install-live').textContent = result.started
      ? `${result.provider_id} 安装任务已启动；安装成功后可单独启用并检查 MCP 连接。`
      : '没有启动安装，请检查安装状态。';
    $('install-live').className = result.started ? 'installation-status success' : 'installation-status error';
    await refreshInstallationStatus();
  } catch (error) {
    $('install-live').textContent = '安装未启动：' + friendlyError(error);
    $('install-live').className = 'installation-status error';
    throw error;
  }
}

$('start-install').onclick = () => action(installSelectedComponent);
$('installation-refresh').onclick = () => action(refreshInstallationStatus);

// Auto-refresh installation status
setInterval(() => {
  if (!$('component-management').classList.contains('hidden') &&
      !$('providers').classList.contains('hidden') &&
      !busy) {
    action(refreshInstallationStatus);
  }
}, 2000);

// Skill Library user interface lives in skill-library-page.js; MCP capabilities remain unchanged.
