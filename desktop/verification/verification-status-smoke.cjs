// Browser regression: verification badge must follow the current Runtime session.
// Uses the real Desktop HTML/JS and mocked read-only management RPC responses.
'use strict';
const { chromium } = require('../browser-component/node_modules/playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1040,height:780}});
    const errors=[];
    page.on('pageerror', error=>errors.push(error.message));
    await page.addInitScript(() => {
      window.sessionStarted = new Date(Date.now() - 20000).toISOString();
      window.runtimeState = 'ready';
      window.runtimeAlive = true;
      window.manualVerified = false;
      window.activityUnavailable = false;
      window.activityRows = [{
        id:'old', tool:'read_text', outcome:'success', source:'native_mcp',
        started_at:new Date(Date.now()-120000).toISOString()
      }];
      window.__TAURI__ = {core:{invoke:async (method,{command,args})=>{
        if(method!=='manage') throw Error('Unexpected Tauri command');
        if(command==='status') return {
          config:{version:'test',data_directory:'private',onboarding_complete:true,
            runtime_port:18081,health_port:18082,tunnel_id:'',
            browser_port:8931,browser_enabled:false,autostart:false},
          runtime:window.runtimeState,
          tunnel:'stopped',browser:'disabled',
          workspaces:{roots:{},config_sha256:'test'},
          local_mcp_verified:window.manualVerified
        };
        if(command==='activity_recent') return window.activityUnavailable
          ? {available:false,activities:[],runtime_alive:window.runtimeAlive,
             runtime_started_at:window.sessionStarted}
          : {available:true,activities:window.activityRows,
             runtime_alive:window.runtimeAlive,
             runtime_started_at:window.sessionStarted};
        throw Error('Unexpected RPC: '+command);
      }}};
    });
    const verify = async (expected) => {
      await page.evaluate(async() => { await refresh(); await refreshActivity(); });
      assert.equal((await page.locator('#verified-status').textContent()).trim(),expected);
    };
    await page.goto(pathToFileURL(path.join(__dirname,'../ui/index.html')).href);
    await page.waitForFunction(() => document.querySelector('#verified-status').textContent !== '未验证');
    // An old successful call cannot verify a newly started Runtime.
    await verify('等待工具调用');
    await page.evaluate(() => { window.activityRows.unshift({
      id:'success',tool:'git_status',outcome:'success',source:'native_mcp',
      started_at:new Date().toISOString()
    }); });
    await verify('本地调用已验证');
    assert.ok(await page.locator('#verification-card').evaluate(el=>el.classList.contains('ready')));

    await page.evaluate(() => { window.activityRows.unshift({
      id:'failure',tool:'run_process',outcome:'failed',source:'native_mcp',
      started_at:new Date().toISOString()
    }); });
    await verify('最近调用失败');
    assert.ok(await page.locator('#verification-card').evaluate(el=>el.classList.contains('error')));

    await page.evaluate(() => { window.sessionStarted=new Date(Date.now()+1000).toISOString(); });
    await verify('等待工具调用');
    await page.evaluate(() => { window.manualVerified = true; });
    await verify('本地诊断已通过');

    await page.evaluate(() => { window.runtimeAlive=false; window.runtimeState='stopped'; });
    await verify('未连接');
    await page.evaluate(() => { window.runtimeAlive=true; window.runtimeState='ready'; window.manualVerified=false; window.activityUnavailable=true; });
    await verify('活动状态不可用');
    await page.evaluate(() => { window.activityUnavailable=false; });
    await verify('等待工具调用');

    assert.deepEqual(errors, []);
    console.log('PASS: historical isolation, success, failure, restart, manual probe, stop, telemetry unavailable');
  } finally { await browser.close(); }
})().catch(err=>{console.error(err);process.exitCode=1});
