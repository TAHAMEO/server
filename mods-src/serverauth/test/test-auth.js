// End-to-end tests of the Server Auth mod against a running test server (see run.sh).
const mineflayer = require('mineflayer');
const fs = require('fs');
const path = require('path');

const SERVER_DIR = process.env.SERVER_DIR;
const FIFO = path.join(SERVER_DIR, 'console.fifo');
const PORT = Number(process.env.PORT || 25599);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;

function check(name, ok, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`);
  if (!ok) failures++;
}

function consoleCmd(cmd) {
  fs.appendFileSync(FIFO, cmd + '\n');
}

function serverLog() {
  return fs.readFileSync(path.join(SERVER_DIR, 'server.out'), 'utf8');
}

function join(username, extra = {}) {
  return new Promise((resolve, reject) => {
    const bot = mineflayer.createBot({ host: '127.0.0.1', port: PORT, username, version: '1.20.1', auth: 'offline', ...extra });
    bot.messages = [];
    bot.kickReason = null;
    bot.on('messagestr', (m) => bot.messages.push(m));
    bot.on('kicked', (reason) => { bot.kickReason = typeof reason === 'string' ? reason : JSON.stringify(reason); });
    // mineflayer misses kicks that arrive right after joining; read the raw packet too
    bot._client.on('kick_disconnect', (p) => { bot.kickReason = String(p.reason); });
    bot.on('error', (e) => { bot.lastError = e; });
    const timer = setTimeout(() => reject(new Error(`${username}: no spawn within 30s (kick: ${bot.kickReason})`)), 30000);
    bot.once('spawn', () => { clearTimeout(timer); resolve(bot); });
    bot.once('end', () => { clearTimeout(timer); bot.ended = true; resolve(bot); });
  });
}

async function waitFor(bot, pattern, ms = 8000) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    const hit = bot.messages.find((m) => pattern.test(m));
    if (hit) return hit;
    await sleep(100);
  }
  return null;
}

async function say(bot, text, pattern, ms) {
  bot.messages = [];
  bot.chat(text);
  return waitFor(bot, pattern, ms);
}

function quit(bot) {
  return new Promise((r) => { if (bot.ended) return r(); bot.once('end', r); bot.quit(); });
}

function waitEnd(bot, ms) {
  return new Promise((r) => { if (bot.ended) return r(true); const t = setTimeout(() => r(false), ms); bot.once('end', () => { clearTimeout(t); r(true); }); });
}

module.exports = { join, waitFor, say, quit, waitEnd, check, consoleCmd, serverLog, sleep, PORT, failures: () => failures };

if (require.main === module) (async () => {
  // 1. New player: locked down until /register.
  let alice = await join('Alice');
  check('new player is asked to register', !!(await waitFor(alice, /Choose a password: \/register/)));
  const held = alice.entity.position.clone();
  alice.setControlState('forward', true);
  alice.setControlState('jump', true);
  await sleep(2500);
  alice.clearControlStates();
  await sleep(1000);
  check('cannot walk away before logging in', alice.entity.position.distanceTo(held) < 1.0,
    `moved ${alice.entity.position.distanceTo(held).toFixed(2)} blocks`);
  await say(alice, 'hello from pending alice', /x^/, 1500);
  check('chat is blocked before login', !serverLog().includes('hello from pending alice'));
  check('other commands are blocked', !!(await say(alice, '/help', /Choose a password/)));
  check('short password rejected', !!(await say(alice, '/register abc abc', /at least 6 characters/)));
  check('mismatched passwords rejected', !!(await say(alice, '/register Secret123 Other123', /are different/)));
  check('common password rejected', !!(await say(alice, '/register password password', /too easy to guess/)));
  check('password containing the name rejected', !!(await say(alice, '/register xAlice99x xAlice99x', /put your name/)));
  check('register works', !!(await say(alice, '/register Secr3t-Pass! Secr3t-Pass!', /Password saved and you are logged in/)));
  await say(alice, 'hello from alice', /x^/, 1500);
  check('chat works after login', serverLog().includes('hello from alice'));

  // Give items and move Alice somewhere else, then log off.
  consoleCmd('give Alice minecraft:diamond 5');
  consoleCmd('spreadplayers 64 64 0 1 false Alice');
  await sleep(4000);
  const awayPos = alice.entity.position.clone();
  const diamonds = alice.inventory.items().filter((i) => i.name === 'diamond').reduce((n, i) => n + i.count, 0);
  check('test setup: Alice has 5 diamonds away from spawn', diamonds === 5 && awayPos.distanceTo(held) > 30,
    `diamonds=${diamonds} pos=${awayPos}`);
  await quit(alice);
  await sleep(1500);

  // 2. Returning player: inventory and location hidden until /login.
  alice = await join('Alice');
  check('returning player is asked to log in', !!(await waitFor(alice, /Log in with \/login/)));
  await sleep(1500);
  check('inventory hidden before login', alice.inventory.items().length === 0,
    `items=${alice.inventory.items().map((i) => i.name + 'x' + i.count)}`);
  check('location hidden before login (held at spawn)', alice.entity.position.distanceTo(awayPos) > 30,
    `pos=${alice.entity.position}`);
  check('wrong password counted', !!(await say(alice, '/login wrongpass', /Wrong password\. 2 tries left/)));
  check('/l alias logs in', !!(await say(alice, '/l Secr3t-Pass!', /Logged in\. Have fun!/)));
  await sleep(2000);
  const back = alice.inventory.items().filter((i) => i.name === 'diamond').reduce((n, i) => n + i.count, 0);
  check('inventory restored after login', back === 5, `diamonds=${back}`);
  check('location restored after login', alice.entity.position.distanceTo(awayPos) < 1.5, `pos=${alice.entity.position}`);
  check('change password', !!(await say(alice, '/changepassword Secr3t-Pass! N3w-Secret!', /Password changed/)));
  check('change password needs the right old one', !!(await say(alice, '/changepassword nope12 Another-1', /old password is not correct/)));
  await quit(alice);
  await sleep(1500);

  // 3. Names are case-exact.
  const lower = await join('alice');
  await waitEnd(lower, 5000);
  await sleep(500);
  check('different capitalisation of a registered name is refused',
    /registered as/.test(lower.kickReason || '') || /alice lost connection: This name is registered as "Alice"/.test(serverLog()));
  await sleep(1500);

  // 4. Pending players cannot be hurt.
  const carol = await join('Carol');
  await waitFor(carol, /Choose a password/);
  consoleCmd('damage Carol 5 minecraft:generic');
  await sleep(2000);
  check('cannot be damaged before login', carol.health === 20, `health=${carol.health}`);

  // 5. Timeout kick.
  const ended = await waitEnd(carol, 30000);
  check('kicked after the login timeout', ended && /did not log in in time/.test(carol.kickReason || ''), carol.kickReason);
  await sleep(1500);

  // 6. Admin reset.
  consoleCmd('auth unregister Alice');
  await sleep(1000);
  alice = await join('Alice');
  check('after /auth unregister the player must register again', !!(await waitFor(alice, /Choose a password/)));
  check('re-register works', !!(await say(alice, '/register Fresh-Pass9 Fresh-Pass9', /Password saved/)));
  await quit(alice);
  await sleep(1500);


  // 7. Password file: hashes only, private permissions.
  const file = path.join(SERVER_DIR, 'serverauth', 'accounts.json');
  const text = fs.readFileSync(file, 'utf8');
  const json = JSON.parse(text);
  const hash = json.accounts.alice.hash;
  check('passwords stored as PBKDF2-SHA512 hashes', /^pbkdf2_sha512\$210000\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$/.test(hash), hash);
  check('no plaintext passwords in the file', !/Secr3t|N3w-Secret|Fresh-Pass9/.test(text));
  check('accounts.json is private (600)', (fs.statSync(file).mode & 0o777) === 0o600, (fs.statSync(file).mode & 0o777).toString(8));
  check('serverauth/ folder is private (700)', (fs.statSync(path.dirname(file)).mode & 0o777) === 0o700);
  check('passwords never written to the log', !/Secr3t|N3w-Secret|Fresh-Pass9|wrongpass/.test(serverLog()));

  // 8. Brute force: 4 wrong passwords from one address (1 already used above) lock it out.
  let dave = await join('Alice');
  await waitFor(dave, /Log in with/);
  await say(dave, '/login guess-one', /Wrong password/);
  await say(dave, '/login guess-two', /Wrong password/);
  dave.messages = [];
  dave.chat('/login guess-three');
  await waitEnd(dave, 8000);
  check('address locked out after repeated wrong passwords', /Try again in 1 minute/.test(dave.kickReason || ''), dave.kickReason);
  await sleep(1500);
  dave = await join('Alice');
  await waitEnd(dave, 5000);
  check('locked-out address is refused on reconnect', /Too many wrong passwords from your address/.test(dave.kickReason || ''), dave.kickReason);

  const errors = serverLog().split('\n').filter((l) => /\/(ERROR|FATAL)\]/.test(l) && !/yggdrasil/i.test(l));
  check('no errors in the server log', errors.length === 0, errors.slice(0, 3).join(' | '));
  console.log(failures === 0 ? 'ALL TESTS PASSED' : `${failures} TEST(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.log('FAIL  test crashed:', e.message); process.exit(2); });
