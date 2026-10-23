// Layout regression: fixed sidebar and centered content on maximized/wide windows.
// Browser UI only; management RPC is mocked and no local runtime state is changed.
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require('../browser-component/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1024, height: 800}});
    await page.addInitScript(() => {
      window.__TAURI__ = {core: {invoke: async (_method, request) => {
        if (request?.command === 'status') {
          return {
            config: {version: 'test', data_directory: 'private', onboarding_complete: true,
              runtime_port: 18081, health_port: 18082, browser_port: 8931,
              browser_enabled: false, autostart: false},
            runtime: 'stopped', tunnel: 'stopped', browser: 'disabled',
            workspaces: {roots: {}, config_sha256: 'test'}
          };
        }
        if (request?.command === 'activity_recent') return {available: true, activities: []};
        return {};
      }}};
    });
    await page.goto(pathToFileURL(path.join(__dirname, '../ui/index.html')).href);

    for (const width of [900, 1024, 1440, 1920, 2560]) {
      await page.setViewportSize({width, height: 900});
      for (const name of ['overview', 'workspaces', 'skill-list', 'about']) {
        await page.locator(`nav [data-view="${name}"]`).click();
        const layout = await page.evaluate(() => {
          const main = document.querySelector('main').getBoundingClientRect();
          const header = document.querySelector('main > header').getBoundingClientRect();
          const active = document.querySelector('main > .view:not(.hidden)').getBoundingClientRect();
          const sidebar = document.querySelector('aside').getBoundingClientRect();
          return {
            viewport: document.documentElement.clientWidth,
            scrollWidth: document.documentElement.scrollWidth,
            mainRight: main.right,
            sidebarRight: sidebar.right,
            headerLeft: header.left,
            headerRight: header.right,
            headerWidth: header.width,
            viewLeft: active.left,
            viewRight: active.right,
            viewWidth: active.width
          };
        });
        const targetCenter = (layout.sidebarRight + layout.viewport) / 2;
        const center = rect => (layout[rect + 'Left'] + layout[rect + 'Right']) / 2;
        assert.ok(Math.abs(layout.mainRight - width) <= 2,
          `${width}px ${name}: main must fill the area to the right edge: ${JSON.stringify(layout)}`);
        assert.ok(Math.abs(center('header') - targetCenter) <= 2,
          `${width}px ${name}: header off-center: ${JSON.stringify(layout)}`);
        assert.ok(Math.abs(center('view') - targetCenter) <= 2,
          `${width}px ${name}: view off-center: ${JSON.stringify(layout)}`);
        assert.ok(Math.abs(layout.viewWidth - layout.headerWidth) <= 2,
          `${width}px ${name}: header and content widths differ`);
        assert.ok(layout.viewWidth <= 1337, `${width}px ${name}: content too wide`);
        assert.ok(layout.headerLeft >= layout.sidebarRight,
          `${width}px ${name}: header overlaps sidebar`);
        assert.ok(layout.scrollWidth <= width + 2,
          `${width}px ${name}: horizontal document overflow: ${JSON.stringify(layout)}`);
      }
    }
    console.log('PASS: centered layout and no overflow for 900/1024/1440/1920/2560px across four pages');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
