'use strict';
// Modern Provider Card Rendering - V2
const node = (tag,text,className) => {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
const button = (text,fn) => {const el=node('button',text);el.onclick=()=>action(fn);return el;};
const output = (id,value) => {$(id).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);};
const unwrap = value => value?.data ?? value;

let providerCatalog, importPreview, installPreview, skillDescriptors=[], skillInputs=[], lastSkillResult;
let watchedInstallJob=null;
let customPackagePreview=null;
let starterBundlePreview=null;
let providerEditVersion, providerEditPreview;
let skillInventory=[], skillInventorySources=[];

const skillLabels={manage:'添加、编辑或移除来源',sources:'来源与缓存状态',sync:'同步来源',find:'搜索并加载 Skill',load:'阅读完整 Skill',resource:'阅读附属资源',validate:'验证 Skill',prepare:'准备创作草稿','apply-local':'应用已审阅草稿',states:'单个 Skill 启用状态',toggle:'启用或停用 Skill','plan-change':'预览改名或删除','apply-change':'应用改名或删除','publish-plan':'规划 GitHub 发布','restore-local':'恢复已归档 Skill','publication-prepare':'预览向 Git 仓库转移','publication-apply':'应用已审阅转移'};
const groups={sources:['sources','manage','sync'],browse:['states','find','load','resource','validate','toggle'],author:['prepare','apply-local','plan-change','apply-change','restore-local'],publish:['publish-plan','publication-prepare','publication-apply']};
const fieldLabels={action:'操作',source_id:'来源 ID',kind:'来源类型',location:'本地目录或 GitHub 仓库',branch:'分支',subdir:'子目录',enabled:'启用',priority:'优先级',query:'搜索内容',limit:'结果数量',include_best_content:'同时加载最佳结果',name:'Skill 名称或来源限定 ref',path:'资源相对路径',content:'完整 SKILL.md 正文',draft_id:'草稿 ID',expected_current_sha256:'预览中的原文件 SHA-256 / absent',confirm:'我已审阅并确认应用',new_name:'新名称',plan_id:'变更计划 ID',expected_tree_sha256:'预览中的文件树 SHA-256',github_repo:'GitHub 仓库（owner/repo）',visibility:'仓库可见性',trash_id:'归档 ID',destination_source_id:'目标来源 ID',include_disabled:'包括停用 Skill',force:'强制同步'};

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
      ? '尚未安装 Skill Library，请选择自己的源码或 wheel。'
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
      if (row.provider_id === 'skill-library' && !$('skill-package').value.trim()) {
        $('install-live').textContent = '请输入已获得授权的 v0.5.0 源码目录或 wheel 完整路径，然后点「安装 / 修复」。';
        return;
      }
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
    if (provider_id === 'skill-library' && !skill_package) throw '请先填写 Skill Library v0.5.0 源码目录或 wheel 完整路径。';
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
  return `确认安装 / 修复 ${plan.provider_id}？\n\n${disconnect}来源：${plan.skill_package || plan.source || '经审阅的内置组件'}\n目标：${plan.environment}\n\n固定依赖：\n${specs.slice(0, 2200)}${specs.length > 2200 ? '…（详细清单可在下方查看）' : ''}\n\n计划 SHA-256：${plan.sha256}\n\n可能需要联网和运行第三方程序。安装完成后仍需单独启用 MCP。`;
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
      skill_package: plan.skill_package || null,
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

// Skill management (keeping existing implementation for now)
async function loadSkills(){
  await loadSkillPermissions();
  try{skillDescriptors=(await rpc('provider_details',{provider_id:'skill-library'})).capabilities.filter(item=>Object.hasOwn(skillLabels,item.id.slice('skill-library.'.length)));}
  catch(error){skillDescriptors=[];$('skill-status').textContent='Skill Library 尚未连接。请在 MCP 插件页安装并启用，再刷新此页。';$('skill-fields').replaceChildren();return;}
  const available=skillDescriptors.filter(item=>item.available).length;
  $('skill-status').textContent=skillDescriptors.length&&!available
    ?`已发现 ${skillDescriptors.length} 项Skill能力，但当前全部不可用。请到「MCP插件与Provider」启用Skill Library，再刷新。`
    :`真实服务发现 ${skillDescriptors.length} / 17 项管理能力，其中 ${available} 项可用。`;
  populateSkillActions();
}
async function loadSkillList(){
  skillInventory=[];skillInventorySources=[];$('skill-list-items').replaceChildren();
  $('skill-list-reading').textContent='';
  $('skill-list-content').textContent='选择一个 Skill 阅读完整正文。';
  $('skill-list-status').textContent='正在读取 Skill Library 的完整缓存目录…';
  try{
    const states=unwrap(await rpc('skill_action',{action:'states',arguments:{},confirmed:false}));
    const sources=unwrap(await rpc('skill_action',{action:'sources',arguments:{},confirmed:false}));
    if(!Array.isArray(states?.skills)||!Array.isArray(sources?.sources))throw new Error('服务未返回有效的 Skill 目录。');
    skillInventory=states.skills;skillInventorySources=sources.sources;
    const select=$('skill-list-source');const previous=select.value;select.replaceChildren();
    const all=node('option','所有来源');all.value='';select.append(all);
    for(const source of skillInventorySources){const option=node('option',`${source.id} · ${source.kind}`);option.value=source.id;select.append(option);}
    if(skillInventorySources.some(source=>source.id===previous))select.value=previous;
    renderSkillList();
  }catch(error){
    const disabled=String(error).includes('Capability is unavailable: skill-library.');
    const notReady=disabled||String(error).includes('Unknown capability: skill-library.')||String(error).includes('Runtime is stopped');
    $('skill-list-status').textContent=disabled?'Skill Library当前未启用，无法读取缓存目录。':'无法读取Skill列表，请检查Runtime和Skill Library连接状态。';
    $('skill-list-items').append(node('p',notReady?'Skill Library已安装的文件不会丢失。请前往「MCP插件与Provider」点击「启用并连接」，成功后刷新Skill列表。':friendlyError(error),'provider-error'));
  }
}
$('skill-install-link').onclick=()=>action(async()=>{
  view('component-management');currentComponentView='runtime';renderComponentManagement();await loadProviders();
  $('install-provider').value='skill-library';
  const index=providerCatalog.providers.findIndex(row=>row.provider_id==='skill-library');
  const installed=index>=0&&providerCatalog.providers[index].execution_files_present===true;
  if(installed){
    $('provider-list').children[index].scrollIntoView({block:'start'});
    $('provider-observation').textContent='Skill Library已安装，请在上方组件卡片点击「启用并连接」，成功后刷新Skill列表，无需重复安装。';
  }else{
    $('install-panel').scrollIntoView({block:'start'});
    $('install-live').textContent='Skill Library尚未安装。请填写你有权使用的v0.5.0源码目录或wheel完整路径，然后点击「安装 / 修复」。';
    $('install-live').className='installation-status';
  }
});
$('skill-quick-add').onclick=()=>action(async()=>{
  const source_id=$('skill-quick-id').value.trim(),location=$('skill-quick-path').value.trim();
  if(!/^[a-z0-9][a-z0-9_-]*$/.test(source_id)||!location)throw '请填写唯一的来源ID（英文小写、数字、-、_）和已授权的Skill目录完整路径。';
  if(!window.confirm(`将以下本地Skill目录注册为来源并同步？\n${source_id}\n${location}\n此目录必须已获得独立Skill读取授权。`))return;
  $('skill-quick-result').textContent='正在登记本地来源…';
  $('skill-quick-result').className='installation-status';
  try{await rpc('skill_action',{action:'manage',arguments:{action:'add',source_id,kind:'local',location},confirmed:false});}
  catch(error){$('skill-quick-result').textContent='添加来源失败：'+friendlyError(error)+'。请检查Provider连接及Skill专用目录授权。';$('skill-quick-result').className='installation-status error';throw error;}
  $('skill-quick-result').textContent='来源已登记，正在同步并校验缓存…';
  try{
    const response=unwrap(await rpc('skill_action',{action:'sync',arguments:{source_id},confirmed:false}));
    const status=response?.results?.[source_id];
    if(!status||status.status==='error')throw new Error(status?.error||'服务没有返回成功的同步结果');
    await loadSkillList();
    $('skill-quick-result').textContent=`来源${source_id}已同步。请查看列表及实际缓存数量（无有效SKILL.md时仍可能为空）。`;
    $('skill-quick-result').className='installation-status success';
  }catch(error){
    $('skill-quick-result').textContent=`来源已登记但同步失败：${friendlyError(error)}。请修复路径或权限后到Skills管理执行「同步来源」，不用重复添加来源。`;
    $('skill-quick-result').className='installation-status error';
    throw error;
  }
});

function renderSkillList(){
  const query=$('skill-list-query').value.trim().toLocaleLowerCase();
  const sourceId=$('skill-list-source').value;const status=$('skill-list-state').value;
  const rows=skillInventory.filter(skill=>(!sourceId||skill.source_id===sourceId)&&(!status||(status==='enabled')===skill.effective_enabled)&&(!query||`${skill.ref} ${skill.name} ${skill.source_id}`.toLocaleLowerCase().includes(query)));
  const enabled=skillInventory.filter(skill=>skill.effective_enabled).length;
  const noSources=skillInventorySources.length===0;
  $('skill-list-status').textContent=noSources
    ?'Skill Library已连接，但尚未添加Skill来源。'
    :`共 ${skillInventory.length} 个已缓存 Skill · ${enabled} 个启用 · 当前显示 ${rows.length} 个`;
  const list=$('skill-list-items');list.replaceChildren();
  if(!rows.length){
    const emptyCard = node('div', undefined, 'card');
    emptyCard.append(node('p',noSources?'请在上方登记你的本地Skill目录并同步；安装Skill Library程序不会自动导入个人Skill。':skillInventory.length?'没有符合筛选条件的 Skill。':'尚无已缓存 Skill。请在 Skills 管理中添加来源并同步。', 'text-muted'));
    list.append(emptyCard);
    return;
  }
  for(const skill of rows){
    const source=skillInventorySources.find(item=>item.id===skill.source_id);
    const card=node('div',undefined,'card mb-md');
    const header = node('div', undefined, 'card-header');
    header.append(
      node('h3', skill.name, 'card-title'),
      createStatusBadge(skill.effective_enabled ? '已启用' : '已停用', skill.effective_enabled ? 'success' : 'neutral')
    );
    card.append(header);
    const body = node('div', undefined, 'card-body');
    body.append(node('p',`${skill.ref} · ${source?.kind||'来源'} · ${source?.location||skill.source_id}`, 'text-small text-muted'));
    card.append(body);
    const controls=node('div',undefined,'card-actions');
    if(skill.effective_enabled)controls.append(button('阅读全文',async()=>{
      $('skill-list-content').textContent='正在读取…';$('skill-list-reading').textContent=skill.ref;
      try{const result=unwrap(await rpc('skill_action',{action:'load',arguments:{name:skill.ref,source_id:skill.source_id},confirmed:false}));if(typeof result?.content!=='string')throw new Error('服务未返回 Skill 正文。');output('skill-list-content',result.content);$('skill-list-reader').scrollIntoView({block:'start'});}catch(error){$('skill-list-content').textContent=friendlyError(error);throw error;}
    }));
    controls.append(button('管理',async()=>{await loadSkills();view('skills');selectSkillOperation('toggle',{source_id:skill.source_id,name:skill.name,enabled:skill.enabled===false});}));
    card.append(controls);list.append(card);
  }
}
$('reload-skill-list').onclick=()=>action(loadSkillList);
for(const id of ['skill-list-query','skill-list-source','skill-list-state'])$(id).addEventListener(id==='skill-list-query'?'input':'change',renderSkillList);
function populateSkillActions(){const select=$('skill-operation');select.replaceChildren();for(const name of groups[$('skill-group').value]){const option=node('option',skillLabels[name]);option.value=name;select.append(option);}renderSkillForm();}
function renderSkillForm(prefill={}){
  const operation=$('skill-operation').value;
  const descriptor=skillDescriptors.find(item=>item.id===`skill-library.${operation}`);
  const form=$('skill-fields');form.replaceChildren();skillInputs=[];
  if(!descriptor){form.append(node('p','当前服务未提供这项能力。', 'text-muted'));return;}
  form.append(node('p',descriptor.description, 'mb-md'));
  output('skill-schema',descriptor);
  const schema=descriptor.input_schema||{};
  for(const [name,property] of Object.entries(schema.properties||{})){
    const options=property.anyOf||[property];const type=options.find(item=>item.type&&item.type!=='null')?.type||'string';
    const required=(schema.required||[]).includes(name);

    const formGroup = node('div', undefined, 'form-group');
    const label=node('label',`${fieldLabels[name]||name}${required?' *':'（可选）'}`, 'form-label');
    formGroup.append(label);

    let input;
    const enumOptions=property.enum||options.find(item=>item.enum)?.enum;
    const suggestions=name==='action'?(operation==='manage'?['add','update','remove']:['rename','delete']):name==='kind'?['local','github','git']:name==='visibility'?['private','public']:enumOptions;
    if(type==='boolean'||suggestions){input=node('select', undefined, 'form-select');if(!required){const blank=node('option','使用服务默认值');blank.value='';input.append(blank);}for(const value of type==='boolean'?['true','false']:suggestions){const option=node('option',value);option.value=value;input.append(option);}}
    else{input=node(name==='content'||type==='object'||type==='array'?'textarea':'input');input.classList.add(name==='content'||type==='object'||type==='array'?'form-textarea':'form-input');if(type==='integer'||type==='number')input.type='number';if(name==='content')input.rows=16;}
    input.dataset.parameter=name;
    const initial=Object.hasOwn(prefill,name)?prefill[name]:property.default;
    if(initial!==undefined&&initial!==null)input.value=typeof initial==='object'?JSON.stringify(initial,null,2):String(initial);
    if(name==='confirm')input.value='false';
    formGroup.append(input);
    if(property.description)formGroup.append(node('span',property.description, 'form-hint'));
    form.append(formGroup);skillInputs.push({name,input,type,required});
  }
}
function skillArguments(){const args={};for(const field of skillInputs){const raw=field.input.value;if(!raw){if(field.required)throw `请填写 ${fieldLabels[field.name]||field.name}`;continue;}args[field.name]=field.type==='boolean'?raw==='true':['integer','number'].includes(field.type)?Number(raw):['object','array'].includes(field.type)?JSON.parse(raw):raw;}return args;}
async function executeSkill(){
  const operation=$('skill-operation').value;const descriptor=skillDescriptors.find(item=>item.id===`skill-library.${operation}`);if(!descriptor?.available)throw 'Skill 能力尚未可用，请先连接组件。';
  const args=skillArguments();let confirmed=false;
  if(descriptor.requires_confirmation){if(args.confirm!==true)throw '请先阅读预览结果，并将「我已审阅并确认应用」设为 true。';confirmed=window.confirm(`${skillLabels[operation]}？\n${JSON.stringify(args,null,2)}\n将沿用原服务的版本校验和权限检查。`);if(!confirmed)return;}
  const result=await rpc('skill_action',{action:operation,arguments:args,confirmed});lastSkillResult=unwrap(result);output('skill-result',result);
  const content=lastSkillResult?.content||lastSkillResult?.loaded?.content;output('skill-content',typeof content==='string'?content:'阅读或预览后的完整正文会显示在这里。');
  renderSkillNext(operation,lastSkillResult);
  renderSkillRows(lastSkillResult);
}
function renderSkillRows(result){const list=$('skill-catalog');list.replaceChildren();for(const source of result?.sources||[]){const card=node('div',undefined,'card mb-md');const header=node('div',undefined,'card-header');header.append(node('h3',source.id, 'card-title'),createStatusBadge(source.enabled?'启用':'停用',source.enabled?'success':'neutral'));card.append(header);const body=node('div',undefined,'card-body');body.append(node('p',`${source.kind} · ${source.cached_skill_count} 个缓存 Skill`),node('p',source.location, 'text-small'),node('p',`缓存版本：${source.cached_version||'尚未同步'}`, 'text-muted'));card.append(body);const controls=node('div',undefined,'card-actions');controls.append(button('编辑来源',async()=>selectSkillOperation('manage',{...source,action:'update',source_id:source.id})),button('同步此来源',async()=>selectSkillOperation('sync',{source_id:source.id})),button('查看单项状态',async()=>selectSkillOperation('states',{source_id:source.id})));card.append(controls);list.append(card);}for(const skill of result?.skills||result?.matches||[]){const card=node('div',undefined,'card mb-md');const header=node('div',undefined,'card-header');header.append(node('h3',skill.ref||skill.name, 'card-title'),skill.effective_enabled!==undefined?createStatusBadge(skill.effective_enabled?'启用':'停用',skill.effective_enabled?'success':'neutral'):node('span',''));card.append(header);const body=node('div',undefined,'card-body');body.append(node('p',skill.description||`来源 ${skill.source_id}`, 'text-muted'));card.append(body);const controls=node('div',undefined,'card-actions');controls.append(button('阅读全文',async()=>selectSkillOperation('load',{name:skill.ref||skill.name,source_id:skill.source_id})),button('启停此 Skill',async()=>selectSkillOperation('toggle',{source_id:skill.source_id,name:skill.name,enabled:skill.enabled===false})),button('验证',async()=>selectSkillOperation('validate',{name:skill.name,source_id:skill.source_id})));card.append(controls);list.append(card);}}
function renderSkillNext(operation,result){const controls=$('skill-next');controls.replaceChildren();const next=operation==='prepare'?'apply-local':operation==='plan-change'?'apply-change':operation==='publication-prepare'?'publication-apply':null;if(next&&result?.[next==='apply-local'?'draft_id':'plan_id'])controls.append(button('将预览结果带入下一步（仍需确认）',async()=>{selectSkillOperation(next,result);}));if(result?.trash_id)controls.append(button('恢复这个归档',async()=>selectSkillOperation('restore-local',result)));}
function selectSkillOperation(operation,prefill){$('skill-group').value=Object.keys(groups).find(group=>groups[group].includes(operation));populateSkillActions();$('skill-operation').value=operation;renderSkillForm(prefill);$('skill-fields').scrollIntoView({block:'start'});}
async function loadSkillPermissions(){const result=await rpc('skill_permissions');const list=$('skill-roots');list.replaceChildren();for(const [name,root] of Object.entries(result.available_roots)){const row=node('div',undefined,'permission-row');row.append(node('strong',name),node('p',root.path, 'text-small text-muted'));for(const [mode,caption,allowed,selected] of [['read','允许 Skill 读取',root.read,result.local_roots],['write','允许创作与改名、删除、恢复',root.write,result.writable_roots]]){const group=node('div',undefined,'checkbox-group');const check=node('input');check.type='checkbox';check.dataset.root=name;check.dataset.permission=mode;check.checked=selected.includes(name);check.disabled=!allowed;const label=node('label',caption);label.setAttribute('for',`skill-${name}-${mode}`);check.id=`skill-${name}-${mode}`;group.append(check,label);row.append(group);}list.append(row);}}
$('save-skill-permissions').onclick=()=>action(async()=>{const selected=mode=>[...$('skill-roots').querySelectorAll(`input[data-permission="${mode}"]:checked`)].map(item=>item.dataset.root);const args={local_roots:selected('read'),writable_roots:selected('write'),confirmed:true};if(!window.confirm(`保存 Skill 专用读写授权？\n读取：${args.local_roots.join(', ')||'无'}\n写入：${args.writable_roots.join(', ')||'无'}`))return;await rpc('skill_permissions',args);await loadSkillPermissions();notice('Skill 授权已保存，请重新启用该组件以加载。');});
$('reload-skills').onclick=()=>action(loadSkills);$('skill-group').onchange=populateSkillActions;$('skill-operation').onchange=()=>renderSkillForm();$('execute-skill').onclick=()=>action(executeSkill);

// View loaders
for(const [page,loader] of [['component-management',loadProviders],['skill-list',loadSkillList],['skills',loadSkills]])document.querySelectorAll(`[data-view="${page}"]`).forEach(el=>el.addEventListener('click',()=>action(loader)));
