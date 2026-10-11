// Real browser DOM with mocked RPCs: navigation and preserved controls only.
// This does not verify installed binaries or actual MCP connectivity.
'use strict';
const {chromium} = require('../browser-component/node_modules/playwright');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1040,height:780}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.dismiss());
    await page.addInitScript(() => {
      window.managementCalls = [];
      window.__TAURI__ = {core:{invoke:async (method, request) => {
        if (method !== 'manage') throw Error('Unexpected native command');
        const {command,args} = request;
        window.managementCalls.push({command,args});
        switch(command) {
          case 'status': return {config:{version:'test',data_directory:'private',
            onboarding_complete:true,runtime_port:18081,health_port:18082,
            browser_port:8931,browser_enabled:false,autostart:false},
            runtime:'stopped',tunnel:'stopped',browser:'disabled',
            workspaces:{roots:{},config_sha256:'workspace-hash'}};
          case 'repository_list': return {repositories:[{name:'Test',url:'file:///test',enabled:true}]};
          case 'provider_installed_list': return {providers:[]};
          case 'provider_discover': return {providers:[
            {provider_id:'number',name:'Number Echo',description:'numeric input',category:'general',latest_version:'1.0.0',installed:false},
            {provider_id:'other',name:'Other Echo',description:'document echo',category:'documents',latest_version:'1.0.0',installed:false},
            {provider_id:'helper',name:'Documents Helper',description:'document utilities',category:'documents',latest_version:'1.0.0',installed:false}
          ],total:3,installed_count:0};
          case 'provider_catalog': return {manifest_directory:'private/provider_manifests',
            runtime_observed:false,providers:window.repositoryRemoved?[]:[{provider_id:'installed-echo',
              runtime_kind:'python',execution_files_present:true,requested_enabled:false,
              repository_installation:{version:'1.0.0',state:'installed',repository_url:'file:///test'}}]};
          case 'provider_health_check': return {status:'healthy',scope:'installation_integrity',checks:{manifest:true}};
          case 'provider_uninstall_remote':
            if(!args.confirmed)return {sha256:'reviewed-uninstall'};
            if(args.expected_sha256!=='reviewed-uninstall')throw Error('Unreviewed uninstall');
            window.repositoryRemoved=true;
            return {started:true,job:'repository:installed-echo'};
          case 'installation_status': return {jobs:window.repositoryRemoved?[{job:'repository:installed-echo',state:'completed'}]:[],installed_receipts:[]};
          case 'provider_action': return {status:'selection_saved'};
          case 'repository_add': throw 'Repository URL rejected';
          default: throw Error('Unexpected RPC: '+command);
        }
      }}};
      window.confirm = () => true;
    });
    await page.goto(pathToFileURL(path.join(__dirname,'../ui/index.html')).href);
    await page.waitForFunction(() => !document.querySelector('#refresh').disabled);
    assert.equal(await page.locator('nav [data-view="providers"]').count(),0);
    await page.locator('nav [data-view="component-management"]').click();
    await page.waitForFunction(() => !document.querySelector('#refresh').disabled);
    assert.equal(await page.locator('#heading').textContent(),'组件与 MCP 插件');
    assert.ok(await page.locator('#providers').isVisible());
    assert.equal(await page.locator('#view-runtime').getAttribute('aria-pressed'),'true');
    assert.match(await page.locator('#provider-list').textContent(),/仓库安装.*1\.0\.0/);
    assert.ok(await page.getByRole('button',{name:'启用并连接',exact:true}).isVisible());
    await page.getByRole('button',{name:'检查完整性',exact:true}).click();
    await page.waitForFunction(() => !document.querySelector('#refresh').disabled);
    assert.ok(await page.evaluate(() => window.managementCalls.some(c=>c.command==='provider_health_check')));
    await page.getByRole('button',{name:'卸载',exact:true}).click();
    await page.waitForFunction(() => !document.querySelector('#refresh').disabled && !document.querySelector('#provider-list').textContent.includes('installed-echo'));
    assert.ok(await page.evaluate(() => window.managementCalls.some(c=>c.command==='provider_uninstall_remote'&&c.args.confirmed&&c.args.expected_sha256==='reviewed-uninstall')));
    await page.locator('#manifest-content').fill('preserved manifest draft');
    await page.evaluate(() => {window.originalManifestInput=document.querySelector('#manifest-content');});
    await page.locator('#view-repositories').click();
    assert.ok(!(await page.locator('#providers').isVisible()));
    await page.locator('[data-action="browse"]').click();
    await page.waitForFunction(() => document.querySelector('#view-marketplace').getAttribute('aria-pressed')==='true');
    assert.ok(await page.locator('#provider-search').isVisible());
    assert.ok((await page.locator('#provider-search').boundingBox()).width>=200,'Search input must remain usable beside category filter');
    await page.locator('#provider-search').fill('Echo');
    await page.locator('#category-filter').selectOption('documents');
    assert.equal(await page.locator('#marketplace-content .provider-card').count(),1);
    assert.match(await page.locator('#marketplace-content').textContent(),/Other Echo/);
    await page.locator('#provider-search').fill('Helper');
    assert.equal(await page.locator('#category-filter').inputValue(),'documents');
    assert.equal(await page.locator('#marketplace-content .provider-card').count(),1);
    assert.match(await page.locator('#marketplace-content').textContent(),/Documents Helper/);

    assert.equal(await page.locator('#view-installed').count(),0);
    assert.equal(await page.locator('nav [data-view="development"]').count(),0);
    await page.locator('#view-runtime').click();
    await page.waitForFunction(() => !document.querySelector('#refresh').disabled);
    assert.equal(await page.locator('#manifest-content').inputValue(),'preserved manifest draft');
    assert.ok(await page.evaluate(() => window.originalManifestInput===document.querySelector('#manifest-content')));
    await page.locator('#rescan-providers').click();
    await page.waitForFunction(() => !document.querySelector('#refresh').disabled);
    assert.ok(await page.evaluate(() => window.managementCalls.some(c=>c.command==='provider_action'&&c.args.action==='rescan')));
    await page.locator('nav [data-view="overview"]').click();
    assert.ok(!(await page.locator('#providers').isVisible()));
    await page.evaluate(() => view('providers'));
    await page.waitForFunction(() => !document.querySelector('#refresh').disabled);
    assert.ok(await page.locator('#providers').isVisible());
    const message = await page.evaluate(async()=>{
      try {await window.componentManager.addRepository('invalid');} catch(error) {return error.message;}
    });
    assert.equal(message,'Failed to add repository: Repository URL rejected');
    assert.deepEqual(errors,[]);
    console.log('PASS: unified navigation; three tabs; repository browse; preserved draft/DOM; original rescan RPC; legacy routing; string errors; usable search width and combined filters');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
