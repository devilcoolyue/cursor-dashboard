// V2 only: retire recognized backups after a fresh, consistent backup exists.
(() => {
  const folder = path.dirname(databasePath);
  const prefix = path.basename(databasePath) + '.cursor-panel-';
  try {
    const files = fs.readdirSync(folder).filter(name =>
      name.startsWith(prefix) && /^\d+-\d+\.bak$/.test(name.slice(prefix.length)))
      .map(name => {
        const file = path.join(folder, name);
        return {file, stat: fs.lstatSync(file)};
      }).filter(item => item.stat.isFile())
      .sort((a, b) => a.stat.mtimeMs - b.stat.mtimeMs || a.file.localeCompare(b.file));
    let count = files.length;
    let bytes = files.reduce((total, item) => total + item.stat.size, 0);
    for (const item of files) {
      if (count <= 20 && bytes <= 512 * 1024 * 1024) break;
      if (item.file === backup) continue;
      fs.unlinkSync(item.file);
      count -= 1;
      bytes -= item.stat.size;
    }
  } catch (_) {
    console.warn('旧备份清理未完成，请检查备份目录空间和权限。');
  }
})();
