// Part B (after a restart): the hidden items and location come back at login.
const t = require('./test-auth.js');
(async () => {
  const want = JSON.parse(require('fs').readFileSync(require('path').join(process.env.SERVER_DIR, 'frank-pos.json'), 'utf8'));
  const frank = await t.join('Frank');
  t.check('accounts survive a restart', !!(await t.waitFor(frank, /Log in with/)));
  t.check('Frank logs in', !!(await t.say(frank, '/login Fr4nk-ly-secret', /Logged in/)));
  await t.sleep(2000);
  const gold = frank.inventory.items().filter((i) => i.name === 'gold_ingot').reduce((n, i) => n + i.count, 0);
  t.check('items hidden during the shutdown are restored', gold === 7, `gold=${gold}`);
  const p = frank.entity.position;
  const d = Math.hypot(p.x - want.x, p.y - want.y, p.z - want.z);
  t.check('location restored after the restart', d < 1.5, `pos=${p} want=${JSON.stringify(want)}`);
  await t.quit(frank);
  console.log(t.failures() ? 'RESTART TEST FAILED' : 'RESTART TEST PASSED');
  process.exit(t.failures() ? 1 : 0);
})().catch((e) => { console.log('FAIL  crashed:', e.message); process.exit(2); });
