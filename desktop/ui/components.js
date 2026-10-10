'use strict';
// All displayed text is data. Provider descriptions and Skill content are never HTML.
const node = (tag,text,className) => {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
const button = (text,fn) => {const el=node('button',text);el.onclick=()=>action(fn);return el;};
const output = (id,value) => {$(id).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);};
const unwrap = value => value?.data ?? value;
let providerCatalog, importPreview, installPreview, skillDescriptors=[], skillInputs=[], lastSkillResult, developmentSnapshot;
let watchedInstallJob=null;
let customPackagePreview=null;
let starterBundlePreview=null;
let developmentSourcePreview;
let providerEditVersion, providerEditPreview;
let skillInventory=[], skillInventorySources=[];
const skillLabels={manage:'添加、编辑或移除来源',sources:'来源与缓存状态',sync:'同步来源',find:'搜索并加载 Skill',load:'阅读完整 Skill',resource:'阅读附属资源',validate:'验证 Skill',prepare:'准备创作草稿','apply-local':'应用已审阅草稿',states:'单个 Skill 启用状态',toggle:'启用或停用 Skill','plan-change':'预览改名或删除','apply-change':'应用改名或删除','publish-plan':'规划 GitHub 发布','restore-local':'恢复已归档 Skill','publication-prepare':'预览向 Git 仓库转移','publication-apply':'应用已审阅转移'};
const groups={sources:['sources','manage','sync'],browse:['states','find','load','resource','validate','toggle'],author:['prepare','apply-local','plan-change','apply-change','restore-local'],publish:['publish-plan','publication-prepare','publication-apply']};
const fieldLabels={action:'操作',source_id:'来源 ID',kind:'来源类型',location:'本地目录或 GitHub 仓库',branch:'分支',subdir:'子目录',enabled:'启用',priority:'优先级',query:'搜索内容',limit:'结果数量',include_best_content:'同时加载最佳结果',name:'Skill 名称或来源限定 ref',path:'资源相对路径',content:'完整 SKILL.md 正文',draft_id:'草稿 ID',expected_current_sha256:'预览中的原文件 SHA-256 / absent',confirm:'我已审阅并确认应用',new_name:'新名称',plan_id:'变更计划 ID',expected_tree_sha256:'预览中的文件树 SHA-256',github_repo:'GitHub 仓库（owner/repo）',visibility:'仓库可见性',trash_id:'归档 ID',destination_source_id:'目标来源 ID',include_disabled:'包括停用 Skill',force:'强制同步'};

async function loadProviders(){
  providerCatalog=await rpc('provider_catalog');
  const list=$('provider-list');list.replaceChildren();
  $('provider-path').textContent=providerCatalog.manifest_directory;
  $('provider-observation').textContent=providerCatalog.runtime_observed?'状态来自当前 Runtime 的真实 MCP 发现':'Runtime 未运行；以下仅表示配置和依赖存在情况';
  for(const row of providerCatalog.providers){
    const card=node('article',undefined,'wide provider-card');
    const advanced=node('details',undefined,'provider-advanced');
    advanced.append(node('summary','详细信息与高级管理'));
    const lifecycle=row.lifecycle;
    const state=lifecycle?({ready:'MCP 已连接',disabled:'已停用',failed:'连接失败',error:'连接失败',discovered:'已发现',unconfigured:'尚未配置'})[lifecycle.state]||'连接状态未知':'未连接';
    const installedFiles=row.execution_files_present===true;
    const live=lifecycle?.state==='ready';
    const failed=['error','failed'].includes(lifecycle?.state);
    const currentStatus=live?'MCP 已连接':!installedFiles?'缺少运行组件':failed?'连接失败':row.installation_recorded?'已安装，待启用':state;
    const header=node('div',undefined,'provider-card-header');
    header.append(node('h2',row.provider_id),node('strong',currentStatus));
    card.append(header);
    if(failed)card.append(node('p',`MCP启动失败：${lifecycle?.error_message||'连接未建立'}。可尝试重连；持续失败请停用后修复依赖。`,'error-text'));
    else if(!installedFiles)card.append(node('p',row.provider_id==='skill-library'?'尚未安装Skill Library，请选择自己的源码或wheel。':row.directory_exists===false?'MCP源码目录缺失，需要安装依赖。':'缺少运行文件，需要先安装或检查系统前提。','error-text'));
    advanced.append(node('p',`${row.runtime_kind} · 启动偏好 ${row.requested_enabled?'启用':'停用'} · 已发现工具 ${lifecycle?.tool_count??'未检测'}`));
    advanced.append(node('p',row.url||row.python||row.command));
    if(!installedFiles)advanced.append(node('p',row.directory_exists===false?'工作目录不存在：请先安装对应组件，不要直接启用。':'缺少执行组件：请先安装对应依赖，不要直接启用。','error-text'));
    if(row.installation_recorded)advanced.append(node('p','已记录安装；仍需通过实际文件和MCP连接验证。'));
    if(!row.install_supported && !installedFiles)advanced.append(node('p','此清单暂无受控安装规格。可在下方接入自选MCP依赖，或检查外部软件前提。','error-text'));
    if(lifecycle?.error_message)advanced.append(node('p',lifecycle.error_message,'error-text'));
    const quick=node('div',undefined,'actions provider-quick');
    if(!installedFiles && row.install_supported)quick.append(button(row.provider_id==='skill-library'?'安装Skill Library':'安装 / 修复组件',async()=>{
      $('install-provider').value=row.provider_id;
      if(row.provider_id!=='skill-library')$('skill-package').value='';
      $('install-panel').scrollIntoView({block:'start'});
      if(row.provider_id==='skill-library'&&!$('skill-package').value.trim()){
        $('install-live').textContent='请输入已获得授权的v0.5.0源码目录或wheel完整路径，然后点「安装 / 修复」。';
        return;
      }
      await installSelectedComponent();
    }));
    else if(failed && installedFiles) {
      quick.append(button('重试连接',async()=>{
        if(!window.confirm(`重新发现 ${row.provider_id} 的MCP工具？\n${lifecycle?.error_message||'此前连接失败'}`))return;
        const result=await rpc('provider_action',{action:'reload',provider_id:row.provider_id,confirmed:true});
        output('provider-result',result);await loadProviders();
      }));
      if(row.install_supported)quick.append(button('修复依赖',async()=>{
        $('install-provider').value=row.provider_id;
        if(row.provider_id!=='skill-library')$('skill-package').value='';
        $('install-panel').scrollIntoView({block:'start'});
        await installSelectedComponent();
      }));
    }
    else if(installedFiles&&!live)quick.append(button('启用并连接',async()=>{
      if(!window.confirm(`启用 ${row.provider_id} 并连接真实MCP？此组件可能访问本机资源，请确认其工具权限。`))return;
      const result=await rpc('provider_action',{action:'enable',provider_id:row.provider_id,confirmed:true});
      output('provider-result',result);await loadProviders();
      notice(result.status==='selection_saved'?'启用偏好已保存；启动Runtime后才会连接。':`${row.provider_id}已发起连接；请以实际MCP状态为准。`);
    }));
    const controls=node('div',undefined,'actions');
    for(const [label,operation] of [['启用','enable'],['停用','disable'],['重载工具','reload']]){
      if(['enable','reload'].includes(operation)&&!installedFiles)continue;
      controls.append(button(label,async()=>{
      if(!window.confirm(`${label} ${row.provider_id}？\n${operation==='enable'?'将启动清单指定的 MCP 组件，并按原权限 Broker 发现工具。':'将改变该组件的实际连接状态。'}\n${row.url||row.python||row.command||''}`))return;
       const result=await rpc('provider_action',{action:operation,provider_id:row.provider_id,confirmed:true});
       output('provider-result',result);await loadProviders();
       notice(result.status==='selection_saved'?'启动偏好已保存，请启动Runtime后再检查实际MCP连接。':`${row.provider_id}已执行${label}；请以卡片上的MCP状态为准。`);
      }));
    }
    controls.append(button('工具与参数',async()=>{const result=await rpc('provider_details',{provider_id:row.provider_id});renderTools(result);output('provider-result',result);}));
    controls.append(button('查看 / 编辑清单',async()=>{providerEditVersion=await rpc('provider_configuration',{provider_id:row.provider_id,content:null,confirmed:false,expected_sha256:null,expected_content_sha256:null});providerEditPreview=null;$('edit-provider-id').textContent=row.provider_id;$('edit-manifest-content').value=providerEditVersion.content;output('edit-manifest-preview',providerEditVersion.sha256);$('edit-manifest-panel').scrollIntoView({block:'start'});}));
    if(row.install_supported)controls.append(button(installedFiles?'检查 / 修复依赖':'安装组件…',async()=>{
      $('install-provider').value=row.provider_id;
      if(row.provider_id!=='skill-library')$('skill-package').value='';
      $('install-panel').scrollIntoView({block:'start'});
      $('install-live').textContent='点击「安装 / 修复」会自动校验安装计划，并在确认窗口展示来源和目标目录。';
    }));
    else controls.append(node('span','无自动安装规格 · 可配置已有MCP','error-text'));
    advanced.append(controls);card.append(quick,advanced);list.append(card);
  }
  await refreshInstallationStatus();
}
async function refreshInstallationStatus(){
  const jobs=await rpc('installation_status');
  output('install-jobs',jobs.jobs.length?jobs:`当前无执行中的安装任务。已有安装记录：${(jobs.installed_receipts||[]).join('、')||'无'}`);
  const running=jobs.jobs.find(job=>job.state==='running');
  const finished=watchedInstallJob&&jobs.jobs.find(job=>job.job===watchedInstallJob&&job.state!=='running');
  if(finished){
    watchedInstallJob=null;
    $('install-live').textContent=finished.state==='installed'?`${finished.provider_id} 的依赖已安装；请检查文件后单独启用，验证真实MCP工具。`:`${finished.provider_id} 安装失败（退出码 ${finished.exit_code}）；查看下方日志，可修复后重试。`;
    await loadProviders();
    return;
  }
  if(running)$('install-live').textContent=`${running.provider_id} 正在安装（PID ${running.pid}）…请查看任务日志，安装过程中勿重复启动。`;
}
function renderTools(result){const list=$('provider-tools');list.replaceChildren();for(const tool of result.capabilities){const detail=node('details');detail.append(node('summary',`${tool.id} · ${tool.available?'可用':'不可用'} · ${tool.risk_level}`),node('p',tool.description),node('pre',JSON.stringify(tool,null,2)));list.append(detail);}}
async function previewInstall({detail=true}={}){
  installPreview=null;
  try{
    const provider_id=$('install-provider').value.trim();
    const skill_package=$('skill-package').value.trim()||null;
    if(!provider_id)throw '请选择要安装的组件。';
    if(provider_id==='skill-library'&&!skill_package)throw '请先填写Skill Library v0.5.0源码目录或wheel完整路径。';
    if(provider_id!=='skill-library'&&skill_package)throw '非Skill Library组件请清空本地包路径。';
    $('install-live').textContent=`正在核对${provider_id}的版本、来源与安装条件…`;
    installPreview=await rpc('provider_install',{provider_id,skill_package,confirmed:false,expected_sha256:null});
    output('install-preview',installPreview);
    if(detail)$('install-live').textContent=`${provider_id}依赖已验证。点击「安装 / 修复」将展示一次安装确认，无须再打开技术计划。`;
    return installPreview;
  }catch(error){
    $('install-live').textContent='安装条件不满足：'+friendlyError(error);
    throw error;
  }
}
$('preview-bundle').onclick=()=>action(async()=>{
  starterBundlePreview=null;
  starterBundlePreview=await rpc('provider_bundle',{confirmed:false,expected_sha256:null});
  output('bundle-preview',starterBundlePreview);
  $('bundle-status').textContent='已审核7项常用组件的锁定依赖；尚未安装。';
});
$('start-bundle').onclick=()=>action(async()=>{
  const running=await rpc('installation_status');
  if((running.jobs||[]).some(job=>job.state==='running'))throw '已有组件正在安装，请等待完成。';
  starterBundlePreview=await rpc('provider_bundle',{confirmed:false,expected_sha256:null});
  output('bundle-preview',starterBundlePreview);
  const source=starterBundlePreview.plans?.map(row=>row.provider_id).join('、')||starterBundlePreview.providers?.join('、');
  if(!window.confirm(`确认一次性准备7个常用MCP？\n${source}\n\n已审核依赖规格；可能联网下载较多文件。安装后仍需单独启用。\n计划SHA-256：${starterBundlePreview.sha256}`))return;
  const result=await rpc('provider_bundle',{confirmed:true,expected_sha256:starterBundlePreview.sha256});
  starterBundlePreview=null;watchedInstallJob=result.job||null;
  $('bundle-status').textContent=result.started?'已启动常用组件准备任务；请查看下面的安装任务状态。':'没有启动，请查看安装日志。';
  output('bundle-preview',result);
  $('install-panel').scrollIntoView({block:'start'});
  await refreshInstallationStatus();
});
$('reload-providers').onclick=()=>action(loadProviders);
$('rescan-providers').onclick=()=>action(async()=>{if(!window.confirm('重扫并应用已选择的 Provider 清单变化？Runtime 主服务保持运行。'))return;output('provider-result',await rpc('provider_action',{action:'rescan',provider_id:null,confirmed:true}));await loadProviders();});
$('preview-import').onclick=()=>action(async()=>{importPreview=await rpc('provider_import',{content:$('manifest-content').value,confirmed:false,expected_sha256:null});output('manifest-preview',importPreview);});
$('apply-import').onclick=()=>action(async()=>{if(!importPreview)throw '请先预览当前清单。';if(!window.confirm(`保存 ${importPreview.provider_id} 的这份已审阅清单？\nSHA-256 ${importPreview.sha256}\n保存后仍需单独启用。`))return;output('manifest-preview',await rpc('provider_import',{content:$('manifest-content').value,confirmed:true,expected_sha256:importPreview.sha256}));importPreview=null;await loadProviders();});
$('preview-manifest-edit').onclick=()=>action(async()=>{if(!providerEditVersion)throw '请先选择要编辑的组件。';providerEditPreview=await rpc('provider_configuration',{provider_id:providerEditVersion.provider_id,content:$('edit-manifest-content').value,confirmed:false,expected_sha256:providerEditVersion.sha256,expected_content_sha256:null});output('edit-manifest-preview',providerEditPreview);});
$('save-manifest-edit').onclick=()=>action(async()=>{if(!providerEditPreview)throw '请先预览当前修改。';if(!window.confirm(`保存 ${providerEditVersion.provider_id} 的已审阅配置？\n${providerEditPreview.content_sha256}\n保存后需单独重载或重扫。`))return;output('edit-manifest-preview',await rpc('provider_configuration',{provider_id:providerEditVersion.provider_id,content:$('edit-manifest-content').value,confirmed:true,expected_sha256:providerEditVersion.sha256,expected_content_sha256:providerEditPreview.content_sha256}));providerEditVersion=null;providerEditPreview=null;await loadProviders();});
function customPackageArgs(){
  return {provider_id:$('custom-package-id').value.trim(),package_kind:$('custom-package-kind').value,
    packages:$('custom-package-pins').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean),
    confirmed:false,expected_sha256:null};
}
$('custom-package-preview').onclick=()=>action(async()=>{
  const args=customPackageArgs();customPackagePreview=null;
  customPackagePreview=await rpc('provider_package',args);
  output('custom-package-result',customPackagePreview);
});
$('custom-package-save').onclick=()=>action(async()=>{
  const args=customPackageArgs();
  if(!customPackagePreview || args.provider_id!==customPackagePreview.provider_id ||
     args.package_kind!==customPackagePreview.package_kind ||
     JSON.stringify(args.packages)!==JSON.stringify(customPackagePreview.packages))
    throw '依赖清单已改变，请重新审核。';
  if(!window.confirm(`登记 ${args.provider_id} 的 ${args.package_kind} 依赖？\n${args.packages.join('\n')}\n\n第三方依赖包含可执行代码，安装后仍需单独启用MCP。`))return;
  const result=await rpc('provider_package',{...args,confirmed:true,expected_sha256:customPackagePreview.sha256});
  customPackagePreview=null;output('custom-package-result',result);
  $('install-provider').value=result.provider_id;$('skill-package').value='';
  await loadProviders();$('install-panel').scrollIntoView({block:'start'});
  $('install-live').textContent='自定义依赖已登记。点击「安装 / 修复」，系统会自动复核依赖并让你确认一次。';
});
$('preview-install').onclick=()=>action(previewInstall);
function installReviewText(plan,needsDisable=false){
  const specs=(plan.specs||[]).map(item=>`${item.name}\n${(item.content||'').trim()}`).join('\n\n');
  const disconnect=needsDisable?'当前组件已启用（包括连接失败的状态）。确认后将先停用该组件，再安装依赖。\n\n':'';
  return `确认安装 / 修复 ${plan.provider_id}？\n\n${disconnect}来源：${plan.skill_package||plan.source||'经审阅的内置组件'}\n目标：${plan.environment}\n\n固定依赖：\n${specs.slice(0,2200)}${specs.length>2200?'…（详细清单可在下方查看）':''}\n\n计划SHA-256：${plan.sha256}\n\n可能需要联网和运行第三方程序。安装完成后仍需单独启用MCP。`;
}
async function installSelectedComponent(){
  try{
    const jobs=await rpc('installation_status');
    if((jobs.jobs||[]).some(job=>job.state==='running'))throw '已有组件正在安装，完成后再重试。';
    // Always regenerate from current inputs: no separate prerequisite preview click.
    // The backend checks the digest once more before launching anything.
    const plan=await previewInstall({detail:false});
    const catalog=await rpc('provider_catalog');
    const active=catalog.providers?.find(row=>row.provider_id===plan.provider_id);
    const needsDisable=active?.lifecycle?.enabled===true;
    if(!window.confirm(installReviewText(plan,needsDisable))){
      $('install-live').textContent='已取消安装，组件及配置保持不变。';
      return;
    }
    if(needsDisable){
      $('install-live').textContent=`正在停用${plan.provider_id}，准备修复依赖…`;
      await rpc('provider_action',{action:'disable',provider_id:plan.provider_id,confirmed:true});
    }
    const result=await rpc('provider_install',{provider_id:plan.provider_id,
      skill_package:plan.skill_package||null,confirmed:true,expected_sha256:plan.sha256});
    output('install-preview',result);installPreview=null;
    watchedInstallJob=result.job||null;
    $('install-live').textContent=result.started?`${result.provider_id}安装任务已启动；安装成功后可单独启用并检查MCP连接。`:'没有启动安装，请检查安装状态。';
    await refreshInstallationStatus();
  }catch(error){
    $('install-live').textContent='安装未启动：'+friendlyError(error);
    throw error;
  }
}
$('start-install').onclick=()=>action(installSelectedComponent);
$('installation-refresh').onclick=()=>action(refreshInstallationStatus);
setInterval(()=>{if(!$('providers').classList.contains('hidden')&&!busy)action(refreshInstallationStatus);},2000);

async function loadSkills(){
  await loadSkillPermissions();
  try{skillDescriptors=(await rpc('provider_details',{provider_id:'skill-library'})).capabilities.filter(item=>Object.hasOwn(skillLabels,item.id.slice('skill-library.'.length)));}
  catch(error){skillDescriptors=[];$('skill-status').textContent='Skill Library 尚未连接。请在 MCP 插件页安装并启用，再刷新此页。';$('skill-fields').replaceChildren();return;}
  const available=skillDescriptors.filter(item=>item.available).length;
  $('skill-status').textContent=`真实服务发现 ${skillDescriptors.length} / 17 项管理能力，其中 ${available} 项可用。`;
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
    $('skill-list-status').textContent='无法读取 Skill 列表。请确认 Runtime 已启动，Skill Library 已安装并启用，然后刷新。';
    const notReady=String(error).includes('Unknown capability: skill-library.')||String(error).includes('Runtime is stopped');
    $('skill-list-items').append(node('p',notReady?'Skill Library尚未连接。请先完成安装与启用；本地Skill文件不会因此丢失。':friendlyError(error),'error-text'));
  }
}
$('skill-install-link').onclick=()=>action(async()=>{
  view('providers');await loadProviders();
  $('install-provider').value='skill-library';
  $('install-panel').scrollIntoView({block:'start'});
  $('install-live').textContent='Skill Library没有随安装包附带源码。请填写你有权使用的v0.5.0源码目录或wheel完整路径，然后直接点击「安装 / 修复」；安装完成后单独启用。';
});
$('skill-quick-add').onclick=()=>action(async()=>{
  const source_id=$('skill-quick-id').value.trim(),location=$('skill-quick-path').value.trim();
  if(!/^[a-z0-9][a-z0-9_-]*$/.test(source_id)||!location)throw '请填写唯一的来源ID（英文小写、数字、-、_）和已授权的Skill目录完整路径。';
  if(!window.confirm(`将以下本地Skill目录注册为来源并同步？\n${source_id}\n${location}\n此目录必须已获得独立Skill读取授权。`))return;
  $('skill-quick-result').textContent='正在登记本地来源…';
  try{await rpc('skill_action',{action:'manage',arguments:{action:'add',source_id,kind:'local',location},confirmed:false});}
  catch(error){$('skill-quick-result').textContent='添加来源失败：'+friendlyError(error)+'。请检查Provider连接及Skill专用目录授权。';throw error;}
  $('skill-quick-result').textContent='来源已登记，正在同步并校验缓存…';
  try{
    const response=unwrap(await rpc('skill_action',{action:'sync',arguments:{source_id},confirmed:false}));
    const status=response?.results?.[source_id];
    if(!status||status.status==='error')throw new Error(status?.error||'服务没有返回成功的同步结果');
    await loadSkillList();
    $('skill-quick-result').textContent=`来源${source_id}已同步。请查看列表及实际缓存数量（无有效SKILL.md时仍可能为空）。`;
  }catch(error){
    $('skill-quick-result').textContent=`来源已登记但同步失败：${friendlyError(error)}。请修复路径或权限后到Skills管理执行「同步来源」，不用重复添加来源。`;
    throw error;
  }
});
function renderSkillList(){
  const query=$('skill-list-query').value.trim().toLocaleLowerCase();
  const sourceId=$('skill-list-source').value;const status=$('skill-list-state').value;
  const rows=skillInventory.filter(skill=>(!sourceId||skill.source_id===sourceId)&&(!status||(status==='enabled')===skill.effective_enabled)&&(!query||`${skill.ref} ${skill.name} ${skill.source_id}`.toLocaleLowerCase().includes(query)));
  const enabled=skillInventory.filter(skill=>skill.effective_enabled).length;
  $('skill-list-status').textContent=`共 ${skillInventory.length} 个已缓存 Skill · ${enabled} 个启用 · 当前显示 ${rows.length} 个`;
  const list=$('skill-list-items');list.replaceChildren();
  if(!rows.length){list.append(node('p',skillInventory.length?'没有符合筛选条件的 Skill。':'尚无已缓存 Skill。请在 Skills 管理中添加来源并同步。'));return;}
  for(const skill of rows){
    const source=skillInventorySources.find(item=>item.id===skill.source_id);
    const card=node('article',undefined,'wide');
    card.append(node('h2',skill.name),node('strong',skill.effective_enabled?'已启用':skill.source_enabled===false?'来源已停用':'Skill 已停用'),node('p',`${skill.ref} · ${source?.kind||'来源'} · ${source?.location||skill.source_id}`));
    const controls=node('div',undefined,'actions');
    if(skill.effective_enabled)controls.append(button('阅读全文',async()=>{
      $('skill-list-content').textContent='正在读取…';$('skill-list-reading').textContent=skill.ref;
      try{const result=unwrap(await rpc('skill_action',{action:'load',arguments:{name:skill.ref,source_id:skill.source_id},confirmed:false}));if(typeof result?.content!=='string')throw new Error('服务未返回 Skill 正文。');output('skill-list-content',result.content);$('skill-list-reader').scrollIntoView({block:'start'});}catch(error){$('skill-list-content').textContent=friendlyError(error);throw error;}
    }));
    controls.append(button('启停 / 更多管理',async()=>{await loadSkills();view('skills');selectSkillOperation('toggle',{source_id:skill.source_id,name:skill.name,enabled:skill.enabled===false});}));
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
  if(!descriptor){form.append(node('p','当前服务未提供这项能力。'));return;}
  form.append(node('p',descriptor.description));
  output('skill-schema',descriptor);
  const schema=descriptor.input_schema||{};
  for(const [name,property] of Object.entries(schema.properties||{})){
    const options=property.anyOf||[property];const type=options.find(item=>item.type&&item.type!=='null')?.type||'string';
    const required=(schema.required||[]).includes(name);
    const label=node('label',`${fieldLabels[name]||name}${required?' *':'（可选）'}`);
    let input;
    const enumOptions=property.enum||options.find(item=>item.enum)?.enum;
    const suggestions=name==='action'?(operation==='manage'?['add','update','remove']:['rename','delete']):name==='kind'?['local','github','git']:name==='visibility'?['private','public']:enumOptions;
    if(type==='boolean'||suggestions){input=node('select');if(!required){const blank=node('option','使用服务默认值');blank.value='';input.append(blank);}for(const value of type==='boolean'?['true','false']:suggestions){const option=node('option',value);option.value=value;input.append(option);}}
    else{input=node(name==='content'||type==='object'||type==='array'?'textarea':'input');if(type==='integer'||type==='number')input.type='number';if(name==='content')input.rows=16;}
    input.dataset.parameter=name;
    const initial=Object.hasOwn(prefill,name)?prefill[name]:property.default;
    if(initial!==undefined&&initial!==null)input.value=typeof initial==='object'?JSON.stringify(initial,null,2):String(initial);
    if(name==='confirm')input.value='false';
    label.append(input);if(property.description)label.append(node('small',property.description));form.append(label);skillInputs.push({name,input,type,required});
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
function renderSkillRows(result){const list=$('skill-catalog');list.replaceChildren();for(const source of result?.sources||[]){const card=node('article',undefined,'wide');card.append(node('h2',source.id),node('p',`${source.kind} · ${source.enabled?'启用':'停用'} · ${source.cached_skill_count} 个缓存 Skill`),node('p',source.location),node('p',`缓存版本：${source.cached_version||'尚未同步'}`));const controls=node('div',undefined,'actions');controls.append(button('编辑来源',async()=>selectSkillOperation('manage',{...source,action:'update',source_id:source.id})),button('同步此来源',async()=>selectSkillOperation('sync',{source_id:source.id})),button('查看单项状态',async()=>selectSkillOperation('states',{source_id:source.id})));card.append(controls);list.append(card);}for(const skill of result?.skills||result?.matches||[]){const card=node('article',undefined,'wide');card.append(node('h2',skill.ref||skill.name),node('p',skill.description||`来源 ${skill.source_id} · ${skill.effective_enabled===false?'已停用':'可用'}`));const controls=node('div',undefined,'actions');controls.append(button('阅读全文',async()=>selectSkillOperation('load',{name:skill.ref||skill.name,source_id:skill.source_id})),button('启停此 Skill',async()=>selectSkillOperation('toggle',{source_id:skill.source_id,name:skill.name,enabled:skill.enabled===false})),button('验证',async()=>selectSkillOperation('validate',{name:skill.name,source_id:skill.source_id})));card.append(controls);list.append(card);}}
function renderSkillNext(operation,result){const controls=$('skill-next');controls.replaceChildren();const next=operation==='prepare'?'apply-local':operation==='plan-change'?'apply-change':operation==='publication-prepare'?'publication-apply':null;if(next&&result?.[next==='apply-local'?'draft_id':'plan_id'])controls.append(button('将预览结果带入下一步（仍需确认）',async()=>{selectSkillOperation(next,result);}));if(result?.trash_id)controls.append(button('恢复这个归档',async()=>selectSkillOperation('restore-local',result)));}
function selectSkillOperation(operation,prefill){$('skill-group').value=Object.keys(groups).find(group=>groups[group].includes(operation));populateSkillActions();$('skill-operation').value=operation;renderSkillForm(prefill);$('skill-fields').scrollIntoView({block:'start'});}
async function loadSkillPermissions(){const result=await rpc('skill_permissions');const list=$('skill-roots');list.replaceChildren();for(const [name,root] of Object.entries(result.available_roots)){const row=node('div',undefined,'permission-row');row.append(node('strong',name),node('p',root.path));for(const [mode,caption,allowed,selected] of [['read','允许 Skill 读取',root.read,result.local_roots],['write','允许创作与改名、删除、恢复',root.write,result.writable_roots]]){const label=node('label',caption);const check=node('input');check.type='checkbox';check.dataset.root=name;check.dataset.permission=mode;check.checked=selected.includes(name);check.disabled=!allowed;label.prepend(check);row.append(label);}list.append(row);}}
$('save-skill-permissions').onclick=()=>action(async()=>{const selected=mode=>[...$('skill-roots').querySelectorAll(`input[data-permission="${mode}"]:checked`)].map(item=>item.dataset.root);const args={local_roots:selected('read'),writable_roots:selected('write'),confirmed:true};if(!window.confirm(`保存 Skill 专用读写授权？\n读取：${args.local_roots.join(', ')||'无'}\n写入：${args.writable_roots.join(', ')||'无'}`))return;await rpc('skill_permissions',args);await loadSkillPermissions();notice('Skill 授权已保存，请重新启用该组件以加载。');});
$('reload-skills').onclick=()=>action(loadSkills);$('skill-group').onchange=populateSkillActions;$('skill-operation').onchange=()=>renderSkillForm();$('execute-skill').onclick=()=>action(executeSkill);
async function loadDevelopment(){developmentSnapshot=await rpc('development_status');$('development-state').textContent=developmentSnapshot.enabled?'已开启：ChatGPT 可在以下源码 root 自开发':'已关闭（默认）';$('development-path').value=developmentSnapshot.root?.path||'';output('development-details',developmentSnapshot);}
async function setDevelopment(enabled){if(!enabled&&!developmentSnapshot?.root){notice('自开发已关闭，当前没有专用 root 授权。');return;}if(!window.confirm(enabled?'开启自开发？将对所选完整 PLA 源码目录授予读取、写入和受控执行权限。':'关闭自开发并移除此专用 root 的授权？源码文件保留。'))return;await rpc('development_configure',{enabled,path:$('development-path').value.trim(),confirmed:true,expected_sha256:developmentSnapshot.config_sha256});await loadDevelopment();await refresh();}
$('reload-development').onclick=()=>action(loadDevelopment);$('enable-development').onclick=()=>action(()=>setDevelopment(true));$('disable-development').onclick=()=>action(()=>setDevelopment(false));
$('preview-development-source').onclick=()=>action(async()=>{developmentSourcePreview=await rpc('development_prepare',{path:$('development-empty-path').value.trim(),confirmed:false,expected_sha256:null});output('development-source-preview',developmentSourcePreview);});
$('prepare-development-source').onclick=()=>action(async()=>{if(!developmentSourcePreview)throw '请先预览源码快照。';if(!window.confirm(`将已校验源码准备到 ${developmentSourcePreview.target}？\n${developmentSourcePreview.file_count} 个文件\n不自动开启授权或更新当前应用。`))return;const result=await rpc('development_prepare',{path:$('development-empty-path').value.trim(),confirmed:true,expected_sha256:developmentSourcePreview.sha256});output('development-source-preview',result);developmentSourcePreview=null;$('development-path').value=result.target;notice('完整源码已准备。请停止 Runtime，再选择开启自开发。');});
for(const [page,loader] of [['providers',loadProviders],['skill-list',loadSkillList],['skills',loadSkills],['development',loadDevelopment]])document.querySelectorAll(`[data-view="${page}"]`).forEach(el=>el.addEventListener('click',()=>action(loader)));
