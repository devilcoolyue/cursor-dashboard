const fs = require('fs');
const path = require('path');
const session = __SESSION_JSON__;
const [appRoot, databasePath] = process.argv.slice(2);

async function switchAccount() {
  if (session.preview) throw new Error('Preview commands cannot switch accounts.');
  if (Date.now() >= session.expiresAt * 1000) throw new Error('Token expired. Generate a new command.');
  const claims = JSON.parse(Buffer.from(session.token.split('.')[1], 'base64url').toString('utf8'));
  if (claims.type !== 'session' || !session.refreshToken) {
    throw new Error('A verified desktop session is required. Generate a new command.');
  }
  if (!fs.existsSync(databasePath)) throw new Error('Cursor database not found. Open Cursor once first.');
  const sqlite = require(path.join(appRoot, 'node_modules', '@vscode', 'sqlite3'));
  const db = await new Promise((resolve, reject) => {
    const opened = new sqlite.Database(databasePath, sqlite.OPEN_READWRITE,
      error => error ? reject(error) : resolve(opened));
  });
  db.configure('busyTimeout', 5000);
  const run = (sql, values = []) => new Promise((resolve, reject) =>
    db.run(sql, values, error => error ? reject(error) : resolve()));
  const get = (sql) => new Promise((resolve, reject) =>
    db.get(sql, (error, row) => error ? reject(error) : resolve(row)));
  try {
    const check = await get('PRAGMA quick_check');
    if (!check || Object.values(check)[0] !== 'ok') throw new Error('Database integrity check failed.');
    await get('SELECT key, value FROM ItemTable LIMIT 1');
    const backup = databasePath + '.cursor-panel-' + Date.now() + '-' + process.pid + '.bak';
    fs.writeFileSync(backup, '', { flag: 'wx', mode: 0o600 });
    // VACUUM INTO includes committed WAL data, unlike copying only state.vscdb.
    await run('VACUUM INTO ?', [backup]);
    console.log('Backup: ' + backup);
    await run('BEGIN IMMEDIATE');
    try {
      for (const [key, value] of [
        ['cursorAuth/accessToken', session.token],
        ['cursorAuth/refreshToken', session.refreshToken],
        ['cursorAuth/cachedEmail', session.email],
        ['cursorAuth/cachedSignUpType', 'Auth_0'],
        ['cursorAuth/stripeMembershipAuthId', session.subject],
      ]) {
        await run('INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)', [key, value]);
      }
      for (const key of ['stripeMembershipType', 'stripeSubscriptionStatus', 'stripeCustomerId',
        'cachedScopedProfile', 'cachedTeam', 'teamId']) {
        await run('DELETE FROM ItemTable WHERE key = ?', ['cursorAuth/' + key]);
      }
      await run('COMMIT');
    } catch (error) {
      await run('ROLLBACK').catch(() => {});
      throw error;
    }
    console.log('Local account updated: ' + session.email);
  } finally {
    await new Promise((resolve, reject) => db.close(error => error ? reject(error) : resolve()));
  }
}

switchAccount().catch(error => {
  console.error('Switch failed: ' + error.message);
  process.exitCode = 1;
});
