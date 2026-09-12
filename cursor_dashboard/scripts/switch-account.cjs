const fs = require('fs');
const path = require('path');
const session = __SESSION_JSON__;
const [appRoot, databasePath] = process.argv.slice(2);

async function switchAccount() {
  if (session.preview) throw new Error('预览命令不能切换账号。');
  if (Date.now() >= session.expiresAt * 1000) throw new Error('登录凭证已过期，请回到面板重新生成命令。');
  const claims = JSON.parse(Buffer.from(session.token.split('.')[1], 'base64url').toString('utf8'));
  if (claims.type !== 'session' || !session.refreshToken) {
    throw new Error('缺少已验证的桌面登录凭证，请回到面板重新生成命令。');
  }
  if (!fs.existsSync(databasePath)) throw new Error('未找到用户数据库，请先打开一次 Cursor。');
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
    if (!check || Object.values(check)[0] !== 'ok') throw new Error('数据库完整性检查失败，已停止切换。');
    await get('SELECT key, value FROM ItemTable LIMIT 1');
    const backup = databasePath + '.cursor-panel-' + Date.now() + '-' + process.pid + '.bak';
    fs.writeFileSync(backup, '', { flag: 'wx', mode: 0o600 });
    // VACUUM INTO includes committed WAL data, unlike copying only state.vscdb.
    await run('VACUUM INTO ?', [backup]);
    console.log('备份位置：' + backup);
    // __BACKUP_RETENTION__
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
    console.log('本地账号已更新：' + session.email);
  } finally {
    await new Promise((resolve, reject) => db.close(error => error ? reject(error) : resolve()));
  }
}

switchAccount().catch(error => {
  console.error('切换失败：' + error.message);
  process.exitCode = 1;
});
