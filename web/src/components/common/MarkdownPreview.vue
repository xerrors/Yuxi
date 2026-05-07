<template>
  <div
    :class="[
      'yk-markdown-preview',
      'flat-md-preview',
      { 'is-dark': themeStore.isDark, 'is-compact': compact }
    ]"
    v-html="renderedMarkdown"
  ></div>
</template>

<script setup>
import { computed, shallowRef, watch } from 'vue'
import { useThemeStore } from '@/stores/theme'
import { renderMarkdown } from '@/utils/markdown_preview'
import 'katex/dist/katex.min.css'

const props = defineProps({
  content: {
    type: String,
    default: ''
  },
  compact: {
    type: Boolean,
    default: false
  }
})

const themeStore = useThemeStore()
const shikiTheme = computed(() => (themeStore.isDark ? 'github-dark' : 'github-light'))
const renderedMarkdown = shallowRef('')

watch(
  [() => props.content, shikiTheme],
  async ([content, theme], _, onCleanup) => {
    let expired = false
    onCleanup(() => {
      expired = true
    })

    if (!content) {
      renderedMarkdown.value = ''
      return
    }

    const html = await renderMarkdown(content, { theme })
    if (!expired) renderedMarkdown.value = html
  },
  { immediate: true }
)
</script>

<style lang="less">
.yk-markdown-preview,
.flat-md-preview.yk-markdown-preview {
  max-width: 100%;
  color: var(--gray-1000);
  font-family:
    -apple-system, BlinkMacSystemFont, 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei',
    'Hiragino Sans GB', 'Source Han Sans CN', sans-serif;
  font-size: 0.95rem;
  line-height: 1.75;
  word-break: break-word;
  padding: 0;

  &.is-compact {
    font-size: 14px;
    line-height: 1.65;
  }

  h1,
  h2 {
    font-size: 1.1rem;
  }

  h3,
  h4 {
    font-size: 1.05rem;
  }

  h5,
  h6 {
    font-size: 1rem;
  }

  strong {
    font-weight: 500;
  }

  p:last-child {
    margin-bottom: 0;
  }

  li > p,
  ol > p,
  ul > p {
    margin: 0.25rem 0;
  }

  ul,
  ol {
    padding-left: 1.625rem;
  }

  ul li::marker,
  ol li::marker {
    color: var(--main-bright);
  }

  .contains-task-list {
    padding-left: 0;
    list-style: none;
  }

  .task-list-item {
    list-style: none;
  }

  .task-list-item-checkbox {
    margin-right: 8px;
    transform: translateY(1px);
  }

  a {
    color: var(--main-700);
  }

  hr {
    height: 1px;
    margin: 1.25rem 0;
    border: 0;
    background: linear-gradient(90deg, transparent, var(--gray-200), transparent);
  }

  blockquote {
    margin: 1rem 0;
    padding: 0.25rem 0 0.25rem 1rem;
    border-left: 3px solid var(--gray-200);
    color: var(--gray-700);
  }

  cite {
    position: relative;
    margin-left: 4px;
    padding: 0 0.25rem;
    border-radius: 4px;
    outline: 2px solid var(--gray-200);
    background-color: var(--gray-200);
    color: var(--gray-800);
    font-size: 12px;
    font-style: normal;
    cursor: pointer;
    user-select: none;

    &:hover::after {
      content: attr(source);
      position: absolute;
      bottom: calc(100% + 6px);
      left: 50%;
      z-index: 1000;
      width: max-content;
      min-width: 100px;
      max-width: 400px;
      padding: 8px 12px;
      border-radius: 6px;
      transform: translateX(-50%);
      background-color: #222;
      color: #fff;
      font-size: 13px;
      line-height: 1.5;
      text-align: center;
      white-space: normal;
      word-break: break-word;
      pointer-events: none;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    }

    &:hover::before {
      content: '';
      position: absolute;
      bottom: 100%;
      left: 50%;
      z-index: 1000;
      transform: translateX(-50%);
      border: 5px solid transparent;
      border-top-color: var(--gray-900);
    }
  }

  code {
    font-family:
      'Menlo', 'Monaco', 'Consolas', 'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei',
      'Hiragino Sans GB', 'Source Han Sans CN', 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.5;
    letter-spacing: 0.025em;
    tab-size: 4;
    -moz-tab-size: 4;
  }

  :not(pre) > code {
    padding: 1px 5px;
    border-radius: 4px;
    background-color: var(--gray-25);
  }

  pre.shiki {
    margin: 12px 0;
    padding: 12px 14px;
    border: 1px solid var(--gray-100);
    border-radius: 8px;
    overflow: auto;
    font-size: 13px;
    line-height: 1.5;
  }

  &:not(.is-dark) pre.shiki {
    background: var(--gray-25) !important;
  }

  &.is-dark pre.shiki {
    border-color: var(--gray-200);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    margin: 2em 0;
    font-size: 15px;
    display: table;
    outline: 1px solid var(--gray-100);
    outline-offset: 12px;
    border-radius: 8px;
  }

  th,
  td {
    padding: 0.5rem 0;
    text-align: left;
    border: none;
  }

  td {
    border-bottom: 1px solid var(--gray-100);
    color: var(--gray-800);
  }

  tbody tr:last-child td {
    border-bottom: none;
  }

  th {
    border-bottom: 1px solid var(--gray-200);
    color: var(--gray-800);
    font-weight: 600;
  }

  tr {
    background-color: var(--gray-0);
  }

  .katex {
    font-size: 1.05em;
  }

  .katex-display {
    margin: 1rem 0;
    overflow-x: auto;
    overflow-y: hidden;
  }

  .frontmatter-card {
    margin: 0 0 20px;
    padding: 12px 14px;
    border-radius: 8px;
    background: var(--gray-25);
  }

  .frontmatter-card .fm-body {
    display: grid;
    gap: 6px;
  }

  .frontmatter-card .fm-row {
    display: grid;
    grid-template-columns: 96px minmax(0, 1fr);
    gap: 14px;
    align-items: baseline;
  }

  .frontmatter-card .fm-key {
    color: var(--gray-500);
    font-family: 'JetBrains Mono', 'Fira Code', 'Monaco', 'Menlo', monospace;
    font-size: 12px;
    line-height: 1.5;
  }

  .frontmatter-card .fm-value {
    color: var(--gray-800);
    font-size: 13px;
    line-height: 1.5;
    min-width: 0;
  }

  .frontmatter-card .fm-doc-title {
    color: var(--gray-1000);
    font-weight: 600;
  }

  .frontmatter-card .fm-tag {
    display: inline-flex;
    align-items: center;
    margin: 0 4px 4px 0;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--gray-100);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 1.5;
  }

  .frontmatter-card .fm-json {
    margin: 2px 0 0;
    padding: 8px 10px;
    border-radius: 6px;
    overflow: auto;
    background: var(--gray-50);
    color: var(--gray-800);
    font-size: 12px;
    line-height: 1.5;
  }
}
</style>
