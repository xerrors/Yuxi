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
      <!-- 氛围装饰背景 -->
      <div class="ambient" aria-hidden="true">
        <span class="orb orb-1"></span>
        <span class="orb orb-2"></span>
        <span class="orb orb-3"></span>
        <div class="grid-mesh"></div>
      </div>

      <header class="glass-header">
        <div class="logo">
          <img
            :src="infoStore.organization.logo"
            :alt="infoStore.organization.name"
            class="logo-img"
          />
          <span class="logo-text">{{ infoStore.organization.name }}</span>
        </div>
        <div class="header-actions">
          <!-- <a
            class="github-link"
            href="https://github.com/xerrors/Yuxi"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="GitHub"
          >
            <svg height="20" width="20" viewBox="0 0 16 16" version="1.1">
              <path
                fill-rule="evenodd"
                d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
              ></path>
            </svg>
          </a> -->
          <UserInfoComponent :show-button="true" />
        </div>
      </header>

      <main class="hero-section">
        <div class="hero-layout">
          <div class="hero-content reveal-up">
            <p v-if="typedBadge" class="hero-badge" :class="{ typing: isBadgeTyping }">
              <span class="badge-dot"></span>
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
              <p v-if="currentSubtitle" class="subtitle" :key="currentSubtitle">
                {{ currentSubtitle }}
              </p>
            </Transition>
            <div class="hero-actions reveal-up delay-2">
              <button class="button-base primary" @click="goToChat">
                <span>开始体验</span>
                <ArrowRight :size="18" />
              </button>
              <!-- <a
                class="button-base secondary"
                href="https://xerrors.github.io/Yuxi/"
                target="_blank"
                rel="noopener noreferrer"
              >
                <BookText :size="18" />
                <span>查看文档</span>
              </a> -->
            </div>
          </div>

          <aside class="hero-visual reveal-up delay-1">
            <div class="visual-card">
              <div class="visual-glow" aria-hidden="true"></div>
              <svg
                class="graph-watermark"
                viewBox="0 0 240 200"
                fill="none"
                aria-hidden="true"
                xmlns="http://www.w3.org/2000/svg"
              >
                <g stroke="currentColor" stroke-width="2">
                  <line x1="120" y1="100" x2="48" y2="44" />
                  <line x1="120" y1="100" x2="200" y2="56" />
                  <line x1="120" y1="100" x2="56" y2="156" />
                  <line x1="120" y1="100" x2="180" y2="150" />
                  <line x1="48" y1="44" x2="200" y2="56" />
                </g>
                <g fill="currentColor">
                  <circle cx="120" cy="100" r="11" />
                  <circle cx="48" cy="44" r="7" />
                  <circle cx="200" cy="56" r="8" />
                  <circle cx="56" cy="156" r="6" />
                  <circle cx="180" cy="150" r="9" />
                </g>
              </svg>

              <div class="flow-diagram">
                <div class="flow-row">
                  <div class="flow-node">
                    <span class="flow-icon"><Workflow :size="22" /></span>
                    <span class="flow-name">智能体 Harness</span>
                  </div>

                  <div class="flow-link" aria-hidden="true">
                    <span class="flow-rail"></span>
                    <span
                      class="flow-dot flow-dot--fwd"
                      v-for="n in 2"
                      :key="`f1${n}`"
                      :style="{ '--i': n - 1 }"
                    ></span>
                    <span
                      class="flow-dot flow-dot--back"
                      v-for="n in 2"
                      :key="`b1${n}`"
                      :style="{ '--i': n - 1 }"
                    ></span>
                  </div>

                  <div class="flow-node">
                    <span class="flow-icon"><Sparkles :size="22" /></span>
                    <span class="flow-name">RAG 引擎</span>
                  </div>

                  <div class="flow-link" aria-hidden="true">
                    <span class="flow-rail"></span>
                    <span
                      class="flow-dot flow-dot--fwd"
                      v-for="n in 2"
                      :key="`f2${n}`"
                      :style="{ '--i': n - 1 }"
                    ></span>
                    <span
                      class="flow-dot flow-dot--back"
                      v-for="n in 2"
                      :key="`b2${n}`"
                      :style="{ '--i': n - 1 }"
                    ></span>
                  </div>

                  <div class="flow-node">
                    <span class="flow-icon"><Library :size="22" /></span>
                    <span class="flow-name">知识库</span>
                  </div>
                </div>

                <div class="flow-mid">
                  <div class="flow-vcol">
                    <div class="flow-vlink" aria-hidden="true">
                      <span class="flow-vrail"></span>
                      <span
                        class="flow-dot flow-dot--down"
                        v-for="n in 2"
                        :key="`d1${n}`"
                        :style="{ '--i': n - 1 }"
                      ></span>
                      <span
                        class="flow-dot flow-dot--up"
                        v-for="n in 2"
                        :key="`u1${n}`"
                        :style="{ '--i': n - 1 }"
                      ></span>
                    </div>
                  </div>

                  <div class="flow-center">
                    <div class="flow-node flow-node--hub">
                      <span class="flow-icon flow-icon--hub flow-icon--llm">
                        <span class="hub-ring"></span>
                        <Brain :size="46" />
                      </span>
                      <span class="flow-name"> </span>
                    </div>
                  </div>

                  <div class="flow-vcol">
                    <div class="flow-vlink" aria-hidden="true">
                      <span class="flow-vrail"></span>
                      <span
                        class="flow-dot flow-dot--down"
                        v-for="n in 2"
                        :key="`d2${n}`"
                        :style="{ '--i': n - 1 }"
                      ></span>
                      <span
                        class="flow-dot flow-dot--up"
                        v-for="n in 2"
                        :key="`u2${n}`"
                        :style="{ '--i': n - 1 }"
                      ></span>
                    </div>
                  </div>
                </div>

                <div class="flow-row flow-row--second">
                  <div class="flow-node">
                    <span class="flow-icon"><User :size="22" /></span>
                    <span class="flow-name">终端用户</span>
                  </div>

                  <div class="flow-link" aria-hidden="true">
                    <span class="flow-rail"></span>
                    <span
                      class="flow-dot flow-dot--fwd"
                      v-for="n in 2"
                      :key="`f3${n}`"
                      :style="{ '--i': n - 1 }"
                    ></span>
                    <span
                      class="flow-dot flow-dot--back"
                      v-for="n in 2"
                      :key="`b3${n}`"
                      :style="{ '--i': n - 1 }"
                    ></span>
                  </div>

                  <div class="flow-node">
                    <span class="flow-icon"><Terminal :size="22" /></span>
                    <span class="flow-name">工具沙盒</span>
                  </div>

                  <div class="flow-link" aria-hidden="true">
                    <span class="flow-rail"></span>
                    <span
                      class="flow-dot flow-dot--fwd"
                      v-for="n in 2"
                      :key="`f5${n}`"
                      :style="{ '--i': n - 1 }"
                    ></span>
                    <span
                      class="flow-dot flow-dot--back"
                      v-for="n in 2"
                      :key="`b5${n}`"
                      :style="{ '--i': n - 1 }"
                    ></span>
                  </div>

                  <div class="flow-node">
                    <span class="flow-icon"><Puzzle :size="22" /></span>
                    <span class="flow-name">技能 Skills</span>
                  </div>
                </div>

                <p class="flow-caption">智能体发起检索 · 引擎融合向量与图谱 · 召回知识增强生成</p>
              </div>

              <div class="stat-row" v-if="realtimeStats.length">
                <div class="stat-item" v-for="stat in realtimeStats" :key="stat.key">
                  <span class="stat-item-value">
                    <component :is="stat.icon" :size="15" />
                    {{ stat.value }}
                  </span>
                  <span class="stat-item-label">{{ stat.label }}</span>
                </div>
              </div>
            </div>
          </aside>
        </div>
      </main>

      <footer class="footer">
        <div class="footer-content">
          <p class="copyright">
            {{ infoStore.footer?.copyright || '© 2025 All rights reserved' }}
          </p>
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
import { healthApi } from '@/apis/system_api'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import {
  ArrowRight,
  Workflow,
  Library,
  Sparkles,
  User,
  Brain,
  Terminal,
  Puzzle
} from 'lucide-vue-next'

const router = useRouter()
const userStore = useUserStore()
const infoStore = useInfoStore()
const repoUrl = 'https://github.com/xerrors/Yuxi'
const faqUrl = 'https://xerrors.github.io/Yuxi/'

// 加载状态
const isLoading = ref(true)
const error = ref(null)
const typedBadge = ref('')
const isBadgeTyping = ref(false)
let badgeTimer = null
let subtitleTimer = null

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

const getHeroBadgeText = () => {
  return '在“智”的道路上稳步前行'
}

const stopBadgeTyping = () => {
  if (badgeTimer) {
    clearInterval(badgeTimer)
    badgeTimer = null
  }
  isBadgeTyping.value = false
}

const startBadgeTyping = () => {
  stopBadgeTyping()
  const text = getHeroBadgeText()
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
    startBadgeTyping()
  } catch (e) {
    console.error('加载失败:', e)
    stopBadgeTyping()
    stopSubtitleCarousel()
    typedBadge.value = ''
  } finally {
    isLoading.value = false
  }
}

const retryLoad = () => {
  loadData()
}

const goToChat = async () => {
  if (!userStore.isLoggedIn) {
    sessionStorage.setItem('redirect', '/')
    router.push('/login')
    return
  }

  router.push('/agent')
}

onMounted(() => {
  // 加载数据
  loadData()
})

onUnmounted(() => {
  stopBadgeTyping()
  stopSubtitleCarousel()
})

// 首页不再展示统计数据
const realtimeStats = computed(() => {
  return []
})
</script>

<style lang="less" scoped>
.home-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  color: var(--main-900);
  background: var(--main-5);
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

// 氛围装饰背景
.ambient {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
}

.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(70px);
  will-change: transform;
}

.orb-1 {
  width: 440px;
  height: 440px;
  top: -140px;
  right: -90px;
  background: var(--main-100);
  opacity: 0.55;
  animation: orbFloat 18s ease-in-out infinite;
}

.orb-2 {
  width: 380px;
  height: 380px;
  bottom: -160px;
  left: -120px;
  background: var(--main-200);
  opacity: 0.4;
  animation: orbFloat 22s ease-in-out infinite reverse;
}

.orb-3 {
  width: 300px;
  height: 300px;
  top: 32%;
  left: 52%;
  background: var(--main-50);
  opacity: 0.6;
  animation: orbFloat 26s ease-in-out infinite;
}

.grid-mesh {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(to right, var(--main-40) 1px, transparent 1px),
    linear-gradient(to bottom, var(--main-40) 1px, transparent 1px);
  background-size: 60px 60px;
  opacity: 0.7;
  -webkit-mask-image: radial-gradient(ellipse 75% 55% at 50% 8%, #000, transparent 72%);
  mask-image: radial-gradient(ellipse 75% 55% at 50% 8%, #000, transparent 72%);
}

// 顶部导航
.glass-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 0.85rem 2.5rem;
  background-color: var(--color-trans-light);
  backdrop-filter: blur(20px);
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  border-bottom: 1px solid var(--main-40);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.logo {
  display: flex;
  align-items: center;
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

.github-link {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  text-decoration: none;
  color: var(--gray-600);
  border: 1px solid transparent;
  transition:
    color 0.2s ease,
    background 0.2s ease,
    border-color 0.2s ease;

  &:hover {
    color: var(--main-700);
    background: var(--main-30);
    border-color: var(--main-40);
  }

  svg {
    fill: currentColor;
  }
}

// Hero
.hero-section {
  position: relative;
  z-index: 1;
  flex: 1;
  width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 7rem 2rem 3rem;
}

.hero-layout {
  display: grid;
  grid-template-columns: 1.05fr 0.95fr;
  gap: 3rem;
  align-items: start;
  width: 100%;
  max-width: 1180px;
  margin: 0 auto;
}

.hero-content {
  display: flex;
  flex-direction: column;
  gap: 1.4rem;
  padding-top: 0.5rem;
}

.reveal-up {
  opacity: 0;
  transform: translateY(16px);
  animation: revealUp 0.7s cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

.reveal-up.delay-1 {
  animation-delay: 110ms;
}

.reveal-up.delay-2 {
  animation-delay: 220ms;
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  align-self: flex-start;
  padding: 0.4rem 0.9rem;
  border-radius: 999px;
  background: var(--main-0);
  border: 1px solid var(--main-40);
  color: var(--main-700);
  font-size: 0.85rem;
  letter-spacing: 0.02em;
  font-weight: 600;
  margin: 0;
  box-shadow: 0 4px 14px -8px rgba(3, 80, 101, 0.4);
}

.badge-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--main-500);
  box-shadow: 0 0 0 4px var(--main-50);
  flex-shrink: 0;
}

.hero-badge-link {
  color: inherit;
  text-decoration: none;
}

.hero-badge-number {
  color: var(--main-700);
  font-weight: 700;
  transition: color 0.2s ease;
}

.hero-badge-link:hover .hero-badge-number {
  color: var(--main-800);
}

.hero-badge.typing::after {
  content: '';
  display: inline-block;
  width: 1px;
  height: 1em;
  margin-left: 2px;
  background: var(--main-600);
  vertical-align: -0.1em;
  animation: caretBlink 0.8s steps(1, end) infinite;
}

.title {
  font-size: clamp(2.6rem, 4.4vw, 4.2rem);
  font-weight: 800;
  margin: 0;
  background: linear-gradient(120deg, var(--main-900) 10%, var(--main-600) 60%, var(--main-500));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  letter-spacing: -0.02em;
  line-height: 1.08;
}

.subtitle {
  font-size: 1.45rem;
  font-weight: 600;
  color: var(--gray-700);
  line-height: 1.45;
  margin: 0;
  min-height: calc(1.45em * 1.3);
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

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 1.25rem;
  align-items: center;
  margin-top: 3.5rem;
}

.button-base {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.5rem 2rem;
  border-radius: 999px;
  font-size: 1.05rem;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  text-decoration: none;
  transition:
    background 0.25s ease,
    box-shadow 0.25s ease;
  min-height: 52px;
}

.button-base.primary {
  background: linear-gradient(135deg, var(--main-600), var(--main-500));
  color: var(--gray-0);
  box-shadow: 0 12px 28px -12px rgba(3, 80, 101, 0.55);

  :deep(svg) {
    transition: transform 0.25s ease;
  }

  &:hover {
    background: linear-gradient(135deg, var(--main-700), var(--main-600));
    box-shadow: 0 16px 34px -12px rgba(3, 80, 101, 0.6);

    :deep(svg) {
      transform: translateX(3px);
    }
  }
}

.button-base.secondary {
  background: var(--main-0);
  color: var(--main-700);
  border-color: var(--main-40);
  padding: 0.5rem 1.6rem;

  :deep(svg) {
    color: var(--main-600);
  }

  &:hover {
    background: var(--main-30);
    border-color: var(--main-200);
    color: var(--main-800);
  }
}

// Hero 右侧可视化卡片
.hero-visual {
  display: flex;
  justify-content: center;
}

.visual-card {
  position: relative;
  width: 100%;
  max-width: 460px;
  padding: 1.75rem;
  border-radius: 24px;
  background: linear-gradient(165deg, var(--main-0), var(--main-20));
  border: 1px solid var(--main-40);
  box-shadow: 0 30px 60px -34px rgba(3, 80, 101, 0.35);
  overflow: hidden;
}

.visual-glow {
  position: absolute;
  top: -40%;
  right: -20%;
  width: 70%;
  height: 70%;
  background: radial-gradient(circle, var(--main-100), transparent 70%);
  opacity: 0.7;
  pointer-events: none;
}

.graph-watermark {
  position: absolute;
  top: -26px;
  right: -26px;
  width: 200px;
  height: auto;
  color: var(--main-500);
  opacity: 0.09;
  pointer-events: none;
}

// Harness → RAG 引擎 → 知识库 横向数据流
.flow-diagram {
  position: relative;
  z-index: 1;
}

.flow-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.flow-row--second {
  margin-top: 0;
}

/* 竖向连线：复刻横向 flow-link 的轨道 + 流动圆点，方向改为上下 */
.flow-vlink {
  position: relative;
  width: 54px;
  height: 36px;
  margin: 0 auto;
}

.flow-vrail {
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 50%;
  width: 2px;
  transform: translateX(-50%);
  border-radius: 2px;
  background: linear-gradient(
    180deg,
    var(--main-50),
    var(--main-200) 25%,
    var(--main-200) 75%,
    var(--main-50)
  );
}

.flow-dot--down {
  left: calc(50% - 5px);
  background: var(--main-500);
  box-shadow: 0 0 0 4px var(--main-50);
  animation: flowDown 2.4s linear infinite;
  animation-delay: calc(var(--i) * 1.2s);
}

.flow-dot--up {
  left: calc(50% + 5px);
  transform: translateX(-100%);
  background: var(--main-300);
  box-shadow: 0 0 0 4px var(--main-30);
  animation: flowUp 2.4s linear infinite;
  animation-delay: calc(var(--i) * 1.2s + 0.6s);
}

/* 中间排：左竖线 / 中央LLM / 右竖线，与上下排三列对齐 */
.flow-mid {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.flow-vcol {
  width: 76px;
  flex-shrink: 0;
  display: flex;
  justify-content: center;
}

/* 中央 LLM 居中 */
.flow-center {
  flex: 1;
  display: flex;
  justify-content: center;
}

.flow-icon--hub.flow-icon--llm {
  width: 150px;
  height: 60px;
  border-radius: 26px;
}

/* 中央 LLM 节点不受普通节点 76px 宽度限制 */
.flow-center .flow-node {
  width: auto;
}

.flow-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.55rem;
  flex-shrink: 0;
  width: 76px;
  text-align: center;
}

.flow-icon {
  width: 54px;
  height: 54px;
  border-radius: 16px;
  background: var(--main-30);
  border: 1px solid var(--main-40);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition:
    background 0.2s ease,
    border-color 0.2s ease;

  :deep(svg) {
    color: var(--main-700);
  }
}

.flow-node:hover .flow-icon {
  background: var(--main-100);
  border-color: var(--main-200);
}

.flow-name {
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--main-800);
  line-height: 1.3;
}

// 中间枢纽：主色高亮 + 脉冲环
.flow-icon--hub {
  position: relative;
  width: 60px;
  height: 60px;
  border-radius: 18px;
  background: linear-gradient(140deg, var(--main-500), var(--main-600));
  border: none;
  box-shadow: 0 10px 22px -10px rgba(3, 80, 101, 0.55);

  :deep(svg) {
    color: var(--gray-0);
    position: relative;
    z-index: 1;
  }
}

.flow-node--hub:hover .flow-icon--hub {
  background: linear-gradient(140deg, var(--main-500), var(--main-600));
}

.hub-ring {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  border: 2px solid var(--main-400);
  animation: hubPulse 2.4s ease-out infinite;
}

.flow-link {
  position: relative;
  flex: 1;
  height: 54px;
  min-width: 0;
}

.flow-rail {
  position: absolute;
  left: 4px;
  right: 4px;
  top: 50%;
  height: 2px;
  transform: translateY(-50%);
  border-radius: 2px;
  background: linear-gradient(
    90deg,
    var(--main-50),
    var(--main-200) 25%,
    var(--main-200) 75%,
    var(--main-50)
  );
}

.flow-dot {
  position: absolute;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.flow-dot--fwd {
  top: calc(50% - 5px);
  background: var(--main-500);
  box-shadow: 0 0 0 4px var(--main-50);
  animation: flowRight 2.4s linear infinite;
  animation-delay: calc(var(--i) * 1.2s);
}

.flow-dot--back {
  top: calc(50% + 5px);
  transform: translateY(-100%);
  background: var(--main-300);
  box-shadow: 0 0 0 4px var(--main-30);
  animation: flowLeft 2.4s linear infinite;
  animation-delay: calc(var(--i) * 1.2s + 0.6s);
}

.flow-caption {
  margin: 1.25rem 0 0;
  text-align: center;
  font-size: 0.84rem;
  color: var(--gray-600);
  line-height: 1.5;
}

.stat-row {
  position: relative;
  display: flex;
  margin-top: 1.5rem;
  padding-top: 1.35rem;
  border-top: 1px solid var(--main-40);
}

.stat-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;

  &:not(:first-child) {
    padding-left: 1.2rem;
  }

  &:not(:last-child) {
    padding-right: 1.2rem;
    border-right: 1px solid var(--main-40);
  }
}

.stat-item-value {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 1.3rem;
  font-weight: 700;
  color: var(--main-800);
  line-height: 1.1;

  :deep(svg) {
    color: var(--main-500);
  }
}

.stat-item-label {
  font-size: 0.8rem;
  color: var(--gray-600);
}

// 页脚
.footer {
  position: relative;
  z-index: 1;
  margin-top: auto;
  border-top: 1px solid var(--main-40);
}

.footer-content {
  text-align: center;
  padding: 1.75rem 2rem;
  max-width: 1180px;
  margin: 0 auto;
}

.copyright {
  color: var(--main-700);
  font-size: 0.9rem;
  font-weight: 500;
  margin: 0;
  opacity: 0.75;
}

@keyframes revealUp {
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

@keyframes orbFloat {
  0%,
  100% {
    transform: translate(0, 0) scale(1);
  }
  50% {
    transform: translate(0, -26px) scale(1.04);
  }
}

@keyframes flowRight {
  0% {
    left: -4px;
    opacity: 0;
  }
  15% {
    opacity: 1;
  }
  85% {
    opacity: 1;
  }
  100% {
    left: calc(100% - 4px);
    opacity: 0;
  }
}

@keyframes flowLeft {
  0% {
    left: calc(100% - 4px);
    opacity: 0;
  }
  15% {
    opacity: 1;
  }
  85% {
    opacity: 1;
  }
  100% {
    left: -4px;
    opacity: 0;
  }
}

@keyframes flowDown {
  0% {
    top: -4px;
    opacity: 0;
  }
  15% {
    opacity: 1;
  }
  85% {
    opacity: 1;
  }
  100% {
    top: calc(100% - 4px);
    opacity: 0;
  }
}

@keyframes flowUp {
  0% {
    top: calc(100% - 4px);
    opacity: 0;
  }
  15% {
    opacity: 1;
  }
  85% {
    opacity: 1;
  }
  100% {
    top: -4px;
    opacity: 0;
  }
}

@keyframes hubPulse {
  0% {
    opacity: 0.6;
    transform: scale(1);
  }
  70%,
  100% {
    opacity: 0;
    transform: scale(1.4);
  }
}

// 暗色模式
:global(:root.dark) {
  .home-container {
    background: var(--main-5);
  }

  .hero-badge-number {
    color: var(--main-200);
  }

  .hero-badge-link:hover .hero-badge-number {
    color: var(--main-100);
  }

  .button-base.secondary {
    color: var(--main-200);

    :deep(svg) {
      color: var(--main-300);
    }

    &:hover {
      color: var(--main-100);
    }
  }

  .github-link {
    color: var(--gray-400);

    &:hover {
      color: var(--main-200);
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .reveal-up,
  .orb,
  .hero-badge.typing::after {
    animation: none;
  }

  .reveal-up {
    opacity: 1;
    transform: none;
  }

  .flow-dot,
  .hub-ring {
    display: none;
  }

  .subtitle-switch-enter-active,
  .subtitle-switch-leave-active {
    transition: none;
  }
}

@media (max-width: 960px) {
  .hero-layout {
    grid-template-columns: 1fr;
    gap: 2.5rem;
  }

  .hero-content {
    align-items: flex-start;
    text-align: left;
  }

  .visual-card {
    max-width: 520px;
    margin: 0 auto;
  }
}

@media (max-width: 768px) {
  .glass-header {
    padding: 0.75rem 1.25rem;
  }

  .logo-text {
    font-size: 1.15rem;
  }

  .hero-section {
    padding: 6rem 1.25rem 2.5rem;
  }

  .title {
    font-size: clamp(2.2rem, 9vw, 3rem);
  }

  .subtitle {
    font-size: 1.2rem;
  }

  .button-base {
    width: 100%;
  }
}
</style>
