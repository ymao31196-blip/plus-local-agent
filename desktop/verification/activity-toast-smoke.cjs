// Browser-level smoke for local activity and fixed notification lifecycle.
// Mock management calls only; does not touch the installed Desktop Runtime.
'use strict';
const { chromium } = require('../browser-component/node_modules/playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 900, height: 600 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.rpcCalls = [];
      window.activityUnavailable = false;
      window.moreActivities = false;
      window.__TAURI__ = { core: { invoke: async (command, request) => {
        if (command !== 'manage') throw new Error('Unexpected native RPC');
        window.rpcCalls.push(request.command);
        if (request.command === 'activity_recent') {
          const component = { id: '3', tool: 'browser.navigate', source: 'capability',
            provider: 'browser', capability: 'browser.navigate',
            started_at: new Date().toISOString(), outcome: 'failed' };
          return window.activityUnavailable ? { available: false, activities: [] } : {
            available: true, activities: [
              { id: '1', tool: 'write_text', source: 'native_mcp',
                started_at: new Date().toISOString(), outcome: 'success' },
              { id: '2', tool: 'run_process', source: 'native_mcp', program: 'python.exe',
                started_at: new Date().toISOString(), outcome: 'unknown' },
              component,
              ...(window.moreActivities ? Array.from({ length: 8 }, (_, i) => ({
                id: 'extra-' + i, tool: 'run_process', source: 'native_mcp', program: 'python',
                started_at: new Date().toISOString(), outcome: 'success'
              })) : [])
            ], component_activities: [component]
          };
        }
        if (request.command === 'status') return {
          config: { version: 'test', data_directory: 'private', onboarding_complete: true,
            runtime_port: 18081, health_port: 18082, tunnel_id: '',
            browser_port: 8931, browser_enabled: false, autostart: false },
          runtime: 'stopped', tunnel: 'stopped', browser: 'disabled',
          workspaces: { roots: {}, config_sha256: 'test' }
        };
        throw new Error('Unexpected management command: ' + request.command);
      }}};
    });
    await page.goto(pathToFileURL(path.join(__dirname, '../ui/index.html')).href);
    await page.waitForFunction(() => document.querySelectorAll('.activity-row').length === 3);
    assert.match(await page.locator('#activity-list').textContent(), /write_text.*成功/s);
    assert.match(await page.locator('#activity-list').textContent(), /python.exe.*run_process.*进行中/s);
    assert.match(await page.locator('#activity-list').textContent(), /Browser.*navigate.*失败/s);
    await page.locator('#activity-filter').selectOption('capability');
    assert.equal(await page.locator('.activity-row').count(), 1);
    assert.match(await page.locator('#activity-list').textContent(), /Browser.*navigate/s);
    await page.locator('.activity-row summary').click();
    assert.ok(await page.locator('.activity-details').isVisible());
    assert.match(await page.locator('.activity-details').textContent(), /组件ID.*browser/s);
    await page.locator('#activity-filter').selectOption('process');
    assert.equal(await page.locator('.activity-row').count(), 2);
    await page.locator('#activity-filter').selectOption('all');

    await page.evaluate(() => { window.moreActivities = true; });
    await page.evaluate(() => refreshActivity());
    assert.equal(await page.locator('.activity-row').count(), 6);
    assert.ok(await page.locator('#activity-more').isVisible());
    await page.locator('#activity-more').click();
    assert.equal(await page.locator('.activity-row').count(), 11);
    await page.locator('#activity-more').click();
    assert.equal(await page.locator('.activity-row').count(), 6);
    await page.evaluate(() => { window.moreActivities = false; });
    await page.evaluate(() => refreshActivity());

    await page.evaluate(() => notice('已保存', false));
    assert.ok(await page.locator('#notice').isVisible());
    assert.equal(await page.locator('#notice').evaluate(node => getComputedStyle(node).position), 'fixed');
    await page.locator('#notice-close').click();
    assert.ok(!(await page.locator('#notice').isVisible()));
    await page.evaluate(() => notice('可自动消失的消息', false));
    await page.locator('#notice').waitFor({ state: 'hidden', timeout: 5500 });

    await page.evaluate(() => { window.activityUnavailable = true; });
    await page.evaluate(() => refreshActivity());
    assert.match(await page.locator('#activity-fetched').textContent(), /数据未更新/);
    assert.match(await page.locator('#activity-list').textContent(), /write_text/);

    const countBefore = await page.evaluate(() => window.rpcCalls.filter(x => x === 'activity_recent').length);
    await page.locator('nav [data-view="setup"]').click();
    await page.waitForTimeout(3300);
    const countAfter = await page.evaluate(() => window.rpcCalls.filter(x => x === 'activity_recent').length);
    assert.equal(countAfter, countBefore, 'Hidden overview should not be polled');
    assert.deepEqual(errors, []);
    console.log('PASS: activity display, statuses, fixed/dismissible/auto-hide toast, stale state, hidden-page polling');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
