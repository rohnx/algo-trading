// Run from the repository root: node orb/tests/test_ui.cjs
// Requires Playwright and Chrome; PLAYWRIGHT_PATH and CHROME_PATH override discovery.
// --list prints stable scenario IDs. --case ID runs exactly one scenario.
// --reverse checks order independence; every run uses a fresh context and fixture server.
const { launchBrowser, openScenario } = require('./browser/support.cjs');
const scenarios = [
  ...require('./browser/sessions.cjs'),
  ...require('./browser/chart.cjs'),
  ...require('./browser/sources.cjs'),
  ...require('./browser/motion.cjs'),
  ...require('./browser/comparison_returns.cjs'),
  ...require('./browser/comparison_quotes.cjs'),
  ...require('./browser/comparison_emphasis.cjs'),
  ...require('./browser/replay.cjs'),
  ...require('./browser/comparison_groups.cjs'),
  ...require('./browser/layout.cjs'),
];

function selectScenarios(args) {
  let chosen = scenarios;
  const unknown = args.filter((arg, index) => !['--list', '--reverse', '--case'].includes(arg) && args[index - 1] !== '--case');
  if (unknown.length) throw new Error(`Unknown argument: ${unknown[0]}`);
  if (args.includes('--case')) {
    const id = args[args.indexOf('--case') + 1];
    chosen = scenarios.filter(scenario => scenario.id === id);
    if (chosen.length !== 1) throw new Error(`Unknown scenario: ${id}. Use --list to see available IDs.`);
  }
  return args.includes('--reverse') ? [...chosen].reverse() : chosen;
}

async function main() {
  const chosen = selectScenarios(process.argv.slice(2));
  if (process.argv.includes('--list')) {
    for (const scenario of chosen) console.log(`${scenario.id} — ${scenario.name}`);
    return;
  }
  const browser = await launchBrowser();
  const failures = [];
  try {
    for (const scenario of chosen) {
      let fixture;
      try {
        fixture = await openScenario(browser);
        await scenario.run(fixture);
        fixture.assertNoBrowserErrors();
        console.log(`PASS ${scenario.id}: ${scenario.name}`);
      } catch (error) {
        failures.push(`${scenario.id}: ${error.message.split('\n')[0]}`);
        console.error(`FAIL ${scenario.id}: ${error.stack || error}`);
      } finally { await fixture?.close(); }
    }
  } finally { await browser.close(); }
  console.log(`\n${chosen.length - failures.length}/${chosen.length} browser scenarios passed (each also checks browser errors).`);
  if (failures.length) {
    console.error(failures.join('\n'));
    process.exitCode = 1;
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
