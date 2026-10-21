// A player who logs off dead, rejoins and presses respawn stays locked until /login.
const mineflayer = require('mineflayer');
const t = require('./test-auth.js');
function joinDead(name) {
  return new Promise((resolve) => {
    const bot = mineflayer.createBot({ host: '127.0.0.1', port: t.PORT, username: name, version: '1.20.1', auth: 'offline', respawn: false });
    bot.messages = [];
    bot.on('messagestr', (m) => bot.messages.push(m));
    bot.once('death', () => resolve(bot));
    bot.once('spawn', () => resolve(bot));
  });
}
(async () => {
  let erin = await t.join('Erin2');
  await t.waitFor(erin, /Choose a password/);
  t.check('Erin2 registers', !!(await t.say(erin, '/register Pass-77-word Pass-77-word', /Password saved/)));
  t.consoleCmd('kill Erin2');
  await t.sleep(2000);
  await t.quit(erin);
  await t.sleep(1500);
  erin = await joinDead('Erin2');
  t.check('rejoins dead and is asked to log in', !!(await t.waitFor(erin, /Log in with/)));
  const spawned = new Promise((r) => erin.once('spawn', r));
  erin.respawn();
  await Promise.race([spawned, t.sleep(5000)]);
  await t.sleep(1500);
  const p0 = erin.entity.position.clone();
  erin.setControlState('forward', true);
  await t.sleep(2000);
  erin.clearControlStates();
  await t.sleep(1000);
  t.check('after respawning the player is still held until login', erin.entity.position.distanceTo(p0) < 1.0,
    `moved ${erin.entity.position.distanceTo(p0).toFixed(2)}`);
  t.check('login after respawn works', !!(await t.say(erin, '/login Pass-77-word', /Logged in/)));
  await t.sleep(1000);
  const p1 = erin.entity.position.clone();
  erin.setControlState('forward', true);
  await t.sleep(1500);
  erin.clearControlStates();
  t.check('can move after logging in', erin.entity.position.distanceTo(p1) > 1.0, `moved ${erin.entity.position.distanceTo(p1).toFixed(2)}`);
  await t.quit(erin);
  process.exit(t.failures() ? 1 : 0);
})().catch((e) => { console.log('FAIL  crashed:', e.message); process.exit(2); });
