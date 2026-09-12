import assert from 'node:assert/strict'
import { readFile, writeFile } from 'node:fs/promises'

const articles = JSON.parse(await readFile(new URL('../src/help/content.json', import.meta.url), 'utf8'))
const output = new URL('../../docs/user-guide.md', import.meta.url)
const escape = value => value.replaceAll('|', '\\|').replaceAll('\n', '<br>')
assert.equal(new Set(articles.map(article => article.id)).size, articles.length, 'Article IDs must be unique')
const lines = ['# Cursor Panel 使用指南', '',
  '<!-- Generated from frontend/src/help/content.json. Run npm --prefix frontend run docs:generate. -->', '',
  '客户端侧栏「帮助与文档」可重复打开 6 步新手指引或搜索本指南。内置文档无需登录，可离线阅读。', '',
  '新手路径：认识客户端与服务端 → 完成客户端设置 → 添加账号与查看额度 → 切换本机 Cursor → 连接 Linux 实例（可选）→ 设置空间与权限（可选）。', '',
  '指引只保存阅读进度，不会自动执行账号、权限或切换操作。首次打开客户端时自动展示，完成或跳过后不再自动弹出，可随时从帮助入口重看。', '', '## 模块目录', '',
  ...articles.map(article => `- [${article.group} · ${article.title}](#${article.id})`), '',
]
for (const article of articles) {
  assert.match(article.id, /^[a-z][a-z-]+$/)
  assert.equal(new Set(article.sections.map(section => section.id)).size, article.sections.length)
  lines.push(`<a id="${article.id}"></a>`, '', `## ${article.title}`, '', `适用：${article.audience}。`, '', article.summary, '')
  for (const section of article.sections) {
    lines.push(`### ${section.title}`, '')
    for (const paragraph of section.paragraphs || []) lines.push(paragraph, '')
    if (section.steps) lines.push(...section.steps.map((step, index) => `${index + 1}. ${step}`), '')
    if (section.table) {
      const { headers, rows } = section.table
      assert.ok(rows.every(row => row.length === headers.length), `Invalid table in ${article.id}/${section.id}`)
      lines.push(`| ${headers.map(escape).join(' | ')} |`, `| ${headers.map(() => '---').join(' | ')} |`,
        ...rows.map(row => `| ${row.map(escape).join(' | ')} |`), '')
    }
    if (section.code) lines.push('```bash', section.code, '```', '')
    if (section.note) lines.push(`> ${section.note}`, '')
  }
}
const result = lines.join('\n')
if (process.argv.includes('--check')) {
  assert.equal((await readFile(output, 'utf8')).replaceAll('\r\n', '\n'), result, 'Run npm --prefix frontend run docs:generate to update the handbook')
  console.log(`Verified ${articles.length} handbook modules against in-app content.`)
} else {
  await writeFile(output, result)
  console.log(`Generated docs/user-guide.md from ${articles.length} in-app modules.`)
}
