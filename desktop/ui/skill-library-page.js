'use strict';
// One user-facing Skill Library page. Agent-only authoring and publication
// capabilities stay registered in the MCP provider, not this GUI.
let skillSources = [], skillPermissionSnapshot = null;
const skillSyncStatuses = new Map();
const skillDraftKey = 'pla.desktop.skill-library.quick-source.v1';
const skillRpc = (action, args={}) => rpc('skill_action',{action,arguments:args,confirmed:false}).then(unwrap);
const skillKind = kind => ({local:'本地目录',github:'GitHub',git:'Git仓库'})[kind] || kind;
const skillPathKey = value => String(value).trim().replace(/\//g,'\\').replace(/\\+$/,'').toLowerCase();
function sourceLocationKey(kind, location) {
  return kind==='local' ? skillPathKey(location) : String(location).trim().replace(/\.git$/i,'').toLowerCase();
}
function suggestSkillSourceId(kind,location) {
  const last = kind==='local' ? location.replace(/[\\/]+$/,'').split(/[\\/]/).pop() : location.split('/').pop();
  let base=(last||'skills').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'');
  if(!/^[a-z]/.test(base))base='skills-'+base;
  base=base.slice(0,48).replace(/-+$/,'')||'skills';
  let candidate=base,number=2;
  while(skillSources.some(s=>s.id===candidate))candidate=base+'-'+number++;
  return candidate;
}
function draftSkillSource() {
  return {kind:$('skill-source-kind').value,location:$('skill-source-location').value,branch:$('skill-source-branch').value};
}
function saveSkillDraft(){
  try{window.localStorage?.setItem(skillDraftKey,JSON.stringify(draftSkillSource()));}catch(_e){}
}
function restoreSkillDraft(){
  let value;
  try{value=JSON.parse(window.localStorage?.getItem(skillDraftKey)||'null');}catch(_e){}
  if(!value||typeof value!=='object'||Array.isArray(value))return;
  if(['local','github'].includes(value.kind))$('skill-source-kind').value=value.kind;
  if(typeof value.location==='string'&&value.location.length<=4096)$('skill-source-location').value=value.location;
  if(typeof value.branch==='string'&&value.branch.length<=120)$('skill-source-branch').value=value.branch;
}
function updateSkillDraftUI(){
  const kind=$('skill-source-kind').value;
  $('skill-source-branch-field').classList.toggle('hidden',kind!=='github');
  $('skill-source-label').textContent=kind==='github'?'GitHub仓库（owner/repo）':'Skill目录完整路径';
  $('skill-source-location').placeholder=kind==='github'?'owner/my-skills 或 https://github.com/owner/my-skills':'尚无目录记录，请填写完整路径';
  const loc=$('skill-source-location').value.trim();
  const normalized=kind==='github'?loc.replace(/^https:\/\/github\.com\//i,'').replace(/\.git$/i,'').replace(/\/$/,''):loc;
  const found=skillSources.find(x=>x.kind===kind&&sourceLocationKey(kind,x.location)===sourceLocationKey(kind,normalized));
  $('skill-source-candidate').textContent=found?'已存在：'+found.id+'，点击后同步已有来源。':loc?'将生成来源ID：'+suggestSkillSourceId(kind,normalized):'填写位置后自动生成来源ID。';
  $('skill-source-add').textContent=found?'同步已有来源':'添加并同步';
}
function showSkillSources(open){
  $('skill-list-panel').classList.toggle('hidden',open);
  $('skill-sources-panel').classList.toggle('hidden',!open);
  if(open){renderSkillSources();action(loadSkillPermissions);}
}
async function loadSkillList(){
  $('skill-list-status').textContent='正在读取Skill目录…';
  try{
    const [states,sources]=await Promise.all([skillRpc('states'),skillRpc('sources')]);
    if(!Array.isArray(states?.skills)||!Array.isArray(sources?.sources))throw Error('服务返回的目录格式异常');
    skillInventory=states.skills;skillSources=sources.sources;
    const select=$('skill-list-source'),previous=select.value;
    select.replaceChildren();
    const all=node('option','全部来源');all.value='';select.append(all);
    for(const source of skillSources){const option=node('option',source.id);option.value=source.id;select.append(option);}
    if(skillSources.some(s=>s.id===previous))select.value=previous;
    renderSkillList();renderSkillSources();updateSkillDraftUI();
    return true;
  }catch(error){
    $('skill-list-status').textContent='无法读取Skill列表，请确认Skill Library已安装并连接。';
    $('skill-list-items').replaceChildren(node('p',friendlyError(error),'provider-error'));
    return false;
  }
}
function renderSkillList(){
  const q=$('skill-list-query').value.trim().toLowerCase(),
        sourceId=$('skill-list-source').value,state=$('skill-list-state').value;
  const rows=skillInventory.filter(s=>(!sourceId||s.source_id===sourceId)
    &&(!state||(state==='enabled')===s.effective_enabled)
    &&(!q||[s.name,s.ref,s.source_id].join(' ').toLowerCase().includes(q)));
  $('skill-list-status').textContent=!skillSources.length?'尚无Skill来源，请在「管理来源」中添加。':
    '已缓存'+skillInventory.length+'个Skill · '+skillInventory.filter(s=>s.effective_enabled).length+'个可用 · 显示'+rows.length+'个';
  const list=$('skill-list-items');list.replaceChildren();
  if(!rows.length){list.append(node('p',skillSources.length?'没有符合条件的Skill。':'安装服务后，需要先添加自己的Skill来源。','text-muted'));return;}
  for(const skill of rows){
    const source=skillSources.find(s=>s.id===skill.source_id);
    const line=node('div',undefined,'skill-library-row');
    const info=node('div',undefined,'skill-library-meta');
    info.append(node('strong',skill.name),node('span',skill.source_id+' · '+skillKind(source?.kind),'text-muted'));
    const status=createStatusBadge(skill.effective_enabled?'可用':skill.source_enabled?'已停用':'来源已停用',skill.effective_enabled?'success':'neutral');
    const read=button('查看',()=>readSkill(skill));read.disabled=!skill.effective_enabled;
    const toggle=button(skill.enabled?'停用':'启用',()=>setSkillEnabled(skill,!skill.enabled));toggle.disabled=!skill.source_enabled;
    line.append(info,status,read,toggle);list.append(line);
  }
}
async function readSkill(skill){
  $('skill-list-reader').classList.remove('hidden');
  $('skill-list-reading').textContent=skill.ref;
  $('skill-list-content').textContent='正在读取…';
  try{
    const result=await skillRpc('load',{name:skill.ref,source_id:skill.source_id});
    if(typeof result?.content!=='string')throw Error('服务未返回正文');
    $('skill-list-content').textContent=result.content;
    $('skill-list-reader').scrollIntoView({block:'nearest'});
  }catch(error){$('skill-list-content').textContent='读取失败：'+friendlyError(error);throw error;}
}
async function setSkillEnabled(skill,enabled){
  const result=await skillRpc('toggle',{source_id:skill.source_id,name:skill.name,enabled});
  if(result?.enabled!==enabled)throw Error('服务未确认启停结果');
  await loadSkillList();notice('已'+(enabled?'启用':'停用')+'Skill：'+skill.name);
}
async function syncSkills(sourceId=null){
  const id=sourceId||null;
  $('skill-sync-status').textContent='正在同步'+(id?' '+id:'所有来源')+'…';
  $('skill-sync-status').className='installation-status';
  try{
    const result=await skillRpc('sync',id?{source_id:id}:{});
    if(!result?.results||typeof result.results!=='object')throw Error('缺少同步结果');
    const rows=Object.entries(result.results);
    for(const [source,status] of rows)skillSyncStatuses.set(source,status);
    if(!await loadSkillList())throw Error('来源同步已返回，但目录刷新失败');
    const failures=rows.filter(([,r])=>r?.status==='error');
    const invalid=rows.filter(([,r])=>Array.isArray(r?.invalid_skills)&&r.invalid_skills.length);
    const skipped=rows.filter(([,r])=>r?.status==='disabled');
    if(failures.length){
      $('skill-sync-status').textContent='同步失败，仍使用上次有效缓存：'+failures.map(([id,r])=>id+': '+(r.error||'未知错误')).join('；');
      $('skill-sync-status').className='installation-status error';
    }else{
      $('skill-sync-status').textContent=rows.length?'已检查'+rows.length+'个来源并刷新列表'+(skipped.length?'；'+skipped.length+'个来源已停用，未执行同步':'')+(invalid.length?'；有无效Skill需检查':''):'没有可同步的来源';
      $('skill-sync-status').className=invalid.length?'installation-status':'installation-status success';
    }
    return !failures.length&&!invalid.length&&!skipped.length;
  }catch(error){
    $('skill-sync-status').textContent='同步或刷新未完成，之前的缓存仍可使用：'+friendlyError(error);
    $('skill-sync-status').className='installation-status error';throw error;
  }
}
async function addSkillSource(){
  let {kind,location,branch}=draftSkillSource();
  location=location.trim();if(!location)throw '请填写来源位置。';
  if(kind==='github'){
    location=location.replace(/^https:\/\/github\.com\//i,'').replace(/\.git$/i,'').replace(/\/$/,'');
    if(!/^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9_.-]+$/.test(location))throw '请输入有效的owner/repo或github.com仓库地址。';
  }else if(!/^[A-Za-z]:[\\/]/.test(location))throw '请输入Windows本地目录完整路径。';
  saveSkillDraft();
  const latest=await skillRpc('sources');
  if(!Array.isArray(latest?.sources))throw Error('无法检查已有来源，已停止添加');
  skillSources=latest.sources;
  const existing=skillSources.find(s=>s.kind===kind&&sourceLocationKey(kind,s.location)===sourceLocationKey(kind,location));
  if(existing){const ok=await syncSkills(existing.id);$('skill-source-result').textContent=(ok?'已同步已有来源：':'来源已登记，但同步未成功：')+existing.id;return;}
  const source_id=suggestSkillSourceId(kind,location);
  if(!window.confirm('添加'+skillKind(kind)+'来源？\n'+source_id+'\n'+location+'\n不会修改原始Skill仓库，也不会自动授予写入权限。'))return;
  try{
    await skillRpc('manage',{action:'add',source_id,kind,location,...(kind==='github'?{branch:branch.trim()||'main'}:{})});
    const ok=await syncSkills(source_id);
    $('skill-source-result').textContent=(ok?'已登记并同步：':'来源已登记，但同步未成功：')+source_id+'。';
    if(ok)$('skill-source-add-details').open=false;
  }catch(error){
    $('skill-source-result').textContent='操作失败：'+friendlyError(error)+'。如为本地权限问题，请检查下方读取授权。';
    throw error;
  }
}
async function setSourceEnabled(source,enabled){
  const result=await skillRpc('manage',{action:'update',source_id:source.id,enabled});
  if(result?.action!=='updated')throw Error('服务未确认来源更新');
  await loadSkillList();notice('已'+(enabled?'启用':'停用')+'来源：'+source.id);
}
async function removeSource(source){
  if(!window.confirm('移除来源登记「'+source.id+'」？\n原始文件和GitHub仓库不会删除。\n缓存及此来源的单项启停记录会清理。'))return;
  const result=await skillRpc('manage',{action:'remove',source_id:source.id});
  if(result?.action!=='removed')throw Error('服务未确认移除');
  await loadSkillList();notice('已取消登记，原始文件未删除：'+source.id);
}
function renderSkillSources(){
  const list=$('skill-source-items');list.replaceChildren();
  $('skill-source-summary').textContent='已登记'+skillSources.length+'个来源';
  if(!skillSources.length){list.append(node('p','尚无来源。选择本地目录或GitHub仓库添加。','text-muted'));return;}
  for(const source of skillSources){
    const card=node('div',undefined,'skill-source-card');
    const info=node('div',undefined,'skill-library-meta');
    info.append(node('strong',source.id),node('span',skillKind(source.kind)+' · '+(source.cached_skill_count??0)+'个缓存Skill','text-muted'),node('span',source.location,'text-small text-muted'));
    const recent=skillSyncStatuses.get(source.id);
    if(recent)info.append(node('span',recent.status==='error'?'本次同步失败，使用旧缓存':recent.status==='disabled'?'来源停用，未同步':recent.status==='unchanged'?'本次检查：无需更新':'本次同步：'+recent.status,'text-small text-muted'));
    const state=createStatusBadge(source.enabled?'已启用':'已停用',source.enabled?'success':'neutral');
    const controls=node('div',undefined,'skill-source-actions');
    controls.append(button('同步',()=>syncSkills(source.id)),button(source.enabled?'停用来源':'启用来源',()=>setSourceEnabled(source,!source.enabled)),button('移除',()=>removeSource(source)));
    card.append(info,state,controls);list.append(card);
  }
}
async function loadSkillPermissions(){
  const roots=$('skill-roots');roots.replaceChildren();
  try{
    const data=await rpc('skill_permissions');skillPermissionSnapshot=data;
    for(const [name,root] of Object.entries(data.available_roots||{})){
      const row=node('div',undefined,'permission-row');
      const label=node('div');label.append(node('strong',name),node('p',root.path,'text-small text-muted'));row.append(label);
      for(const [mode,text,allowed,enabled] of [
        ['read','允许读取',root.read,(data.local_roots||[]).includes(name)],
        ['write','允许写入',root.write,(data.writable_roots||[]).includes(name)]]){
        const group=node('div',undefined,'checkbox-group'),check=node('input');
        check.type='checkbox';check.dataset.root=name;check.dataset.permission=mode;
        check.checked=enabled;check.disabled=!allowed;group.append(check,node('label',text));row.append(group);
      }
      roots.append(row);
    }
  }catch(error){roots.append(node('p','无法读取授权：'+friendlyError(error),'provider-error'));}
}
async function saveSkillPermissions(){
  if(!skillPermissionSnapshot)throw '请先读取当前权限。';
  const selections=mode=>[...$('skill-roots').querySelectorAll('input[data-permission="'+mode+'"]:checked')].map(el=>el.dataset.root);
  const local_roots=selections('read'),writable_roots=selections('write');
  if(!window.confirm('确认保存Skill专用目录权限？\n读取：'+(local_roots.join(', ')||'无')+'\n写入：'+(writable_roots.join(', ')||'无')+'\n请先停用Skill Library，保存后重新连接。'))return;
  await rpc('skill_permissions',{local_roots,writable_roots,confirmed:true});
  await loadSkillPermissions();notice('Skill权限已保存；请重新连接Skill Library生效。');
}
$('skill-open-sources').onclick=()=>showSkillSources(true);
$('skill-close-sources').onclick=()=>showSkillSources(false);
$('skill-show-add-source').onclick=()=>{$('skill-source-add-details').open=true;$('skill-source-location').focus();};
$('skill-source-add').onclick=()=>action(addSkillSource);
$('skill-sync-all').onclick=()=>action(()=>syncSkills());
$('reload-skill-list').onclick=()=>action(loadSkillList);
$('skill-source-reload').onclick=()=>action(loadSkillList);
$('skill-permissions-reload').onclick=()=>action(loadSkillPermissions);
$('save-skill-permissions').onclick=()=>action(saveSkillPermissions);
$('skill-install-link').onclick=()=>action(async()=>{
  view('component-management');currentComponentView='runtime';
  renderComponentManagement();await loadProviders();
  $('install-provider').value='skill-library';$('install-panel').scrollIntoView({block:'start'});
});
for(const id of ['skill-source-kind','skill-source-location','skill-source-branch'])
  $(id).addEventListener(id==='skill-source-kind'?'change':'input',()=>{saveSkillDraft();updateSkillDraftUI();});
for(const id of ['skill-list-query','skill-list-source','skill-list-state'])
  $(id).addEventListener(id==='skill-list-query'?'input':'change',renderSkillList);
restoreSkillDraft();updateSkillDraftUI();
document.querySelectorAll('[data-view="skill-list"]').forEach(el=>el.addEventListener('click',()=>{showSkillSources(false);action(loadSkillList);}));
