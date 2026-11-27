// Captures the authored frame after the cover signals readiness.
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {launchBrowser} = require('./browser.cjs');
const {openAssets} = require('./browser_assets.cjs');

const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
const puppeteer = createRequire(path.join(spec.modules, 'package.json'))('puppeteer-core');

(async () => {
  let browser, assets;
  try {
    const launched = await launchBrowser(puppeteer, spec.chrome);
    browser = launched.browser;
    assets = await openAssets(spec.work);
    console.log('封面使用 WebGL：' + launched.gpu);
    for (const entry of spec.entries) {
      const page = await browser.newPage();
      try {
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('requestfailed', request => errors.push('Asset failed: ' + request.url()));
        page.on('response', response => {
          if (response.status() >= 400) errors.push('Asset HTTP ' + response.status() + ': ' + response.url());
        });
        await assets.attach(page, errors);
        await page.setViewport({width: entry.width, height: entry.height, deviceScaleFactor: 1});
        await page.goto(`${assets.origin}/${encodeURIComponent(entry.html)}`, {waitUntil: 'networkidle0', timeout: 60000});
        await page.waitForFunction(() => window.__ready === true, {timeout: 60000});
        await page.evaluate(async () => {
          await document.fonts.ready;
          await Promise.all(Array.from(document.images, img => img.decode()));
          document.getAnimations().forEach(animation => animation.pause());
        });
        if (errors.length) throw Error(errors.join('\n'));
        await page.screenshot({path: entry.output, type: 'png', clip: {x: 0, y: 0, width: entry.width, height: entry.height}});
        console.log('Rendered ' + entry.html);
      } finally {
        await page.close();
      }
    }
  } finally {
    if (browser) await browser.close();
    if (assets) await assets.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
