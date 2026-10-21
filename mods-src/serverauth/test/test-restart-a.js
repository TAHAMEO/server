// Part A: a player is mid-login (items hidden) when the server stops.
const t = require('./test-auth.js');
(async () => {
  let frank = await t.join('Frank');
  await t.waitFor(frank, /Choose a password/);
  t.check('Frank registers', !!(await t.say(frank, '/register Fr4nk-ly-secret Fr4nk-ly-secret', /Password saved/)));
  t.consoleCmd('give Frank minecraft:gold_ingot 7');
  t.consoleCmd('spreadplayers 200 -150 0 1 false Frank');
  await t.sleep(4000);
  const pos = frank.entity.position;
  require('fs').writeFileSync(require('path').join(process.env.SERVER_DIR, 'frank-pos.json'), JSON.stringify({ x: pos.x, y: pos.y, z: pos.z }));
  await t.quit(frank);
  await t.sleep(1500);
  frank = await t.join('Frank');
  await t.waitFor(frank, /Log in with/);
  await t.sleep(1000);
  t.check('items hidden while logging in', frank.inventory.items().length === 0);
  t.consoleCmd('stop');   // server stops while Frank is still logging in
  const ended = await t.waitEnd(frank, 60000);
  t.check('server stopped with Frank still logging in', ended);
  process.exit(t.failures() ? 1 : 0);
})().catch((e) => { console.log('FAIL  crashed:', e.message); process.exit(2); });
