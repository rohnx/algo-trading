const assert = require('node:assert/strict');
const { TODAY, COMPARISONS, SECTORS } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'layout/terminal-stroke-clearance',
    name: 'endpoint labels leave their own closing-price line visible near the session edge',
    async run({ page, emit, captureChart, neutralBreakout, comparisonSnapshot }) {
      const fixture = comparisonSnapshot(TODAY, 25), focit = fixture.instruments['FOCIT.BO'];
      for (const bars of [focit.candles, focit.candles_5m]) {
        for (const bar of bars.slice(-12)) bar.close = 30300;
      }
      focit.current_price = 30300;
      focit.breakout = neutralBreakout({ range_locked: true, range_high: 30100, range_low: 29900,
        direction: 'BULLISH', confirmed_at: focit.last_tick_time, price: focit.current_price });
      try {
        await emit(fixture); await page.mouse.move(0, 0);
        for (const [width, height] of [[1440, 900], [390, 844]]) {
          await page.setViewportSize({ width, height });
          for (const interval of [15, 5]) {
            await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
            const chart = await captureChart();
            const canvas = await page.locator('#chart').boundingBox();
            const label = await page.locator('#series-labels [data-symbol="FOCIT.BO"]').boundingBox();
            const tip = chart.series[4].flatMap(line => line.points).at(-1);
            assert.ok(label.x + label.width < canvas.x + tip[0], 'the closing-session label sits before its endpoint');
            const top = label.y - canvas.y - 2, bottom = top + label.height + 4;
            assert.ok(bottom < tip[1] - 1 || top > tip[1] + 1,
              `${width}px ${interval}m: the label background must not erase its flat terminal stroke`);
            assert.equal(chart.series[4].flatMap(line => line.commands).filter(command => command.method === 'moveTo').length, 1,
              'label placement does not manufacture missing-price gaps');
          }
        }
      } finally {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; });
        await emit(comparisonSnapshot());
      }
    }
  },
  {
    id: 'layout/sector-label-layout',
    name: 'all 15 sector endpoint labels remain readable on desktop and mobile in both intervals',
    async run({ page, emit, captureChart, selectGroup, comparisonSnapshot, sectorSnapshot, confirmSector }) {
      const fixture = sectorSnapshot(TODAY, 25);
      for (const [index, [symbol]] of SECTORS.entries()) confirmSector(fixture.instruments[symbol], index % 2 ? 'BEARISH' : 'BULLISH', .004 + index * .00002);
      try {
        await emit(fixture); await page.locator('#sectors-tab').click(); await page.mouse.move(0, 0);
        for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]]) {
          await page.setViewportSize({ width, height });
          for (const interval of [15, 5]) {
            await page.evaluate(interval => { setIntervalMinutes(interval); transition = null; }, interval);
            const chart = await captureChart(), box = await page.locator('#chart').boundingBox();
            const labels = await page.locator('#series-labels button.series-endpoint:visible').evaluateAll(items => items.map(item => {
              const box = item.getBoundingClientRect(), style = getComputedStyle(item);
              return { symbol: item.dataset.symbol, x: box.x, y: box.y, width: box.width, height: box.height, text: item.textContent,
                background: style.backgroundColor, scrollWidth: item.scrollWidth, clientWidth: item.clientWidth };
            }));
            assert.equal(labels.length, SECTORS.length, `${name} ${interval}m has 15 qualified labels`);
            for (const label of labels) {
              assert.ok(label.x >= box.x - .01 && label.x + label.width <= box.x + box.width + .01, `${label.symbol}: horizontal bounds`);
              assert.ok(label.y >= box.y - .01 && label.y + label.height <= box.y + box.height + .01, `${label.symbol}: vertical bounds`);
              assert.ok(label.scrollWidth <= label.clientWidth + 1, `${label.symbol}: full text fits`);
              assert.doesNotMatch(label.text, /NIFTY/i);
              assert.notEqual(label.background, 'rgba(0, 0, 0, 0)');
            }
            const ordered = labels.sort((a, b) => a.y - b.y);
            for (let index = 1; index < ordered.length; index++) assert.ok(ordered[index].y >= ordered[index - 1].y + ordered[index - 1].height - .01, 'dense sector labels have separate rows');
            assert.ok(chart.series.every(lines => lines.length > 0));
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1), 'tabs and chart fit without page overflow');
            await page.screenshot({ path: `/tmp/orb-sectors-${name}-${interval}m.png`, fullPage: true });
          }
        }
      } finally {
        await page.setViewportSize({ width: 1440, height: 900 });
        await selectGroup('derivatives');
        await page.evaluate(() => { setIntervalMinutes(15); transition = null; }); await emit(comparisonSnapshot());
      }
    }
  },
  {
    id: 'layout/theme-persistence',
    name: 'dark theme has readable text and survives reloading',
    async run({ page, emit, snapshot }) {
      await page.locator('#theme-toggle').click();
      const contrast = await page.evaluate(() => {
        const style = getComputedStyle(document.body);
        const channel = c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
        const luminance = color => {
          const parts = color.match(/\d+(?:\.\d+)?/g).slice(0, 3).map(c => channel(Number(c) / 255));
          return .2126 * parts[0] + .7152 * parts[1] + .0722 * parts[2];
        };
        const background = getComputedStyle(document.documentElement).backgroundColor;
        const values = [luminance(style.color), luminance(background)].sort((a, b) => b - a);
        return { ratio: (values[0] + .05) / (values[1] + .05), theme: document.documentElement.dataset.theme };
      });
      assert.equal(contrast.theme, 'dark');
      assert.ok(contrast.ratio >= 4.5, `body text contrast ${contrast.ratio.toFixed(1)}`);
      await page.reload();
      await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
      assert.equal(await page.locator('#theme-toggle').getAttribute('aria-pressed'), 'true');
      await emit(snapshot(TODAY, 25));
      await page.locator('#today').click();
    }
  },
  {
    id: 'layout/desktop-chart',
    name: 'desktop layout gives most of the screen to the chart without overflow',
    async run({ page, emit, captureChart, layoutSnapshot }) {
      const name = 'desktop', width = 1440, height = 900, ratio = 0.85;
      await emit(layoutSnapshot());
      await page.locator('#today').click(); await page.mouse.move(0, 0);
      await page.setViewportSize({ width, height }); await page.waitForTimeout(100);
      const layout = await page.evaluate(() => ({ height: document.querySelector('#chart').getBoundingClientRect().height,
        width: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight, viewport: innerWidth }));
      console.log(`LAYOUT ${name}: chart ${layout.height}px (${Math.round(layout.height / height * 100)}%), overflow ${layout.width - layout.viewport}px horizontal / ${layout.scrollHeight - height}px vertical`);
      assert.ok(layout.height / height > ratio, `chart occupies ${Math.round(layout.height / height * 100)}%`);
      assert.ok(layout.width <= layout.viewport, `horizontal overflow ${layout.width - layout.viewport}px`);
      assert.ok(layout.scrollHeight <= height + 1, `vertical overflow ${layout.scrollHeight - height}px`);
      for (const theme of ['light', 'dark']) {
        if (await page.evaluate(() => document.documentElement.dataset.theme) !== theme) {
          await page.locator('#theme-toggle').click();
          await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme);
        }
        for (const interval of ['15m', '5m']) {
          await page.locator(`#interval-${interval}`).click();
          await page.waitForTimeout(500);
          assert.ok((await captureChart()).series.every(lines => lines.length > 0), `${name} ${theme} ${interval} screenshot includes all comparisons`);
          await page.screenshot({ path: `/tmp/orb-${name}-${theme}-${interval}.png`, fullPage: true });
        }
      }
      await page.locator('#interval-15m').click();
      await page.waitForTimeout(500);

    }
  },
  {
    id: 'layout/desktop-endpoints',
    name: 'desktop endpoint labels remain readable within the chart without hover cards',
    async run({ page, emit, captureChart, revealComparisons, layoutSnapshot }) {
      const name = 'desktop', width = 1440, height = 900;
      await page.setViewportSize({ width, height });
      await page.locator('#theme-toggle').click();
      await emit(layoutSnapshot()); await captureChart();
      const chart = await page.locator('#chart').boundingBox();
      await revealComparisons();
      const labels = await page.locator('#series-labels .series-endpoint').evaluateAll(items => items.map(item => {
        const box = item.getBoundingClientRect();
        const style = getComputedStyle(item);
        return { symbol: item.dataset.symbol, primary: item.dataset.primary === 'true', x: box.x, y: box.y, width: box.width, height: box.height,
          label: item.getAttribute('aria-label'), color: style.color, background: style.backgroundColor, halo: style.boxShadow };
      }));
      assert.equal(labels.length, COMPARISONS.length + 1, 'primary and comparison labels are present');
      for (const label of labels) {
        assert.ok(label.x >= chart.x - .01 && label.x + label.width <= chart.x + chart.width + .01, `${label.symbol} fits the chart width`);
        assert.ok(label.y >= chart.y - .01 && label.y + label.height <= chart.y + chart.height + .01, `${label.symbol} fits the chart height`);
        assert.ok(label.label && label.label.includes('%'), `${label.symbol} has an accessible return`);
        assert.notEqual(label.background, 'rgba(0, 0, 0, 0)', `${label.symbol} masks chart strokes beneath its text`);
        assert.notEqual(label.halo, 'none', `${label.symbol} has clearance around its text`);
      }
      const primary = labels.find(label => label.primary);
      assert.ok(primary.x >= chart.x + chart.width - 76, 'NIFTY text stays entirely in the price-axis margin');
      const ordered = labels.filter(label => !label.primary).sort((a, b) => a.y - b.y);
      for (let i = 1; i < ordered.length; i++) assert.ok(ordered[i].y >= ordered[i - 1].y + ordered[i - 1].height - .01, 'collision handling leaves each label a separate row');
      assert.equal(await page.locator('#chart-inspector, #comparison-detail, #primary-return, #comparison-return').count(), 0);
      await page.screenshot({ path: `/tmp/orb-${name}-endpoints.png`, fullPage: true });
      await page.mouse.move(0, 0);
    }
  },
  {
    id: 'layout/mobile-chart',
    name: 'mobile layout gives most of the screen to the chart without overflow',
    async run({ page, emit, captureChart, layoutSnapshot }) {
      const name = 'mobile', width = 390, height = 844, ratio = 0.7;
      await emit(layoutSnapshot());
      await page.locator('#today').click(); await page.mouse.move(0, 0);
      await page.setViewportSize({ width, height }); await page.waitForTimeout(100);
      const layout = await page.evaluate(() => ({ height: document.querySelector('#chart').getBoundingClientRect().height,
        width: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight, viewport: innerWidth }));
      console.log(`LAYOUT ${name}: chart ${layout.height}px (${Math.round(layout.height / height * 100)}%), overflow ${layout.width - layout.viewport}px horizontal / ${layout.scrollHeight - height}px vertical`);
      assert.ok(layout.height / height > ratio, `chart occupies ${Math.round(layout.height / height * 100)}%`);
      assert.ok(layout.width <= layout.viewport, `horizontal overflow ${layout.width - layout.viewport}px`);
      assert.ok(layout.scrollHeight <= height + 1, `vertical overflow ${layout.scrollHeight - height}px`);
      for (const theme of ['light', 'dark']) {
        if (await page.evaluate(() => document.documentElement.dataset.theme) !== theme) {
          await page.locator('#theme-toggle').click();
          await page.waitForFunction(theme => document.documentElement.dataset.theme === theme, theme);
        }
        for (const interval of ['15m', '5m']) {
          await page.locator(`#interval-${interval}`).click();
          await page.waitForTimeout(500);
          assert.ok((await captureChart()).series.every(lines => lines.length > 0), `${name} ${theme} ${interval} screenshot includes all comparisons`);
          await page.screenshot({ path: `/tmp/orb-${name}-${theme}-${interval}.png`, fullPage: true });
        }
      }
      await page.locator('#interval-15m').click();
      await page.waitForTimeout(500);

    }
  },
  {
    id: 'layout/mobile-endpoints',
    name: 'mobile endpoint labels remain readable within the chart without hover cards',
    async run({ page, emit, captureChart, revealComparisons, layoutSnapshot }) {
      const name = 'mobile', width = 390, height = 844;
      await page.setViewportSize({ width, height });
      await page.locator('#theme-toggle').click();
      await emit(layoutSnapshot()); await captureChart();
      const chart = await page.locator('#chart').boundingBox();
      await revealComparisons();
      const labels = await page.locator('#series-labels .series-endpoint').evaluateAll(items => items.map(item => {
        const box = item.getBoundingClientRect();
        const style = getComputedStyle(item);
        return { symbol: item.dataset.symbol, primary: item.dataset.primary === 'true', x: box.x, y: box.y, width: box.width, height: box.height,
          label: item.getAttribute('aria-label'), color: style.color, background: style.backgroundColor, halo: style.boxShadow };
      }));
      assert.equal(labels.length, COMPARISONS.length + 1, 'primary and comparison labels are present');
      for (const label of labels) {
        assert.ok(label.x >= chart.x - .01 && label.x + label.width <= chart.x + chart.width + .01, `${label.symbol} fits the chart width`);
        assert.ok(label.y >= chart.y - .01 && label.y + label.height <= chart.y + chart.height + .01, `${label.symbol} fits the chart height`);
        assert.ok(label.label && label.label.includes('%'), `${label.symbol} has an accessible return`);
        assert.notEqual(label.background, 'rgba(0, 0, 0, 0)', `${label.symbol} masks chart strokes beneath its text`);
        assert.notEqual(label.halo, 'none', `${label.symbol} has clearance around its text`);
      }
      const primary = labels.find(label => label.primary);
      assert.ok(primary.x >= chart.x + chart.width - 76, 'NIFTY text stays entirely in the price-axis margin');
      const ordered = labels.filter(label => !label.primary).sort((a, b) => a.y - b.y);
      for (let i = 1; i < ordered.length; i++) assert.ok(ordered[i].y >= ordered[i - 1].y + ordered[i - 1].height - .01, 'collision handling leaves each label a separate row');
      assert.equal(await page.locator('#chart-inspector, #comparison-detail, #primary-return, #comparison-return').count(), 0);
      await page.screenshot({ path: `/tmp/orb-${name}-endpoints.png`, fullPage: true });
      await page.mouse.move(0, 0);
    }
  },
];
