const assert = require('node:assert/strict');
const { TODAY } = require('./fixtures.cjs');

module.exports = [
  {
    id: 'sources/selection-and-rejection',
    name: 'source dropdown posts its choice and rejects late packets from the old source',
    async run({ page, emit, serverState, snapshot }) {
      await page.locator('#source-select').selectOption('shoonya');
      await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'shoonya' && document.querySelector('#chart').getAttribute('aria-label').includes('6 candles'));
      assert.deepEqual(serverState.sourceRequests.at(-1).payload, { source: 'shoonya' });
      assert.match(serverState.sourceRequests.at(-1).contentType, /application\/json/);
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /6 candles/);
      await emit(snapshot(TODAY, 25, { source_id: 'yahoo', source_generation: serverState.selectedGeneration - 1, source: 'Late Yahoo snapshot' }));
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /6 candles/);
      assert.equal(await page.locator('#source-select').inputValue(), 'shoonya');
      serverState.rejectSource = true;
      await page.locator('#source-select').selectOption('yahoo');
      await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'shoonya');
      assert.equal(await page.locator('#source-select').inputValue(), 'shoonya');
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /6 candles/);
      serverState.rejectSource = false;
      await page.locator('#source-select').selectOption('yahoo');
      await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'yahoo' && document.querySelector('#chart').getAttribute('aria-label').includes('8 candles'));
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /8 candles/);
    }
  },
  {
    id: 'sources/stream-before-acknowledgement',
    name: 'target stream data paints before a source acknowledgement and cannot be rolled back by it',
    async run({ page, emit, holdSourceRequest, releaseSourceRequest, snapshot }) {
      try {
        await holdSourceRequest(() => page.locator('#source-select').selectOption('shoonya'));
        await emit(snapshot(TODAY, 0, { feed_status: 'connecting' }));
        assert.match(await page.locator('#empty-state').textContent(), /Connecting to Shoonya.*Loading candle history/);
        await emit(snapshot(TODAY, 9, { current_price: 25111 }));
        assert.equal(await page.locator('#source-select').isDisabled(), true, 'the HTTP request remains pending');
        assert.equal(await page.locator('#empty-state').isVisible(), false, 'ready stream data need not wait for the acknowledgement');
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /9 candles/);
        assert.equal(await page.locator('#price').textContent(), '25111.00');
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /9 candles/);
        assert.equal(await page.locator('#price').textContent(), '25111.00', 'the older six-candle POST snapshot cannot overwrite stream quotes');
      } finally {
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        await page.locator('#source-select').selectOption('yahoo');
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'yahoo');
      }
    }
  },
  {
    id: 'sources/failed-acknowledgement',
    name: 'a failed acknowledgement preserves a stream-confirmed Shoonya source and its login error',
    async run({ page, emit, holdSourceRequest, releaseSourceRequest, snapshot }) {
      try {
        await holdSourceRequest(() => page.locator('#source-select').selectOption('shoonya'));
        await emit(snapshot(TODAY, 7, { current_price: 25112, feed_status: 'error', feed_error: 'Shoonya login failed; refresh SHOONYA_AUTH_CODE and check credentials/IP' }));
        releaseSourceRequest(409);
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        assert.equal(await page.locator('#source-select').inputValue(), 'shoonya');
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /7 candles/);
        assert.equal(await page.locator('#price').textContent(), '25112.00');
        assert.match(await page.locator('#feed-status').textContent(), /Shoonya login failed/);
        assert.equal(await page.locator('#shoonya-help').isVisible(), true);
      } finally {
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
      }
    }
  },
  {
    id: 'sources/recovery-guidance',
    name: 'Shoonya recovery guidance loads authorization and public IP only on request',
    async run({ page, emit, serverState, snapshot }) {
      serverState.selectedSource = 'shoonya'; serverState.selectedGeneration = 1;
      await emit(snapshot(TODAY, 7, { feed_status: 'error', feed_error: 'Missing Shoonya credentials: SHOONYA_AUTH_CODE' }));
      assert.equal(await page.locator('#shoonya-help').isVisible(), true);
      assert.equal(serverState.helpRequests, 0, 'hidden or collapsed help does not request authorization metadata');
      assert.equal(serverState.ipRequests, 0, 'selecting a provider never looks up a public IP');
      await page.locator('#shoonya-help summary').click();
      await page.waitForFunction(() => !document.querySelector('#shoonya-login').hidden);
      assert.equal(await page.locator('#shoonya-login').getAttribute('href'), serverState.loginUrl);
      assert.equal(await page.locator('#shoonya-client-id').textContent(), 'Client ID: fixture-client');
      assert.equal(serverState.ipRequests, 0, 'opening guidance still does not look up a public IP');
      await page.locator('#check-ip').click();
      await page.waitForFunction(() => document.querySelector('#public-ip').textContent.includes('203.0.113.8'));
      assert.equal(serverState.ipRequests, 1);
      assert.match(await page.locator('#public-ip').textContent(), /confirm it matches Shoonya/);
      serverState.ipFailure = true;
      await page.locator('#check-ip').click();
      await page.waitForFunction(() => document.querySelector('#public-ip').textContent.includes('curl -s ifconfig.me'));
      assert.equal(await page.locator('#check-ip').isDisabled(), false);
      serverState.ipFailure = false;
      for (const value of [null, 'https://example.com/OAuthlogin/authorize/oauth?client_id=fixture-client', 'https://api.shoonya.com/other?client_id=fixture-client']) {
        serverState.loginUrl = value;
        await page.locator('#shoonya-help summary').click();
        const loaded = page.waitForResponse('**/api/shoonya/help');
        await page.locator('#shoonya-help summary').click();
        await loaded;
        await page.waitForFunction(() => document.querySelector('#shoonya-login-note').textContent.includes('SHOONYA_CLIENT_ID'));
        assert.equal(await page.locator('#shoonya-login').isVisible(), false);
        assert.equal(await page.locator('#shoonya-login').getAttribute('href'), null, 'missing or untrusted authorization URLs are never linked');
        assert.equal(await page.locator('#shoonya-client-id').isVisible(), false);
        assert.equal(await page.locator('#shoonya-client-id').textContent(), '');
      }
      serverState.loginUrl = 'https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=fixture-client';
    }
  },
  {
    id: 'sources/retry',
    name: 'retry waits for a fresh Shoonya session and keeps ready stream data ahead of its acknowledgement',
    async run({ page, emit, holdSourceRequest, releaseSourceRequest, serverState, snapshot }) {
      serverState.selectedSource = 'shoonya'; serverState.selectedGeneration = 1;
      await emit(snapshot(TODAY, 7, { feed_status: 'error', feed_error: 'Shoonya login failed' }));
      await page.locator('#shoonya-help summary').click();
      const oldStatus = snapshot(TODAY, 7, { type: 'status', feed_status: 'error', feed_error: 'Shoonya login failed' });
      try {
        await holdSourceRequest(() => page.locator('#source-retry').click());
        await emit(oldStatus);
        assert.equal(await page.locator('#source-select').isDisabled(), true);
        assert.equal(await page.locator('#empty-state').isVisible(), true, 'an old same-source status cannot confirm a retry');
        await emit(snapshot(TODAY, 10, { type: 'session', current_price: 25113, feed_status: 'live' }));
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /10 candles/);
        assert.equal(await page.locator('#source-retry').isDisabled(), true);
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        assert.match(await page.locator('#chart').getAttribute('aria-label'), /10 candles/);
        assert.equal(await page.locator('#price').textContent(), '25113.00');
        assert.equal(await page.locator('#shoonya-help').isVisible(), false, 'healthy provider state hides recovery guidance');
      } finally {
        releaseSourceRequest();
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled);
        await page.locator('#source-select').selectOption('yahoo');
        await page.waitForFunction(() => !document.querySelector('#source-select').disabled && document.querySelector('#source-select').value === 'yahoo');
      }
    }
  },
  {
    id: 'sources/epoch-restart',
    name: 'server restart accepts a fresh epoch even when its source generation restarts at zero',
    async run({ page, emit, serverState, snapshot }) {
      for (const source of ['shoonya', 'yahoo']) {
        await page.locator('#source-select').selectOption(source);
        await page.waitForFunction(source => !document.querySelector('#source-select').disabled
          && document.querySelector('#source-select').value === source, source);
      }
      assert.ok(serverState.selectedGeneration > 0, 'fixture switched sources before restarting');
      serverState.sourceEpoch = 'fixture-process-b';
      serverState.selectedGeneration = 0;
      await emit(snapshot(TODAY, 7, { current_price: 26002 }));
      assert.match(await page.locator('#chart').getAttribute('aria-label'), /7 candles/);
      assert.equal(await page.locator('#price').textContent(), '26002.00');
      assert.equal(await page.locator('#source-select').inputValue(), 'yahoo');
    }
  },
  {
    id: 'sources/recovery-layout',
    name: 'expanded Shoonya guidance fits desktop and mobile without using real credentials or IP',
    async run({ page, emit, serverState, snapshot }) {
      serverState.selectedSource = 'shoonya'; serverState.selectedGeneration += 1;
      serverState.loginUrl = 'https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=fixture-client';
      serverState.ipFailure = false;
      await emit(snapshot(TODAY, 25, { feed_status: 'error', feed_error: 'Shoonya login failed; refresh SHOONYA_AUTH_CODE and check credentials/IP' }));
      for (const [name, width, height] of [['desktop', 1440, 900], ['mobile', 390, 844]]) {
        await page.setViewportSize({ width, height });
        if (!await page.locator('#shoonya-help').evaluate(help => help.open)) await page.locator('#shoonya-help summary').click();
        await page.waitForFunction(() => !document.querySelector('#shoonya-login').hidden);
        await page.locator('#check-ip').click();
        await page.waitForFunction(() => document.querySelector('#public-ip').textContent.includes('203.0.113.8') && !document.querySelector('#check-ip').disabled);
        const bounds = await page.locator('#shoonya-help .feed-help-panel').boundingBox();
        assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1, `${name} help fits the viewport width`);
        assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= height + 1, `${name} help fits the viewport height`);
        assert.equal(await page.locator('#shoonya-client-id').textContent(), 'Client ID: fixture-client');
        await page.screenshot({ path: `/tmp/orb-source-help-${name}.png`, fullPage: true });
        if (name === 'desktop') await page.screenshot({ path: '/tmp/orb-source-help.png', fullPage: true });
      }
    }
  },
];
