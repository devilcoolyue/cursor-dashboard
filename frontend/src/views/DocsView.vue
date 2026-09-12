<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { helpArticles, helpGroups, matchesHelp } from '../help/content'
import { openGuide } from '../help/onboarding'
import { me } from '../state'
import UiIcon from '../components/UiIcon.vue'
const route = useRoute(), query = ref(''), menuOpen = ref(false), articleHeading = ref<HTMLElement>()
const copyStatus = ref('')
const article = computed(() => helpArticles.find(item => item.id === (route.params.article || 'overview')))
const filtered = computed(() => helpArticles.filter(item => matchesHelp(item, query.value)))
const groups = computed(() => helpGroups.map(name => ({ name, items: filtered.value.filter(item => item.group === name) })).filter(group => group.items.length))
const index = computed(() => helpArticles.findIndex(item => item === article.value))
const previous = computed(() => index.value > 0 ? helpArticles[index.value - 1] : undefined)
const next = computed(() => index.value >= 0 ? helpArticles[index.value + 1] : undefined)
watch(() => route.params.article, async () => {
  menuOpen.value = false; copyStatus.value = ''
  await nextTick(); articleHeading.value?.focus({ preventScroll: true }); window.scrollTo({ top: 0 })
})
async function copy(code: string) {
  const id = article.value?.id
  try { await navigator.clipboard.writeText(code); if (article.value?.id === id) copyStatus.value = '命令已复制，请替换示例路径和域名后使用。' }
  catch { if (article.value?.id === id) copyStatus.value = '无法自动复制，请在命令区手动选择并复制。' }
}
function jump(id: string) {
  const target = document.getElementById(`doc-${id}`)
  target?.scrollIntoView({ block: 'start', behavior: 'auto' }); target?.focus({ preventScroll: true })
}
</script>
<template>
  <component :is="me ? 'section' : 'main'" class="docs-page" :class="{ 'docs-standalone': !me }">
    <header class="topbar help-toolbar"><div class="workspace-title"><UiIcon name="book" :size="18" /><h1>使用文档</h1><span class="settings-context">客户端 · Linux 服务 · 权限</span></div><div class="actions"><RouterLink v-if="!me" class="button" to="/login">返回登录</RouterLink><button @click="openGuide"><UiIcon name="compass" :size="14" />新手指引</button></div></header>
    <div class="docs-layout">
      <aside class="docs-directory" aria-label="文档目录">
        <label class="docs-search"><UiIcon name="search" :size="15" /><input v-model="query" type="search" aria-label="搜索使用文档" placeholder="搜索功能、权限、部署…" @input="menuOpen = true" @keydown.esc="query = ''" /></label>
        <button class="docs-mobile-toggle" :aria-expanded="menuOpen" aria-controls="docs-navigation" @click="menuOpen = !menuOpen">{{ query.trim() ? `找到 ${filtered.length} 篇文档` : '浏览模块目录' }}<UiIcon name="chevronDown" :size="14" /></button>
        <p v-if="query.trim()" class="docs-search-count" role="status">找到 {{ filtered.length }} 篇文档</p>
        <nav id="docs-navigation" class="docs-navigation" :class="{ 'mobile-open': menuOpen }" aria-label="文档模块">
          <div v-for="group in groups" :key="group.name" class="docs-nav-group"><h2>{{ group.name }}</h2><RouterLink v-for="item in group.items" :key="item.id" :to="`/docs/${item.id}`" :aria-current="article?.id === item.id ? 'page' : undefined" @click="menuOpen = false"><UiIcon :name="item.icon" :size="15" /><span>{{ item.title }}<small v-if="query.trim()">{{ item.summary }}</small></span></RouterLink></div>
          <div v-if="!filtered.length" class="docs-no-results"><p>没有匹配的文档</p><small>试试「查看」「设备」或「备份」，也可清空搜索浏览全部模块。</small><button @click="query = ''">清空搜索</button></div>
        </nav>
        <p class="docs-offline-note"><UiIcon name="book" :size="13" />内置文档 · 可离线阅读</p>
      </aside>
      <article v-if="article" :key="article.id" class="docs-article" aria-labelledby="doc-title">
        <header class="docs-article-header"><p class="help-eyebrow">{{ article.group }}</p><h2 id="doc-title" ref="articleHeading" tabindex="-1">{{ article.title }}</h2><p>{{ article.summary }}</p><span class="docs-audience"><UiIcon name="user" :size="13" />适用：{{ article.audience }}</span></header>
        <nav class="docs-toc" aria-label="本篇内容"><strong>本篇内容</strong><a v-for="section in article.sections" :key="section.id" :href="`#/docs/${article.id}`" @click.prevent="jump(section.id)">{{ section.title }}</a></nav>
        <section v-for="section in article.sections" :key="section.id" class="docs-section" :aria-labelledby="`doc-${section.id}`">
          <h3 :id="`doc-${section.id}`" tabindex="-1">{{ section.title }}</h3>
          <p v-for="paragraph in section.paragraphs" :key="paragraph">{{ paragraph }}</p>
          <ol v-if="section.steps" class="docs-instructions"><li v-for="instruction in section.steps" :key="instruction">{{ instruction }}</li></ol>
          <div v-if="section.table" class="docs-table-scroll" role="region" :aria-label="`${section.title}对照表`" tabindex="0"><table><thead><tr><th v-for="column in section.table.headers" :key="column" scope="col">{{ column }}</th></tr></thead><tbody><tr v-for="(row, rowIndex) in section.table.rows" :key="rowIndex"><component :is="columnIndex === 0 ? 'th' : 'td'" v-for="(cell, columnIndex) in row" :key="columnIndex" :scope="columnIndex === 0 ? 'row' : undefined">{{ cell }}</component></tr></tbody></table></div>
          <div v-if="section.code" class="docs-code"><div><span>终端命令 · 请替换示例值</span><button :aria-label="`复制命令：${section.title}`" @click="copy(section.code)"><UiIcon name="copy" :size="13" />复制</button></div><pre><code>{{ section.code }}</code></pre></div>
          <p v-if="section.note" class="docs-note"><UiIcon name="info" :size="16" /><span>{{ section.note }}</span></p>
        </section>
        <p class="docs-copy-status" role="status">{{ copyStatus }}</p>
        <footer class="docs-pagination"><RouterLink v-if="previous" :to="`/docs/${previous.id}`"><small>上一篇</small><span>{{ previous.title }}</span></RouterLink><RouterLink v-if="next" :to="`/docs/${next.id}`"><small>下一篇</small><span>{{ next.title }}<UiIcon name="chevron" :size="14" /></span></RouterLink></footer>
      </article>
      <article v-else class="docs-article"><h2 ref="articleHeading" tabindex="-1">没有找到这篇文档</h2><p class="muted">文档地址可能已变更，请从左侧目录选择模块或重新搜索。</p><RouterLink class="button" to="/docs/overview">返回文档首页</RouterLink></article>
    </div>
  </component>
</template>
