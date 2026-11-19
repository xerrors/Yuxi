<template>
  <div class="home-container">
    <div v-if="isLoading" class="loading-container" role="status">
      <a-spin size="large" />
      <p>正在连接服务…</p>
    </div>
    <div v-else-if="error" class="error-container">
      <a-result status="error" :title="error.title" :sub-title="error.message">
        <template #extra>
          <a-button type="primary" @click="loadData">重试</a-button>
          <a-button :href="docsUrl" target="_blank" rel="noopener noreferrer">常见问题</a-button>
        </template>
      </a-result>
    </div>
    <template v-else>
      <KnowledgeFlow :engaged="inputFocused || Boolean(prompt.trim())" />
      <header class="site-header page-width">
        <div class="brand">
          <img :src="infoStore.branding.logo" alt="" width="32" height="32" />
          <span>{{ infoStore.branding.name }}</span>
        </div>
        <nav class="header-actions" aria-label="首页导航">
          <a class="header-docs" :href="docsUrl" target="_blank" rel="noopener noreferrer"
            >使用文档</a
          >
          <a
            class="github-link"
            href="https://github.com/xerrors/Yuxi"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub"
            ><svg
              aria-hidden="true"
              fill="currentColor"
              height="20"
              width="20"
              viewBox="0 0 16 16"
              version="1.1"
            >
              <path
                fill-rule="evenodd"
                d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
              ></path></svg
          ></a>
          <button
            type="button"
            class="theme-toggle lucide-icon-btn"
            :aria-label="`切换主题，当前${themeStore.themeLabel}`"
            :title="`主题：${themeStore.themeLabel}`"
            @click="themeStore.toggleTheme()"
          >
            <Sun v-if="themeStore.themeMode === 'light'" :size="17" aria-hidden="true" />
            <Moon v-else-if="themeStore.themeMode === 'dark'" :size="17" aria-hidden="true" />
            <Monitor v-else :size="17" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="header-chat"
            :disabled="openingChat"
            :aria-busy="openingChat"
            @click="openChat"
          >
            {{ openingChat ? '打开中' : '去对话' }}
          </button>
        </nav>
      </header>
      <main class="home-main page-width" aria-labelledby="home-title">
        <div class="home-entry">
          <h1 id="home-title" class="hero-title">
            <span
              v-for="(line, index) in titleLines"
              :key="index"
              :class="{ 'title-accent': index === 1 }"
              ><span>{{ line }}</span></span
            >
          </h1>
          <form class="entry-shell entry-composer" @submit.prevent="goToChat">
            <textarea
              id="home-prompt"
              v-model="prompt"
              aria-label="问题或任务"
              rows="2"
              placeholder="想问点什么？"
              @focus="inputFocused = true"
              @blur="inputFocused = false"
              @keydown="handlePromptKeydown"
            ></textarea>
            <div class="entry-toolbar">
              <HomeAgentPreview :paused="inputFocused || Boolean(prompt.trim())" />
              <div class="entry-actions">
                <span class="entry-model">
                  DeepSeek V4.1 Flash<ChevronDown :size="15" aria-hidden="true" />
                </span>
                <button type="submit" class="primary-button lucide-icon-btn" aria-label="发送">
                  <ArrowUp :size="20" aria-hidden="true" />
                </button>
              </div>
            </div>
          </form>
        </div>
      </main>
      <footer class="site-footer page-width">
        <p>{{ infoStore.footer?.copyright || 'All rights reserved' }}</p>
      </footer>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowUp, ChevronDown, Monitor, Moon, Sun } from '@lucide/vue'
import { useInfoStore } from '@/modules/settings/model/info'
import { healthApi } from '@/apis/system_api'
import KnowledgeFlow from '@/modules/home/ui/KnowledgeFlow.vue'
import HomeAgentPreview from '@/modules/home/ui/HomeAgentPreview.vue'
import { entryDraftHandoff } from '@/modules/session/model/thread_draft'
import { useThemeStore } from '@/shared/model/theme'

const router = useRouter()
const themeStore = useThemeStore()
const infoStore = useInfoStore()
const docsUrl = 'https://xerrors.github.io/Yuxi/'
const isLoading = ref(true)
const error = ref(null)
const prompt = ref('')
const openingChat = ref(false)
const inputFocused = ref(false)
const titleLines = computed(() => {
  const title = infoStore.branding.title || ''
  const split = title.indexOf('，')
  return split < 0 ? [title] : [title.slice(0, split + 1), title.slice(split + 1)]
})
/** 读取服务与品牌状态，失败时保留重试入口。 */
async function loadData() {
  isLoading.value = true
  error.value = null
  try {
    const response = await healthApi.checkHealth()
    if (response.status !== 'ok') throw new Error('服务不可用')
    await infoStore.loadInfoConfig()
  } catch {
    error.value = { title: '服务连接失败', message: '后端服务无法响应，请检查服务是否正常运行' }
  } finally {
    isLoading.value = false
  }
}

/** 顶部入口在路由加载期间显示反馈，输入框继续拥有草稿。 */
async function openChat() {
  openingChat.value = true
  try {
    await router.push('/agent')
  } finally {
    openingChat.value = false
  }
}

/** 文本作为新会话草稿交接，身份校验与登录返回由路由守卫处理。 */
async function goToChat() {
  const text = prompt.value.trim()
  if (text) entryDraftHandoff.save(text)
  const failure = await router.push('/agent')
  if (!failure) prompt.value = ''
}

/** 中文候选词确认和 Shift+Enter 保留输入行为。 */
function handlePromptKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void goToChat()
}
onMounted(loadData)
</script>

<style lang="less" scoped>
.home-container {
  position: relative;
  isolation: isolate;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: clip;
  color: var(--gray-900);
  background: var(--gray-10);
}
.page-width {
  width: min(1440px, 100%);
  margin-inline: auto;
  padding-inline: 80px;
}
a {
  color: inherit;
  text-decoration: none;
}
button {
  font: inherit;
  cursor: pointer;
}
a:focus-visible,
button:focus-visible {
  outline: 2px solid var(--gray-700);
  outline-offset: 5px;
}
.loading-container,
.error-container {
  min-height: 100dvh;
  display: grid;
  place-content: center;
  padding: 32px;
}
.loading-container {
  justify-items: center;
  gap: 20px;
  color: var(--gray-600);
}
.site-header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 104px;
  gap: 28px;
}
.brand {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  font-size: 20px;
  font-weight: 600;
  line-height: 1;
  color: var(--gray-700);
  min-width: 0;
  overflow-wrap: anywhere;
}
.brand img {
  object-fit: contain;
  flex-shrink: 0;
}
.brand > span {
  font-weight: inherit;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.header-docs {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--gray-600);
}
.header-docs:hover {
  color: var(--gray-900);
}
.header-chat {
  height: 32px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 7px;
  font-size: 13px;
}
.theme-toggle {
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 50%;
  color: var(--gray-600);
  background: transparent;
}
.theme-toggle:hover {
  background: var(--gray-100);
  color: var(--gray-900);
}
.github-link {
  display: grid;
  place-content: center;
  width: 32px;
  height: 32px;
  color: var(--gray-600);
}
.github-link:hover {
  color: var(--gray-900);
}
.home-main {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding-block: 48px 120px;
}
.home-entry {
  position: relative;
  width: 760px;
  max-width: 100%;
}
.hero-title {
  margin: 0 0 38px;
  text-align: center;
  font-size: 30px;
  line-height: 1.5;
  font-weight: 400;
  color: var(--gray-600);
  overflow-wrap: anywhere;
}
.hero-title > span {
  display: block;
  font-weight: inherit;
}
.hero-title > span > span {
  font-weight: inherit;
}
.hero-title .title-accent {
  margin-top: 5px;
  font-size: 48px;
  font-weight: 600;
  color: var(--gray-1000);
}
.title-accent > span {
  position: relative;
}
.title-accent > span::after {
  content: '';
  position: absolute;
  bottom: -9px;
  left: 15%;
  right: 15%;
  height: 5px;
  border-top: 1px solid var(--gray-400);
  border-radius: 50%;
  transform: rotate(-2deg);
}
.entry-shell {
  overflow: hidden;
  padding: 20px 20px 14px;
  border: 1px solid var(--gray-200);
  border-radius: 24px;
  background: var(--gray-0);
  box-shadow:
    0 2px 5px var(--shadow-0),
    0 14px 40px -16px var(--shadow-2);
  transition:
    border-color 180ms ease,
    box-shadow 180ms ease;
}
.entry-shell:focus-within {
  border-color: var(--gray-300);
  box-shadow:
    0 2px 5px var(--shadow-0),
    0 14px 40px -16px var(--shadow-3);
}
#home-prompt {
  display: block;
  width: 100%;
  height: 80px;
  padding: 2px 2px 18px;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  color: var(--gray-900);
  font: inherit;
  font-size: 17px;
  line-height: 26px;
  scrollbar-width: thin;
}
#home-prompt::placeholder {
  color: var(--gray-600);
}
.entry-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}
.entry-actions {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-shrink: 0;
}
.entry-model {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--gray-600);
}
.primary-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 50%;
  transition: border-color 180ms ease;
}
button.header-chat,
button.header-chat:hover,
button.header-chat:active,
button.header-chat:focus-visible,
button.primary-button,
button.primary-button:hover,
button.primary-button:active,
button.primary-button:focus-visible {
  background: var(--gray-10000);
  border-color: var(--gray-10000);
  color: var(--gray-0);
  box-shadow: none;
}
.site-footer {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 72px;
  color: var(--gray-500);
  font-size: 11px;
}
.site-footer p {
  margin: 0;
  overflow-wrap: anywhere;
}
@media (max-width: 1200px) {
  .page-width {
    padding-inline: 56px;
  }
}
@media (max-width: 900px) {
  .page-width {
    padding-inline: 40px;
  }
  .site-header {
    min-height: 88px;
  }
  .header-actions {
    gap: 10px;
  }
  .hero-title {
    font-size: 26px;
  }
  .hero-title .title-accent {
    font-size: 40px;
  }
  .home-main {
    padding-block: 50px 80px;
  }
  .site-footer {
    min-height: 70px;
  }
}
@media (max-height: 760px) {
  .site-header {
    min-height: 84px;
  }
  .home-main {
    padding-block: 36px 52px;
  }
  .hero-title {
    margin-bottom: 30px;
  }
  .site-footer {
    min-height: 64px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .entry-shell,
  .primary-button {
    transition: none;
  }
}
</style>
