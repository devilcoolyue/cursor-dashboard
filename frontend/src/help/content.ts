import content from './content.json'

export interface HelpSection {
  id: string
  title: string
  paragraphs?: string[]
  steps?: string[]
  table?: { headers: string[]; rows: string[][] }
  code?: string
  note?: string
}
export interface HelpArticle {
  id: string
  group: string
  title: string
  summary: string
  icon: string
  audience: string
  sections: HelpSection[]
}
export const helpArticles: HelpArticle[] = content
export const helpGroups = [...new Set(helpArticles.map(article => article.group))]
export function matchesHelp(article: HelpArticle, query: string) {
  const haystack = [article.title, article.summary, article.group, article.audience,
    ...article.sections.flatMap(section => [section.title, ...(section.paragraphs || []), ...(section.steps || []),
      ...(section.table?.headers || []), ...(section.table?.rows.flat() || []), section.code || '', section.note || '']),
  ].join(' ').toLocaleLowerCase()
  return query.trim().toLocaleLowerCase().split(/\s+/).every(word => haystack.includes(word))
}
