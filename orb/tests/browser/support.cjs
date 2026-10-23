const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { TODAY, createFixture } = require('./fixtures.cjs');
const bundledPlaywright = path.join(require('node:os').homedir(), '.cache', 'codex-runtimes', 'codex-primary-runtime', 'dependencies', 'node', 'node_modules', 'playwright');
const playwright = require(process.env.PLAYWRIGHT_PATH || (fs.existsSync(bundledPlaywright) ? bundledPlaywright : 'playwright'));

function launchBrowser() {
  return playwright.chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', args: ['--no-sandbox'] });
}

async function openScenario(browser) {
  const fixture = createFixture();
  const { server, serverState } = fixture;
  const browserErrors = [];
  let context;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    // A new context owns storage, focus, media preferences, timers, stream and app globals.
    context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: 'America/New_York' });
    await context.addInitScript(() => {
      window.__now = Date.parse('2026-10-05T18:00:00+05:30');
      const NativeDate = Date;
      window.Date = class extends NativeDate {
        constructor(...args) { super(...(args.length ? args : [window.__now])); }
        static now() { return window.__now; }
      };
      window.EventSource = class {
        constructor() { window.__stream = this; setTimeout(() => this.onopen?.(), 0); }
        close() {}
      };
      window.__emit = message => window.__stream.onmessage({ data: JSON.stringify(message) });
      window.__fills = [];
      window.__chartDraws = 0;
      const clearRect = CanvasRenderingContext2D.prototype.clearRect;
      CanvasRenderingContext2D.prototype.clearRect = function (...args) {
        if (this.canvas.id === 'chart') window.__chartDraws += 1;
        return clearRect.apply(this, args);
      };
      const fillRect = CanvasRenderingContext2D.prototype.fillRect;
      CanvasRenderingContext2D.prototype.fillRect = function (x, y, w, h) {
        if (this.canvas.id === 'chart') window.__fills.push({ color: this.fillStyle, x, y, w, h, alpha: this.globalAlpha });
        return fillRect.call(this, x, y, w, h);
      };
      window.__strokes = []; window.__markers = [];
      window.__clips = [];
      let clipRectangle;
      const rect = CanvasRenderingContext2D.prototype.rect, clip = CanvasRenderingContext2D.prototype.clip;
      CanvasRenderingContext2D.prototype.rect = function (...args) {
        if (this.canvas.id === 'chart') clipRectangle = args;
        return rect.apply(this, args);
      };
      CanvasRenderingContext2D.prototype.clip = function (...args) {
        if (this.canvas.id === 'chart') window.__clips.push(clipRectangle);
        return clip.apply(this, args);
      };
      let points = [], commands = [];
      for (const method of ['beginPath', 'moveTo', 'lineTo', 'stroke', 'fill']) {
        const native = CanvasRenderingContext2D.prototype[method];
        CanvasRenderingContext2D.prototype[method] = function (...args) {
          if (this.canvas.id === 'chart') {
            if (method === 'beginPath') { points = []; commands = []; }
            if (method === 'moveTo' || method === 'lineTo') { points.push(args); commands.push({ method, point: args }); }
            if (method === 'stroke') window.__strokes.push({ points: [...points], commands: [...commands], color: this.strokeStyle, dash: this.getLineDash(), alpha: this.globalAlpha, width: this.lineWidth, filter: this.filter });
            if (method === 'fill') window.__markers.push({ points: [...points], color: this.fillStyle, alpha: this.globalAlpha });
          }
          return native.apply(this, args);
        };
      }
    });
    const page = await context.newPage();
    page.on('pageerror', error => browserErrors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && !/404|409|503/.test(message.text())) browserErrors.push(message.text()); });

    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(date => view?.date === date && !document.querySelector('#previous').disabled, TODAY);
    const emit = data => page.evaluate(data => {
      window.__emit(data);
      return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, data);
    const captureChart = options => page.evaluate(options => {
      window.__captureChart = ({ animate = false, now } = {}) => {
        const draw = () => drawChart(now);
        if (!animate) {
          draw(); chartMotion.finish(); draw(); chartMotion.finish();
        }
        window.__fills = []; window.__strokes = []; window.__markers = []; window.__clips = []; draw();
        const css = getComputedStyle(document.documentElement);
        const context = document.querySelector('#chart').getContext('2d');
        const color = name => {
          context.save(); context.strokeStyle = css.getPropertyValue(name).trim();
          const result = context.strokeStyle; context.restore(); return result;
        };
        const count = selectedComparisonGroup === 'sectors' ? 15 : selectedComparisonGroup === 'derivatives' ? 5 : 0;
        const comparisonColors = Array.from({ length: count }, (_, index) => `--comparison-line${index ? `-${index + 1}` : ''}`).map(color);
        const price = color('--price-line');
        const bodyColors = [color('--up'), color('--down')];
        const series = comparisonColors.map(shade => window.__strokes.filter(line => line.color === shade && line.width >= 1.1 && line.points.length > 0));
        return {
          series,
          comparison: series[0],
          allComparisons: series.flat(),
          clips: window.__clips,
          price: window.__strokes.find(line => line.color === price && line.dash.join(',') === '2,4'),
          bodies: window.__fills.filter(fill => bodyColors.includes(fill.color) && fill.w <= 26),
          width: document.querySelector('#chart').clientWidth,
          height: document.querySelector('#chart').clientHeight
        };
      };
      return window.__captureChart(options);
    }, options);
    const selectGroup = async group => {
      const button = page.locator(group === 'sectors' ? '#sectors-tab' : '#comparison-toggle');
      if (await button.getAttribute('aria-selected') !== 'true') await button.click();
      await captureChart();
    };
    const revealComparisons = async () => {
      await page.locator('#series-labels [data-primary="true"]').hover();
      await page.waitForFunction(() => document.querySelector('#series-labels').dataset.reveal === 'true');
    };
    const waitDate = date => page.waitForFunction(date => document.querySelector('#session-date').value === date && document.querySelector('#chart').getAttribute('aria-label').includes(date), date);
    async function holdSourceRequest(action) {
      serverState.holdSource = true;
      const started = new Promise(resolve => { serverState.sourceStarted = resolve; });
      const request = page.waitForRequest('**/api/source');
      await Promise.all([action(), request, started]);
    }
    function releaseSourceRequest(status = 200) {
      serverState.holdSource = false;
      if (!serverState.heldSource) return;
      const { response, data } = serverState.heldSource;
      serverState.heldSource = null;
      response.writeHead(status, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(status === 200 ? data : { message: 'Fixture acknowledgement unavailable.' }));
    }

    return {
      ...fixture, page, emit, captureChart, selectGroup, revealComparisons, waitDate,
      holdSourceRequest, releaseSourceRequest,
      assertNoBrowserErrors() { assert.deepEqual(browserErrors, []); },
      async close() {
        releaseSourceRequest();
        try { await context.close(); } finally { await fixture.close(); }
      }
    };
  } catch (error) {
    try { await context?.close(); } finally { await fixture.close(); }
    throw error;
  }
}

module.exports = { launchBrowser, openScenario };
