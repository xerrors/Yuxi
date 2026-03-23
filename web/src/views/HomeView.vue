<template>
  <div class="home-container">
    <!-- 加载中状态 -->
    <div v-if="isLoading" class="loading-container">
      <a-spin size="large" />
      <p class="loading-text">正在连接服务...</p>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-container">
      <a-result status="error" :title="error.title" :sub-title="error.message">
        <template #extra>
          <a-button type="primary" @click="retryLoad">重试</a-button>
          <a-button :href="faqUrl" target="_blank" rel="noopener noreferrer">常见问题</a-button>
        </template>
      </a-result>
    </div>

    <!-- 正常内容 -->
    <template v-else>
      <div class="hero-section">
        <div class="glass-header">
          <div class="logo">
            <img
              :src="infoStore.organization.logo"
              :alt="infoStore.organization.name"
              class="logo-img"
            />
            <span class="logo-text">{{ infoStore.organization.name }}</span>
          </div>
          <div class="header-actions">
            <div class="github-link">
              <a href="https://github.com/xerrors/Yuxi-Know" target="_blank">
                <svg height="20" width="20" viewBox="0 0 16 16" version="1.1">
                  <path
                    fill-rule="evenodd"
                    d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
                  ></path>
                </svg>
              </a>
            </div>
            <UserInfoComponent :show-button="true" />
          </div>
        </div>

        <div class="hero-layout">
          <div class="hero-content reveal-up">
            <p v-if="typedBadge" class="hero-badge" :class="{ typing: isBadgeTyping }">
              <template v-if="badgeParts.number">
                <span>{{ badgeParts.prefix }}</span>
                <a
                  class="hero-badge-link"
                  :href="repoUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <span class="hero-badge-number">{{ badgeParts.number }}</span>
                </a>
                <span>{{ badgeParts.suffix }}</span>
              </template>
              <template v-else>{{ typedBadge }}</template>
            </p>
            <h1 class="title reveal-up delay-1">{{ infoStore.branding.title }}</h1>
            <Transition name="subtitle-switch" mode="out-in">
              <p v-if="currentSubtitle" class="subtitle" :key="currentSubtitle">{{ currentSubtitle }}</p>
            </Transition>
            <!-- <p class="description">{{ infoStore.branding.description }}</p> -->
            <div class="hero-actions">
              <button class="button-base primary" @click="goToChat">开始体验</button>
              <a class="doc-text-link" href="https://xerrors.github.io/Yuxi-Know/" target="_blank"
                >查看文档</a
              >
            </div>
          </div>
          <div class="insight-panel" v-if="featureCards.length">
            <div
              class="stat-card"
              v-for="(card, index) in featureCards"
              :key="card.label"
              :style="{ '--card-stagger': `${index}` }"
            >
              <div class="stat-headline">
                <span class="stat-icon" v-if="card.icon">
                  <component :is="card.icon" />
                </span>
                <p class="stat-value">{{ card.value }}</p>
              </div>
              <p class="stat-label">{{ card.label }}</p>
              <p class="stat-description">{{ card.description }}</p>
            </div>
          </div>
        </div>
      </div>

      <div class="section action-section" v-if="actionLinks.length">
        <div class="action-grid">
          <a
            v-for="action in actionLinks"
            :key="action.name"
            class="action-card"
            :href="action.url"
            target="_blank"
            rel="noopener noreferrer"
          >
            <span class="action-icon" v-if="action.icon">
              <component :is="action.icon" />
            </span>
            <div class="action-meta">
              <p class="action-title">{{ action.name }}</p>
              <p class="action-url">{{ action.url }}</p>
            </div>
          </a>
        </div>
      </div>

      <ProjectOverview />

      <footer class="footer">
        <div class="footer-content">
          <p class="copyright">{{ infoStore.footer?.copyright || '© 2025 All rights reserved' }}</p>
        </div>
      </footer>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useInfoStore } from '@/stores/info'
import { useAgentStore } from '@/stores/agent'
import { healthApi } from '@/apis/system_api'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import ProjectOverview from '@/components/ProjectOverview.vue'
import {
  BookText,
  Bug,
  Video,
  Route,
  Github,
  Star,
  CheckCircle2,
  GitCommit,
  ShieldCheck
} from 'lucide-vue-next'

const router = useRouter()
const userStore = useUserStore()
const infoStore = useInfoStore()
const agentStore = useAgentStore()
const repoUrl = 'https://github.com/xerrors/Yuxi-Know'
const faqUrl = 'https://xerrors.github.io/Yuxi-Know/latest/changelog/faq.html'

// 加载状态
const isLoading = ref(true)
const error = ref(null)
const typedBadge = ref('')
const isBadgeTyping = ref(false)
let badgeTimer = null
let subtitleTimer = null
let starsFetchController = null

const GITHUB_REPO_API = 'https://api.github.com/repos/xerrors/Yuxi-Know'
const GITHUB_STARS_TIMEOUT = 3000

const formatStars = (count) => {
  if (!Number.isFinite(count) || count <= 0) {
    return ''
  }
  return `${count}`
}

const subtitleIndex = ref(0)

const subtitleOptions = computed(() => {
  const subtitles = infoStore.branding?.subtitles
  if (Array.isArray(subtitles)) {
    const list = subtitles
      .map((item) => (typeof item === 'string' ? item.trim() : ''))
      .filter(Boolean)
    if (list.length) {
      return list
    }
  }

  const fallback = (infoStore.branding?.subtitle || '').trim()
  return fallback ? [fallback] : []
})

const currentSubtitle = computed(() => subtitleOptions.value[subtitleIndex.value] || '')
const badgeParts = computed(() => {
  const text = typedBadge.value || ''
  const match = text.match(/^(.*?)(\d[\d,]*\+?)(\s+GitHub Stars.*)?$/)
  if (!match) {
    return {
      prefix: text,
      number: '',
      suffix: ''
    }
  }

  return {
    prefix: match[1] || '',
    number: match[2] || '',
    suffix: match[3] || ''
  }
})

const stopSubtitleCarousel = () => {
  if (subtitleTimer) {
    clearInterval(subtitleTimer)
    subtitleTimer = null
  }
}

const startSubtitleCarousel = () => {
  stopSubtitleCarousel()
  subtitleIndex.value = 0

  if (subtitleOptions.value.length <= 1) {
    return
  }

  subtitleTimer = setInterval(() => {
    subtitleIndex.value = (subtitleIndex.value + 1) % subtitleOptions.value.length
  }, 2800)
}

const stopStarsFetch = () => {
  if (starsFetchController) {
    starsFetchController.abort()
    starsFetchController = null
  }
}

const fetchGithubStars = async () => {
  stopStarsFetch()
  const controller = new AbortController()
  starsFetchController = controller
  const timer = setTimeout(() => {
    controller.abort()
  }, GITHUB_STARS_TIMEOUT)

  try {
    const response = await fetch(GITHUB_REPO_API, { signal: controller.signal })
    if (!response.ok) {
      return null
    }

    const data = await response.json()
    const stars = Number(data?.stargazers_count)
    return Number.isFinite(stars) && stars > 0 ? stars : null
  } catch (e) {
    return null
  } finally {
    clearTimeout(timer)
    if (starsFetchController === controller) {
      starsFetchController = null
    }
  }
}

const getHeroBadgeText = (starsCount = null) => {
  const realtimeStars = formatStars(starsCount)
  if (realtimeStars) {
    return `已获得 ${realtimeStars} GitHub Stars`
  }

  const features = Array.isArray(infoStore.features) ? infoStore.features : []
  const starFeature = features.find((item) => {
    if (typeof item === 'string') {
      return /star/i.test(item)
    }

    return /star|github/i.test(item?.label || '') || /stars|github/i.test(item?.icon || '')
  })

  if (!starFeature) {
    return ''
  }

  const starValue =
    typeof starFeature === 'string'
      ? ''
      : (starFeature?.value || '').toString().trim()

  return starValue ? `已获得 ${starValue} GitHub Stars` : '已获得 GitHub Stars'
}

const stopBadgeTyping = () => {
  if (badgeTimer) {
    clearInterval(badgeTimer)
    badgeTimer = null
  }
  isBadgeTyping.value = false
}

const startBadgeTyping = (starsCount = null) => {
  stopBadgeTyping()
  const text = getHeroBadgeText(starsCount)
  typedBadge.value = ''

  if (!text) {
    return
  }

  let index = 0
  isBadgeTyping.value = true
  badgeTimer = setInterval(() => {
    index += 1
    typedBadge.value = text.slice(0, index)
    if (index >= text.length) {
      stopBadgeTyping()
    }
  }, 45)
}

const checkHealth = async () => {
  try {
    const response = await healthApi.checkHealth()
    if (response.status !== 'ok') {
      throw new Error('服务不可用')
    }
  } catch (e) {
    error.value = {
      title: '服务连接失败',
      message: '后端服务无法响应，请检查服务是否正常运行'
    }
    throw e
  }
}

const loadData = async () => {
  isLoading.value = true
  error.value = null

  try {
    // 先检查健康状态
    await checkHealth()
    // 健康检查通过后加载配置
    await infoStore.loadInfoConfig()
    startSubtitleCarousel()
    const starsCount = await fetchGithubStars()
    startBadgeTyping(starsCount)
  } catch (e) {
    console.error('加载失败:', e)
    stopBadgeTyping()
    stopSubtitleCarousel()
    stopStarsFetch()
    typedBadge.value = ''
  } finally {
    isLoading.value = false
  }
}

const retryLoad = () => {
  loadData()
}

const goToChat = async () => {
  // 检查用户是否登录
  if (!userStore.isLoggedIn) {
    // 登录后应该跳转到默认智能体而不是/agent
    sessionStorage.setItem('redirect', '/') // 设置为首页，登录后会通过路由守卫处理重定向
    router.push('/login')
    return
  }

  // 根据用户角色进行跳转
  if (userStore.isAdmin) {
    // 管理员用户跳转到聊天页面
    await agentStore.initialize()
    router.push('/agent')
    return
  }

  // 普通用户跳转到默认智能体
  try {
    // 获取默认智能体
    const defaultAgent = agentStore.defaultAgent
    if (defaultAgent?.id) {
      router.push(`/agent/${defaultAgent.id}`)
    } else {
      router.push('/agent')
    }
  } catch (error) {
    console.error('跳转到智能体页面失败:', error)
    router.push('/')
  }
}

onMounted(() => {
  // 加载数据
  loadData()
})

onUnmounted(() => {
  stopBadgeTyping()
  stopSubtitleCarousel()
  stopStarsFetch()
})

const iconKey = (value) => (typeof value === 'string' ? value.toLowerCase() : '')

// region icon_mapping
const featureIconMap = {
  stars: Star,
  issues: CheckCircle2,
  resolved: CheckCircle2,
  commits: GitCommit,
  license: ShieldCheck,
  default: Star
}

const actionIconMap = {
  doc: BookText,
  docs: BookText,
  document: BookText,
  issue: Bug,
  bug: Bug,
  roadmap: Route,
  plan: Route,
  demo: Video,
  video: Video,
  github: Github,
  default: Github
}
// endregion icon_mapping

const featureCards = computed(() => {
  const list = Array.isArray(infoStore.features) ? infoStore.features : []
  return list
    .map((item) => {
      if (typeof item === 'string') {
        return {
          label: item,
          value: '',
          description: '',
          icon: featureIconMap.default
        }
      }

      const key = iconKey(item.icon || item.type)
      return {
        label: item.label || item.name || '',
        value: item.value || '',
        description: item.description || '',
        icon: featureIconMap[key] || featureIconMap.default
      }
    })
    .filter((item) => item.label || item.value || item.description)
})

const actionLinks = computed(() => {
  const actions = infoStore.actions
  if (!Array.isArray(actions)) {
    return []
  }

  return actions
    .map((item) => {
      const key = iconKey(item?.icon || item?.type)
      return {
        name: item?.name || item?.label || '',
        url: item?.url || item?.link || '',
        icon: actionIconMap[key] || actionIconMap.default
      }
    })
    .filter((item) => item.name && item.url)
})
</script>

<style lang="less" scoped>
.home-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  color: var(--main-900);
  background: radial-gradient(circle at top right, var(--main-50), transparent 60%), var(--main-5);
  position: relative;
  overflow-x: hidden;
}

// 加载中状态
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  gap: 1rem;

  .loading-text {
    color: var(--gray-600);
    font-size: 0.95rem;
  }
}

// 错误状态
.error-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 2rem;
}
.glass-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 0.75rem 2.5rem;
  background-color: var(--color-trans-light);
  backdrop-filter: blur(20px);
  // border-bottom: 1px solid var(--main-30);
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  box-shadow: 0 6px 25px rgba(3, 80, 101, 0.02);
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 1rem;
  text-decoration: none;
  color: var(--gray-800);
  font-weight: 500;
  font-size: 0.95rem;
  transition: color 0.2s ease;
  position: relative;
  overflow: hidden;

  &:hover {
    color: var(--gray-900);

    svg {
      transform: scale(1.1);
    }
  }

  &.router-link-active {
    background: linear-gradient(135deg, var(--main-600), var(--main-500));
    color: var(--gray-0);
    border-radius: 1.5rem;

    &:hover {
      background: linear-gradient(135deg, var(--main-700), var(--main-600));
    }
  }

  span {
    white-space: nowrap;
  }
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.logo {
  display: flex;
  align-items: center;
  font-size: 1.4rem;
  font-weight: bold;
  color: var(--main-800);

  .logo-img {
    height: 2rem;
    margin-right: 0.6rem;
  }
}

.logo-text {
  font-size: 1.3rem;
  font-weight: 600;
}

.github-link a {
  display: flex;
  align-items: center;
  text-decoration: none;
  color: var(--gray-600);
  padding: 0.6rem 1rem;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  font-size: 0.9rem;
  font-weight: 500;

  &:hover {
    color: var(--gray-700);

    svg {
      transform: scale(1.1);
    }
  }

  svg {
    margin-right: 6px;
    transition: transform 0.3s ease;
    fill: currentColor;
  }

  .stars-count {
    font-weight: 600;
  }

  // 暗色模式样式
  :global(.dark) & {
    color: var(--gray-400);

    &:hover {
      color: var(--gray-300);
    }
  }
}

.hero-section {
  flex: 1;
  width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 5rem 2rem 2rem;
}

.hero-layout {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 2.5rem;
  align-items: center;
  max-width: 1200px;
  margin: 0 auto;
  padding-top: 4rem;
}

.hero-content {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.reveal-up {
  opacity: 0;
  transform: translateY(14px);
  animation: revealUp 0.7s ease forwards;
}

.reveal-up.delay-1 {
  animation-delay: 120ms;
}

.hero-badge {
  color: var(--main-600);
  font-size: 0.92rem;
  letter-spacing: 0.04em;
  font-weight: 600;
  margin: 0;
}

.hero-badge-link {
  color: inherit;
  text-decoration: none;
}

.hero-badge-number {
  color: var(--main-700);
  text-decoration: underline;
  text-decoration-color: var(--main-500);
  text-underline-offset: 0.15em;
  text-decoration-thickness: 1.5px;
  font-weight: 700;
  transition:
    color 0.2s ease,
    text-decoration-color 0.2s ease;
}

.hero-badge-link:hover .hero-badge-number {
  color: var(--main-800);
  text-decoration-color: var(--main-700);
}

.hero-badge.typing::after {
  content: '';
  display: inline-block;
  width: 1px;
  height: 1em;
  margin-left: 6px;
  background: var(--main-600);
  vertical-align: -0.1em;
  animation: caretBlink 0.8s steps(1, end) infinite;
}

.title {
  font-size: clamp(2.5rem, 4vw, 4rem);
  font-weight: 800;
  margin: 0;
  background: linear-gradient(135deg, var(--main-900), var(--main-600));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  letter-spacing: -0.02em;
  line-height: 1.1;
}

.hero-eyebrow {
  color: var(--main-600);
  text-transform: uppercase;
  letter-spacing: 0.2em;
  font-size: 0.85rem;
}

.subtitle {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--gray-700);
  line-height: 1.4;
  margin: 0;
  min-height: calc(1.4em * 1.3);
}

.subtitle-switch-enter-active,
.subtitle-switch-leave-active {
  transition:
    opacity 0.32s ease,
    transform 0.32s ease;
}

.subtitle-switch-enter-from,
.subtitle-switch-leave-to {
  opacity: 0;
  transform: translateY(7px);
}

.description {
  color: var(--gray-600);
  line-height: 1.6;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  align-items: center;
}

.doc-text-link {
  color: var(--main-700);
  font-weight: 600;
  text-decoration: none;
  border-bottom: 1px dashed var(--main-300);
  padding-bottom: 0.15rem;
  transition:
    color 0.2s ease,
    border-color 0.2s ease;

  &:hover {
    color: var(--main-800);
    border-color: var(--main-500);
  }
}

.button-base {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  padding: 0.5rem 2.75rem;
  border-radius: 999px;
  font-size: 1.05rem;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  text-decoration: none;
  transition: all 0.25s ease;
  min-height: 52px;
}

.button-base.primary {
  background: linear-gradient(135deg, var(--main-600), var(--main-500));
  color: var(--gray-0);
  border-color: transparent;
  position: relative;
  isolation: isolate;

  &:hover {
    background: linear-gradient(135deg, var(--main-700), var(--main-600));
  }

  &::after {
    content: '';
    position: absolute;
    inset: -2px;
    border-radius: inherit;
    background: radial-gradient(circle, rgba(36, 131, 154, 0.35), transparent 68%);
    opacity: 0;
    z-index: -1;
    transition: opacity 0.25s ease;
  }

  &:hover::after {
    opacity: 1;
  }
}

.button-base.secondary {
  background: rgba(2, 57, 68, 0.06);
  color: var(--main-700);
  border-color: var(--gray-100);

  &:hover {
    border-color: var(--main-200);
    background: var(--gray-50);
  }
}

.insight-panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem;
  background: var(--main-0);
  border-radius: 1.5rem;
  padding: 1.5rem;
  border: 1px solid var(--main-40);
  box-shadow: 0 15px 35px rgba(3, 80, 101, 0.08);
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  opacity: 0;
  transform: translateY(12px);
  animation: cardRise 0.55s ease forwards;
  animation-delay: calc(var(--card-stagger, 0) * 90ms + 200ms);
}

.stat-headline {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--gray-25);
  display: inline-flex;
  align-items: center;
  justify-content: center;

  :deep(svg) {
    width: 24px;
    height: 24px;
    color: var(--main-700);
  }
}

.stat-value {
  font-size: 2rem;
  font-weight: 700;
  color: var(--main-800);
  margin: 0;
}

.stat-label {
  margin: 0;
  color: var(--gray-700);
  font-weight: 600;
}

.stat-description {
  margin: 0;
  color: var(--gray-600);
  font-size: 0.9rem;
}

.section {
  width: 100%;
  max-width: 1200px;
  margin: 50px auto 0px auto;
  padding: 2rem 0;
}

.section-header {
  margin-bottom: 1.5rem;

  h2 {
    margin: 0 0 0.5rem;
    font-size: 1.8rem;
    color: var(--main-800);
  }

  p {
    margin: 0;
    color: var(--gray-600);
  }
}

.action-section {
  padding-bottom: 3rem;
}

.action-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1rem;
}

.action-card {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  padding: 1rem 1.25rem;
  border-radius: 1rem;
  text-decoration: none;
  color: inherit;
  border: 1px solid var(--gray-50);
  background: var(--gray-0);
  transition:
    transform 0.2s ease,
    background 0.2s ease;

  &:hover {
    background: var(--gray-0);
    transform: translateY(-2px);
  }
}

.action-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--gray-50);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;

  :deep(svg) {
    width: 22px;
    height: 22px;
    color: var(--main-700);
  }
}

.action-meta {
  flex: 1;
  overflow: hidden;
}

.action-title {
  margin: 0;
  font-weight: 600;
  color: var(--main-800);
}

.action-url {
  margin: 0.25rem 0 0;
  font-size: 0.9rem;
  color: var(--gray-600);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.preview-section {
  padding: 5rem 2rem;
  display: flex;
  justify-content: center;
}

.preview-container {
  position: relative;
  max-width: 1000px;
  overflow: hidden;
  border-radius: 1rem;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
  transition: all 0.3s ease;

  &:hover {
    transform: translateY(-5px);
    box-shadow: 0 25px 50px rgba(0, 0, 0, 0.15);

    .preview-overlay {
      opacity: 1;
    }
  }

  img {
    width: 100%;
    height: auto;
    display: block;
    transition: transform 0.5s ease;
  }

  .preview-overlay {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    background: linear-gradient(to top, rgba(0, 0, 0, 0.7), transparent);
    padding: 2rem;
    opacity: 0.8;
    transition: opacity 0.3s ease;

    .overlay-content {
      color: var(--gray-0);

      h3 {
        font-size: 1.5rem;
        margin-bottom: 0.5rem;
      }

      p {
        font-size: 1rem;
        opacity: 0.9;
      }
    }
  }
}

.footer {
  margin-top: auto;
  background: var(--main-0);
  border-top: 1px solid var(--main-20);
  position: relative;
  z-index: 10;
}

.footer-content {
  text-align: center;
  padding: 2rem;
  max-width: 1200px;
  margin: 0 auto;
}

.copyright {
  color: var(--main-700);
  font-size: 0.9rem;
  font-weight: 500;
  margin: 0;
  opacity: 0.8;
  transition: opacity 0.3s ease;
}

.footer:hover .copyright {
  opacity: 1;
}

@keyframes revealUp {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes cardRise {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes caretBlink {
  50% {
    opacity: 0;
  }
}

:global(:root.dark) {
  .hero-badge-number {
    color: var(--main-200);
    text-decoration-color: var(--main-300);
  }

  .hero-badge-link:hover .hero-badge-number {
    color: var(--main-100);
    text-decoration-color: var(--main-100);
  }

  .doc-text-link {
    color: var(--main-300);
    border-bottom-color: var(--main-500);

    &:hover {
      color: var(--main-200);
      border-bottom-color: var(--main-300);
    }
  }

  .button-base.primary::after {
    background: radial-gradient(circle, rgba(130, 195, 214, 0.28), transparent 72%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .reveal-up,
  .stat-card,
  .hero-badge.typing::after {
    animation: none;
  }

  .reveal-up,
  .stat-card {
    opacity: 1;
    transform: none;
  }

  .subtitle-switch-enter-active,
  .subtitle-switch-leave-active {
    transition: none;
  }
}

@media (max-width: 768px) {
  .glass-header {
    padding: 0.8rem 1.25rem;
    flex-wrap: wrap;
    gap: 1rem;
  }

  .nav-links {
    order: 3;
    width: 100%;
    justify-content: center;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .nav-link {
    padding: 0.5rem 0.8rem;
    font-size: 0.85rem;

    span {
      display: none;
    }
  }

  .logo {
    font-size: 1.1rem;
  }

  .title {
    font-size: 2.4rem;
  }

  .subtitle {
    font-size: 1.2rem;
  }

  .start-button {
    width: 100%;
    text-align: center;
  }

  .hero-content {
    padding: 0;
  }

  .github-link a {
    padding: 0.5rem 0.8rem;
    font-size: 0.85rem;

    .stars-count {
      display: none;
    }
  }

  .hero-layout {
    padding: 0 0.5rem;
  }
}
</style>
