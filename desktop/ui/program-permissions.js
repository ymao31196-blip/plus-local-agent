'use strict';
// User-owned executable grants. This script can only call the private Desktop
// management bridge; it is not exposed to MCP or external Providers.
let executablePermissionState = null;

function paintExecutablePermissions() {
  const host = $('program-permissions-list');
  host.replaceChildren();
  const programs = executablePermissionState?.programs || [];
  if (!programs.length) {
    const empty = document.createElement('p');
    empty.className = 'text-muted';
    empty.textContent = '尚未添加自定义exe授权，当前使用PLA预置程序策略。';
    host.append(empty);
    return;
  }
  programs.forEach(item => {
    const card = document.createElement('div');
    card.className = 'card mb-md';
    const body = document.createElement('div');
    body.className = 'card-body';
    const path = document.createElement('p');
    path.className = 'text-small';
    path.textContent = item.path;
    const status = document.createElement('span');
    status.className = 'status-badge ' + (item.enabled ? 'success' : 'neutral');
    status.textContent = item.enabled ? '已授权' : '已停用';
    body.append(path, status);
    const controls = document.createElement('div');
    controls.className = 'card-actions';
    const toggle = document.createElement('button');
    toggle.textContent = item.enabled ? '停用' : '重新授权';
    const remove = document.createElement('button');
    remove.className = 'secondary';
    remove.textContent = '撤销';
    toggle.onclick = () => changeExecutablePermission(item.enabled ? 'disable' : 'grant', item.path);
    remove.onclick = () => changeExecutablePermission('revoke', item.path);
    controls.append(toggle, remove);
    card.append(body, controls);
    host.append(card);
  });
}

async function loadExecutablePermissions() {
  executablePermissionState = await rpc('program_permissions', { operation: 'list' });
  paintExecutablePermissions();
}

function changeExecutablePermission(operation, path) {
  return action(async () => {
    if (!executablePermissionState) await loadExecutablePermissions();
    const verbs = { grant: '允许执行', disable: '停用', revoke: '撤销' };
    const risk = operation === 'grant'
      ? '\n此程序可使用当前Windows账户的权限，包括访问PLA工作区以外的文件。'
      : '\n已启动的进程不会被此操作自动终止。';
    if (!window.confirm('确认' + verbs[operation] + '以下程序？\n' + path + risk)) return;
    executablePermissionState = await rpc('program_permissions', {
      operation, path, expected_sha256: executablePermissionState.config_sha256
    });
    paintExecutablePermissions();
    notice('程序权限已更新；新请求立即使用最新策略。');
  });
}

$('program-permissions-refresh').onclick = () => action(loadExecutablePermissions);
$('program-permissions-add').onclick = () => {
  const path = $('program-permissions-path').value.trim();
  if (!path) { notice('请填写已安装exe的完整路径。', true); return; }
  changeExecutablePermission('grant', path);
};
document.querySelector('nav [data-view="about"]').addEventListener('click', () => {
  loadExecutablePermissions().catch(e => notice(friendlyError(e), true));
});
