// Actual installed Tauri/WebView2 UI test. Remote debugging is enabled ONLY for
// this owned test process, never persisted or configured in the shipped app.
const { chromium } = require('../browser-component/node_modules/playwright');
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const args=process.argv.slice(2);
const option=name=>args[args.indexOf(name)+1];
const executable=path.resolve(option('--executable'));
const output=path.resolve(option('--output'));
fs.mkdirSync(output,{recursive:true});
const data=fs.mkdtempSync(path.join(os.tmpdir(),'pla-native-验收-'));
const workspace=fs.mkdtempSync(path.join(os.tmpdir(),'pla-workspace-授权-'));
const evidence={executable,data,workspace,clean_windows:'NOT TESTED',tunnel_e2e:'NOT TESTED',chatgpt_e2e:'NOT TESTED',tests:[]};
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function port(){const server=net.createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));const value=server.address().port;await new Promise(r=>server.close(r));return value;}
async function connect(debugPort){for(let i=0;i<100;i++){try{return await chromium.connectOverCDP(`http://127.0.0.1:${debugPort}`);}catch{await pause(300);}}throw Error('Native WebView2 debugging target not available');}
async function awaitExit(child){for(let i=0;i<60;i++){if(child.exitCode!==null || child.signalCode!==null)return;await pause(200);}throw Error('Native app failed to exit');}
function alive(pid){try{process.kill(pid,0);return true;}catch{return false;}}
let child,browser,page;
(async()=>{
  const debugPort=await port(),runtimePort=await port(),healthPort=await port();
  const env={...process.env,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:`--remote-debugging-port=${debugPort}`};
  child=spawn(executable,['--data-dir',data],{env,windowsHide:true,stdio:'ignore'});
  browser=await connect(debugPort);
  page=browser.contexts()[0].pages().find(p=>p.url().includes('tauri')) || browser.contexts()[0].pages()[0];
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.waitForFunction(()=>document.querySelector('#heading').textContent==='首次配置与连接');
  await page.locator('#runtime-port').fill(String(runtimePort));
  await page.locator('#health-port').fill(String(healthPort));
  await page.locator('#save-connection').click();
  await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('配置已保存'));
  assert.equal(await page.locator('#data-directory').textContent(),data);
  evidence.tests.push({test:'installed wizard and UTF-8 private directory',status:'PASS'});
  await page.locator('nav [data-view="workspaces"]').click();
  await page.locator('#workspace-name').fill('desktop_test');await page.locator('#workspace-path').fill(workspace);
  await page.locator('#allow-write').check();await page.locator('#allow-execute').check();await page.locator('#save-workspace').click();
  await page.waitForFunction(()=>document.querySelector('#workspace-list').textContent.includes('desktop_test'));
  evidence.tests.push({test:'installed UI workspace authorization persistence',status:'PASS'});
  await page.locator('nav [data-view="overview"]').click();await page.locator('#start').click();
  await page.waitForFunction(()=>document.querySelector('#runtime').textContent==='MCP 已就绪',null,{timeout:45000});
  await page.locator('nav [data-view="setup"]').click();await page.locator('#verify').click();
  await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('PASS'));
  evidence.tests.push({test:'installed frontend IPC -> frozen Runtime -> real MCP diagnostic',status:'PASS'});
  const reg=path.join(process.env.SystemRoot,'System32','reg.exe');
  const startupKey='HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run';
  let hasPriorStartup=false;
  try{execFileSync(reg,['query',startupKey,'/v','PLADesktop'],{stdio:'pipe'});hasPriorStartup=true;}catch{}
  if(!hasPriorStartup){
    await page.locator('nav [data-view="about"]').click();
    await page.locator('#autostart').check();
    await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('登录启动偏好已保存'));
    assert.ok(execFileSync(reg,['query',startupKey,'/v','PLADesktop'],{encoding:'utf8',stdio:'pipe'}).toLowerCase().includes(executable.toLowerCase()));
    await page.locator('#autostart').uncheck();
    await page.waitForFunction(()=>!document.querySelector('#autostart').checked && !document.querySelector('#refresh').disabled);
    let remains=false;try{execFileSync(reg,['query',startupKey,'/v','PLADesktop'],{stdio:'pipe'});remains=true;}catch{}
    assert.equal(remains,false);
    evidence.tests.push({test:'installed login-start opt-in registry persistence and opt-out',status:'PASS'});
  }else{evidence.tests.push({test:'installed login-start preference',status:'NOT TESTED',detail:'Existing startup registration preserved'});}
  await page.locator('nav [data-view="overview"]').click();
  await page.screenshot({path:path.join(output,'native-overview.png')});
  const duplicate=spawn(executable,['--data-dir',data],{env,windowsHide:true,stdio:'ignore'});
  await awaitExit(duplicate);assert.equal(child.exitCode,null);
  evidence.tests.push({test:'installed native single instance',status:'PASS'});
  await page.evaluate(()=>window.__TAURI__.window.getCurrentWindow().close());await pause(500);
  assert.equal(await page.evaluate(()=>window.__TAURI__.window.getCurrentWindow().isVisible()),false);
  assert.equal((await page.evaluate(()=>window.__TAURI__.core.invoke('manage',{command:'status',args:{}}))).runtime,'ready');
  await page.evaluate(()=>window.__TAURI__.window.getCurrentWindow().show());
  evidence.tests.push({test:'native close hides to tray while owned Runtime remains ready',status:'PASS'});
  await page.locator('#quit-app').click();await awaitExit(child);await browser.close();browser=null;
  evidence.tests.push({test:'installed normal quit',status:'PASS'});
  child=spawn(executable,['--data-dir',data],{env,windowsHide:true,stdio:'ignore'});
  browser=await connect(debugPort);page=browser.contexts()[0].pages().find(p=>p.url().includes('tauri')) || browser.contexts()[0].pages()[0];
  await page.waitForFunction(()=>document.querySelector('#data-directory').textContent.length>0);
  const snapshot=await page.evaluate(()=>window.__TAURI__.core.invoke('manage',{command:'status',args:{}}));
  assert.equal(snapshot.config.runtime_port,runtimePort);assert.equal(snapshot.workspaces.roots.desktop_test.path,workspace);
  evidence.tests.push({test:'installed reopen preserves configuration and workspace',status:'PASS'});
  await page.locator('nav [data-view="about"]').click();
  await page.locator('#browser-enabled').check();await page.locator('#browser-port').fill(String(await port()));await page.locator('#save-browser').click();
  await page.waitForFunction(()=>document.querySelector('#notice').textContent.includes('浏览器偏好已保存'));
  await page.locator('nav [data-view="overview"]').click();await page.locator('#start').click();
  await page.waitForFunction(()=>document.querySelector('#runtime').textContent==='MCP 已就绪',null,{timeout:45000});
  const status=await page.evaluate(()=>window.__TAURI__.core.invoke('manage',{command:'status',args:{}}));
  const runtimePid=status.owned_processes.runtime.pid;
  assert.equal(status.browser,'provider_ready',JSON.stringify(status));
  const runnerState=JSON.parse(fs.readFileSync(path.join(data,'state','execution_runner','runtime.json'),'utf8'));
  const browserState=JSON.parse(fs.readFileSync(path.join(data,'state','browser_runtime','runtime.json'),'utf8'));
  const ownedPids=[runtimePid,status.owned_processes.browser.pid,runnerState.process_id,browserState.mcp_pid,browserState.keeper_pid];
  child.kill();await awaitExit(child);
  for(let i=0;i<60 && ownedPids.some(alive);i++)await pause(100);
  assert.ok(ownedPids.every(pid=>!alive(pid)),'Windows Job failed to reap an owned child on native crash');
  evidence.tests.push({test:'installed native crash Job Object cleans Runtime, runner, browser and keeper',status:'PASS'});
  assert.deepEqual(errors,[]);evidence.tests.push({test:'installed frontend JavaScript errors',status:'PASS'});
})().catch(error=>{evidence.tests.push({test:'native UI acceptance',status:'FAIL',error:error.stack});process.exitCode=1;}).finally(async()=>{
  if(child && child.exitCode===null && child.signalCode===null)child.kill();
  if(browser)try{await browser.close();}catch{}
  fs.writeFileSync(path.join(output,'native-ui-report.json'),JSON.stringify(evidence,null,2));
  console.log(JSON.stringify(evidence,null,2));
});
