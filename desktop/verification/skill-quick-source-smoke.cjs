// Skill Library user-page E2E in real Edge DOM; management RPC is mocked.
'use strict';
const {chromium}=require('../browser-component/node_modules/playwright');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1100,height:850}});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('dialog',d=>d.accept());
    await page.addInitScript(()=>{
      window.calls=[];
      window.registered=[];
      window.diskSkillNames=['first'];
      window.cache=[];
      window.toggled={};
      window.failSync=false;
      window.__TAURI__={core:{invoke:async(method,request)=>{
        if(method!=='manage')throw Error('unexpected management method');
        const {command,args={}}=request;
        window.calls.push({command,args});
        if(command==='status')return {config:{version:'test',onboarding_complete:true,
          data_directory:'private',runtime_port:18081,health_port:18082,
          browser_port:8931,browser_enabled:false,autostart:false},
          runtime:'stopped',tunnel:'stopped',browser:'disabled',
          workspaces:{roots:{},config_sha256:'workspace-sha'}};
        if(command==='skill_permissions')return {
          available_roots:{approved:{path:'D:\\ExampleSkills',read:true,write:true}},
          local_roots:['approved'],writable_roots:[]
        };
        if(command==='skill_action'){
          const a=args.arguments||{};
          if(args.action==='sources')return {data:{sources:window.registered}};
          if(args.action==='states')return {data:{skills:window.cache}};
          if(args.action==='manage'){
            if(a.action==='add'){
              if(window.registered.some(s=>s.id===a.source_id))throw Error('duplicate ID');
              window.registered.push({id:a.source_id,kind:a.kind,location:a.location,
                enabled:true,branch:a.branch||'main',cached_skill_count:0});
              return {data:{action:'added'}};
            }
            if(a.action==='update'){
              const s=window.registered.find(x=>x.id===a.source_id);s.enabled=a.enabled;
              window.cache=window.cache.map(x=>({...x,source_enabled:s.enabled,
                effective_enabled:s.enabled&&x.enabled}));
              return {data:{action:'updated'}};
            }
            if(a.action==='remove'){
              window.registered=window.registered.filter(x=>x.id!==a.source_id);
              window.cache=window.cache.filter(x=>x.source_id!==a.source_id);
              return {data:{action:'removed'}};
            }
          }
          if(args.action==='toggle'){
            window.toggled[a.source_id+':'+a.name]=a.enabled;
            window.cache=window.cache.map(x=>x.name===a.name&&x.source_id===a.source_id?
              {...x,enabled:a.enabled,effective_enabled:a.enabled&&x.source_enabled}:x);
            return {data:{enabled:a.enabled}};
          }
          if(args.action==='sync'){
            const results={};
            for(const s of window.registered.filter(x=>!a.source_id||x.id===a.source_id)){
              if(!s.enabled){results[s.id]={status:'disabled'};continue;}
              if(window.failSync){results[s.id]={status:'error',error:'simulated offline'};continue;}
              window.cache=window.cache.filter(x=>x.source_id!==s.id);
              for(const name of window.diskSkillNames){
                const enabled=window.toggled[s.id+':'+name]!==false;
                window.cache.push({name,source_id:s.id,ref:s.id+':'+name,
                  enabled,source_enabled:s.enabled,effective_enabled:enabled});
              }
              s.cached_skill_count=window.diskSkillNames.length;
              results[s.id]={status:'updated',skill_count:s.cached_skill_count};
            }
            return {data:{results}};
          }
          if(args.action==='load')return {data:{content:'# '+a.name+'\nA test Skill.',ref:a.name}};
          throw Error('unsupported Skill RPC '+args.action);
        }
        if(command==='provider_catalog')return {providers:[],runtime_observed:false};
        if(command==='installation_status')return {jobs:[],installed_receipts:[]};
        if(command==='activity_recent')return {records:[],events:[]};
        throw Error('unexpected command '+command);
      }}};
    });
    const target=pathToFileURL(path.join(__dirname,'../ui/index.html')).href;
    await page.goto(target);
    await page.waitForFunction(()=>!document.querySelector('#refresh').disabled);
    assert.equal(await page.locator('nav [data-view="skill-list"]').count(),1);
    assert.equal(await page.locator('nav [data-view="skills"]').count(),0);
    assert.equal(await page.locator('#skill-group').count(),0);
    await page.locator('nav [data-view="skill-list"]').click();
    await page.waitForFunction(()=>document.querySelector('#skill-list-status').textContent.includes('尚无Skill来源'));
    assert.equal(await page.locator('#skill-source-location').inputValue(),'');
    assert.match(await page.locator('#skill-source-location').getAttribute('placeholder'),/尚无/);
    await page.locator('#skill-open-sources').click();
    assert.equal(await page.locator('#skill-sources-panel').isVisible(),true);
    assert.equal(await page.locator('#skill-list-panel').isVisible(),false);
    await page.locator('#skill-show-add-source').click();
    await page.locator('#skill-source-location').fill('D:\\ExampleSkills');
    await page.reload();
    await page.waitForFunction(()=>!document.querySelector('#refresh').disabled);
    await page.locator('nav [data-view="skill-list"]').click();
    await page.locator('#skill-open-sources').click();
    assert.equal(await page.locator('#skill-source-location').inputValue(),'D:\\ExampleSkills');
    await page.locator('#skill-show-add-source').click();
    await page.locator('#skill-source-add').click();
    await page.waitForFunction(()=>document.querySelector('#skill-source-summary').textContent.includes('1个来源'));
    assert.equal(await page.evaluate(()=>window.calls.filter(c=>c.command==='skill_action'&&c.args.action==='manage'&&c.args.arguments.action==='add').length),1);
    await page.locator('#skill-show-add-source').click();
    await page.locator('#skill-source-add').click();
    await page.waitForFunction(()=>document.querySelector('#skill-source-result').textContent.includes('已同步已有来源'));
    assert.equal(await page.evaluate(()=>window.calls.filter(c=>c.command==='skill_action'&&c.args.action==='manage'&&c.args.arguments.action==='add').length),1);
    assert.equal(await page.locator('#skill-roots .permission-row').count(),1);
    await page.locator('#skill-close-sources').click();
    await page.waitForFunction(()=>document.querySelector('#skill-list-status').textContent.includes('已缓存1个Skill'));
    await page.locator('#skill-list-items button').first().click();
    assert.match(await page.locator('#skill-list-content').textContent(),/A test Skill/);
    await page.locator('#skill-list-items button').last().click();
    await page.waitForFunction(()=>document.querySelector('#skill-list-status').textContent.includes('0个可用'));
    await page.evaluate(()=>{window.diskSkillNames.push('second');});
    await page.locator('#reload-skill-list').click();
    await page.waitForFunction(()=>document.querySelector('#skill-list-status').textContent.includes('已缓存1个Skill'));
    await page.locator('#skill-sync-all').click();
    await page.waitForFunction(()=>document.querySelector('#skill-list-status').textContent.includes('已缓存2个Skill'));
    assert.match(await page.locator('#skill-sync-status').textContent(),/已检查1个来源/);
    await page.locator('#skill-open-sources').click();
    await page.locator('#skill-source-items .skill-source-actions button').nth(1).click();
    await page.waitForFunction(()=>document.querySelector('#skill-source-items').textContent.includes('已停用'));
    await page.locator('#skill-source-items .skill-source-actions button').nth(1).click();
    await page.waitForFunction(()=>document.querySelector('#skill-source-items').textContent.includes('已启用'));
    await page.locator('#skill-close-sources').click();
    assert.match(await page.locator('#skill-list-status').textContent(),/1个可用/);
    await page.locator('#skill-open-sources').click();
    await page.locator('#skill-source-items .skill-source-actions button').nth(2).click();
    await page.waitForFunction(()=>document.querySelector('#skill-source-summary').textContent.includes('0个来源'));
    assert.equal(await page.evaluate(()=>window.diskSkillNames.length),2);
    // GitHub URL is normalized to owner/repo without exposing Git operations.
    await page.locator('#skill-show-add-source').click();
    await page.locator('#skill-source-kind').selectOption('github');
    await page.locator('#skill-source-location').fill('https://github.com/example/skill-repo.git');
    await page.locator('#skill-source-add').click();
    await page.waitForFunction(()=>document.querySelector('#skill-source-summary').textContent.includes('1个来源'));
    assert.equal(await page.evaluate(()=>window.registered[0].kind),'github');
    assert.equal(await page.evaluate(()=>window.registered[0].location),'example/skill-repo');
    await page.evaluate(()=>{window.failSync=true;window.diskSkillNames.push('third');});
    await page.locator('#skill-close-sources').click();
    await page.locator('#skill-sync-all').click();
    await page.waitForFunction(()=>document.querySelector('#skill-sync-status').textContent.includes('simulated offline'));
    assert.match(await page.locator('#skill-sync-status').textContent(),/仍使用上次有效缓存/);
    assert.match(await page.locator('#skill-list-status').textContent(),/已缓存2个Skill/);
    assert.deepEqual(errors,[]);
    console.log('PASS: one entry, WebView draft, add/deduplicate, cache vs sync, read, Skill toggle, source enable/disable, remove without deleting original, permissions');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});

