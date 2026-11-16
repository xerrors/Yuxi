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
          <a-button :href="docsUrl" target="_blank" rel="noopener noreferrer">常见问题</a-button>
        </template>
      </a-result>
    </div>

    <!-- 正常内容 -->
    <template v-else>
      <!-- 氛围装饰背景 -->
      <div class="ambient" aria-hidden="true">
        <span class="glow"></span>
        <span class="glow-accent"></span>
        <svg
          class="constellation"
          viewBox="0 0 1440 900"
          preserveAspectRatio="xMidYMid slice"
          xmlns="http://www.w3.org/2000/svg"
        >
          <!-- 左翼星群 -->
          <g class="drift drift-a">
            <g class="edges">
              <line x1="120" y1="180" x2="240" y2="120" />
              <line x1="120" y1="180" x2="90" y2="420" />
              <line x1="240" y1="120" x2="320" y2="300" />
              <line x1="90" y1="420" x2="210" y2="540" />
              <line x1="90" y1="420" x2="60" y2="600" />
              <line x1="210" y1="540" x2="150" y2="720" />
              <line x1="60" y1="600" x2="150" y2="720" />
              <line x1="320" y1="300" x2="90" y2="420" />
            </g>
            <circle class="leaf" cx="240" cy="120" r="3" />
            <circle class="leaf" cx="320" cy="300" r="2.5" />
            <circle class="leaf" cx="90" cy="420" r="4" />
            <circle class="leaf" cx="210" cy="540" r="3" />
            <circle class="leaf" cx="60" cy="600" r="2.5" />
            <circle class="pulse-ring" cx="120" cy="180" r="6" />
            <circle class="hub" cx="120" cy="180" r="6" />
            <circle class="hub" cx="150" cy="720" r="5" />
          </g>

          <!-- 右翼星群 -->
          <g class="drift drift-b">
            <g class="edges">
              <line x1="1320" y1="200" x2="1200" y2="140" />
              <line x1="1320" y1="200" x2="1350" y2="440" />
              <line x1="1200" y1="140" x2="1120" y2="320" />
              <line x1="1350" y1="440" x2="1230" y2="560" />
              <line x1="1350" y1="440" x2="1380" y2="620" />
              <line x1="1230" y1="560" x2="1300" y2="740" />
              <line x1="1380" y1="620" x2="1300" y2="740" />
              <line x1="1120" y1="320" x2="1350" y2="440" />
            </g>
            <circle class="leaf" cx="1200" cy="140" r="3" />
            <circle class="leaf" cx="1120" cy="320" r="2.5" />
            <circle class="leaf" cx="1350" cy="440" r="4" />
            <circle class="leaf" cx="1230" cy="560" r="3" />
            <circle class="leaf" cx="1380" cy="620" r="2.5" />
            <circle class="pulse-ring" cx="1320" cy="200" r="6" />
            <circle class="hub" cx="1320" cy="200" r="6" />
            <circle class="hub" cx="1300" cy="740" r="5" />
          </g>

          <!-- 上下稀疏星群 -->
          <g class="drift drift-c">
            <g class="edges">
              <line x1="560" y1="120" x2="720" y2="190" />
              <line x1="720" y1="190" x2="880" y2="100" />
              <line x1="520" y1="780" x2="710" y2="850" />
              <line x1="710" y1="850" x2="900" y2="790" />
              <line x1="420" y1="480" x2="320" y2="300" />
              <line x1="1020" y1="470" x2="1120" y2="320" />
            </g>
            <circle class="leaf" cx="560" cy="120" r="3" />
            <circle class="leaf" cx="880" cy="100" r="3" />
            <circle class="leaf" cx="720" cy="190" r="2.5" />
            <circle class="leaf" cx="520" cy="780" r="3" />
            <circle class="leaf" cx="900" cy="790" r="3" />
            <circle class="leaf" cx="710" cy="850" r="2.5" />
            <circle class="leaf" cx="420" cy="480" r="2" />
            <circle class="leaf" cx="1020" cy="470" r="2" />
          </g>

          <!-- 信号流：知识在节点间流动 -->
          <g class="signals">
            <path class="signal" d="M120 180 L240 120 L320 300" />
            <path class="signal signal-late" d="M1320 200 L1350 440 L1230 560" />
            <path class="signal signal-slow" d="M560 120 L720 190 L880 100" />
            <path class="signal signal-slower" d="M90 420 L210 540 L150 720" />
            <path class="signal signal-late signal-slow" d="M1350 440 L1380 620 L1300 740" />
            <path class="signal signal-slower signal-late" d="M520 780 L710 850 L900 790" />
          </g>
        </svg>
      </div>

      <header class="site-header">
        <div class="logo">
          <img
            :src="infoStore.organization.logo"
            :alt="infoStore.organization.name"
            class="logo-img"
          />
          <span class="logo-text">{{ infoStore.organization.name }}</span>
        </div>
        <div class="header-actions">
          <a
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
          </a>
          <UserInfoComponent :show-button="true" />
        </div>
      </header>

      <main class="hero-section">
        <span class="hero-vignette" aria-hidden="true"></span>
        <div class="hero-content">
          <p class="hero-eyebrow reveal-up">
            <span class="eyebrow-dot"></span>开源 · 知识库 × 智能体 Harness
          </p>
          <h1 class="title reveal-up delay-1">{{ infoStore.branding.title }}</h1>
          <div class="subtitle-wrap reveal-up delay-1">
            <Transition name="subtitle-switch">
              <p v-if="currentSubtitle" class="subtitle" :key="currentSubtitle">
                {{ currentSubtitle }}
              </p>
            </Transition>
          </div>
          <div class="hero-actions reveal-up delay-2">
            <button class="button-base primary" @click="goToChat">
              <span>开始体验</span>
              <ArrowRight :size="18" />
            </button>
            <a
              class="button-base secondary"
              :href="docsUrl"
              target="_blank"
              rel="noopener noreferrer"
            >
              <BookText :size="18" />
              <span>查看文档</span>
            </a>
          </div>
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
import { ArrowRight, BookText } from '@lucide/vue'

const router = useRouter()
const userStore = useUserStore()
const infoStore = useInfoStore()
const docsUrl = 'https://xerrors.github.io/Yuxi/'

// 加载状态
const isLoading = ref(true)
const error = ref(null)
const subtitleIndex = ref(0)
let subtitleTimer = null

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
  } catch (e) {
    console.error('加载失败:', e)
    stopSubtitleCarousel()
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
  loadData()
})

onUnmounted(() => {
  stopSubtitleCarousel()
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

  // 整页缓慢流动的浅色渐变层，使用极浅 token 保持克制
  &::before {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(
      115deg,
      var(--main-30) 0%,
      var(--main-50) 28%,
      var(--second-50) 52%,
      var(--main-50) 76%,
      var(--main-30) 100%
    );
    background-size: 300% 300%;
    opacity: 0.35;
    animation: bgFlow 36s ease-in-out infinite alternate;
  }
}

.glow {
  position: absolute;
  top: -260px;
  left: 50%;
  transform: translateX(-50%);
  width: 920px;
  height: 600px;
  border-radius: 50%;
  background: radial-gradient(closest-side, var(--main-50), transparent);
  opacity: 0.7;
  animation: glowDrift 26s ease-in-out infinite alternate;
}

// 辅助色光晕：暖金流光从右下呼应信号流
.glow-accent {
  position: absolute;
  bottom: -280px;
  right: -160px;
  width: 760px;
  height: 520px;
  border-radius: 50%;
  background: radial-gradient(closest-side, var(--second-50), transparent);
  opacity: 0.4;
  animation: glowDriftAccent 34s ease-in-out infinite alternate;
}

// 知识星图
.constellation {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.edges line {
  stroke: var(--main-200);
  stroke-width: 1;
  opacity: 0.55;
}

.leaf {
  fill: var(--main-300);
  opacity: 0.85;
}

.hub {
  fill: var(--main-500);
}

.pulse-ring {
  fill: none;
  stroke: var(--main-400);
  stroke-width: 1.2;
  transform-box: fill-box;
  transform-origin: center;
  animation: nodePulse 3s ease-out infinite;
}

.signals .signal {
  fill: none;
  stroke: var(--second-500);
  stroke-width: 1.4;
  stroke-linecap: round;
  stroke-dasharray: 6 140;
  opacity: 0.8;
  animation: signalFlow 4.5s linear infinite;
}

.signal-late {
  animation-delay: 1.6s;
}

.signal-slow {
  animation-duration: 6s;
  animation-delay: 0.8s;
}

.drift-a {
  animation: driftA 26s ease-in-out infinite alternate;
}

.drift-b {
  animation: driftB 30s ease-in-out infinite alternate;
}

.drift-c {
  animation: driftC 34s ease-in-out infinite alternate;
}

// 顶部导航：无背景无边框，融入页面
.site-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 0.85rem 2.5rem;
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
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
  padding: 4rem 2rem 8.5rem;
}

// 文字背后的柔光衬底，保证星图之上可读性
.hero-vignette {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: min(1100px, 92vw);
  height: min(560px, 72vh);
  background: radial-gradient(closest-side, var(--main-5) 30%, transparent);
  pointer-events: none;
}

.hero-content {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 1.6rem;
  max-width: 1200px;
  margin: 0 auto;
}

.reveal-up {
  opacity: 0;
  transform: translateY(16px);
  animation: revealUp 0.7s cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

.reveal-up.delay-1 {
  animation-delay: 120ms;
}

.reveal-up.delay-2 {
  animation-delay: 240ms;
}

.reveal-up.delay-3 {
  animation-delay: 380ms;
}

.hero-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0;
  padding: 0.42rem 1.05rem;
  border-radius: 999px;
  background: var(--main-0);
  border: 1px solid var(--main-40);
  color: var(--main-700);
  font-size: 0.84rem;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.eyebrow-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--second-500);
  box-shadow: 0 0 0 3px var(--second-50);
}

.title {
  font-size: clamp(2.4rem, 4.2vw, 4rem);
  font-weight: 800;
  margin: 0;
  background: linear-gradient(120deg, var(--main-900) 10%, var(--main-600) 60%, var(--main-500));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  letter-spacing: -0.03em;
  line-height: 1.12;
}

// 交叉淡入淡出容器：离开的旧文案绝对定位，避免布局跳动
.subtitle-wrap {
  position: relative;
  width: 100%;
  min-height: calc(1.35em * 1.3);
}

.subtitle {
  font-size: 1.35rem;
  font-weight: 600;
  color: var(--gray-700);
  line-height: 1.5;
  margin: 0;
}

.subtitle-switch-enter-active,
.subtitle-switch-leave-active {
  transition:
    opacity 0.55s ease,
    transform 0.55s ease;
}

.subtitle-switch-leave-active {
  position: absolute;
  inset: 0;
}

.subtitle-switch-enter-from {
  opacity: 0;
  transform: translateY(5px);
}

.subtitle-switch-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 1.25rem;
  align-items: center;
  margin-top: 0.6rem;
}

.button-base {
  position: relative;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.5rem 2.2rem;
  border-radius: 999px;
  font-size: 1.05rem;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  text-decoration: none;
  transition:
    background 0.25s ease,
    border-color 0.25s ease,
    box-shadow 0.25s ease;
  min-height: 54px;
  min-width: 11rem;
}

.button-base.primary {
  background: linear-gradient(135deg, var(--main-600), var(--main-500));
  color: var(--gray-0);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    0 12px 28px -12px rgba(3, 80, 101, 0.55);

  // hover 时一道流光扫过
  &::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    width: 45%;
    background: linear-gradient(100deg, transparent, rgba(255, 255, 255, 0.32), transparent);
    transform: translateX(-160%) skewX(-18deg);
    transition: transform 0.7s ease;
    pointer-events: none;
  }

  :deep(svg) {
    transition: transform 0.25s ease;
  }

  &:hover {
    background: linear-gradient(135deg, var(--main-700), var(--main-600));
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.22),
      0 16px 34px -12px rgba(3, 80, 101, 0.6);

    &::after {
      transform: translateX(340%) skewX(-18deg);
    }

    :deep(svg) {
      transform: translateX(3px);
    }
  }
}

// 次按钮：玻璃质感，融入流光背景
.button-base.secondary {
  background: var(--color-trans-light);
  backdrop-filter: blur(8px);
  color: var(--main-700);
  border-color: var(--main-40);

  :deep(svg) {
    color: var(--main-600);
  }

  &:hover {
    background: var(--main-30);
    border-color: var(--main-200);
    color: var(--main-800);
  }
}

// 页脚
.footer {
  position: relative;
  z-index: 1;
  margin-top: auto;
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

@keyframes bgFlow {
  from {
    background-position: 0% 40%;
  }
  to {
    background-position: 100% 60%;
  }
}

@keyframes glowDrift {
  from {
    transform: translateX(-50%) translate(0, 0) scale(1);
  }
  to {
    transform: translateX(-50%) translate(90px, 60px) scale(1.15);
  }
}

@keyframes glowDriftAccent {
  from {
    transform: translate(0, 0) scale(1);
  }
  to {
    transform: translate(-100px, -70px) scale(1.18);
  }
}

@keyframes revealUp {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes nodePulse {
  0% {
    opacity: 0.7;
    transform: scale(1);
  }
  70%,
  100% {
    opacity: 0;
    transform: scale(2.4);
  }
}

@keyframes signalFlow {
  to {
    stroke-dashoffset: -146;
  }
}

@keyframes driftA {
  from {
    transform: translate(0, 0);
  }
  to {
    transform: translate(16px, -12px);
  }
}

@keyframes driftB {
  from {
    transform: translate(0, 0);
  }
  to {
    transform: translate(-18px, 10px);
  }
}

@keyframes driftC {
  from {
    transform: translate(0, 0);
  }
  to {
    transform: translate(10px, 14px);
  }
}

// 暗色模式：文字与边框颜色随 token 反转自动适配，只需给次按钮换深色玻璃底
// 注意：:global 包裹嵌套块会被 scoped 编译静默丢弃，必须用 :root.dark 直接嵌套；
// 暗色下 --light-*/--dark-* 名称互换，--dark-10 才是白色 10% 淡色
:root.dark {
  .button-base.secondary {
    background: var(--dark-10);

    &:hover {
      background: var(--dark-25);
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .reveal-up {
    opacity: 1;
    transform: none;
    animation: none;
  }

  .ambient::before,
  .drift,
  .pulse-ring,
  .signals .signal,
  .glow,
  .glow-accent {
    animation: none;
  }

  .signals {
    display: none;
  }

  .subtitle-switch-enter-active,
  .subtitle-switch-leave-active {
    transition: none;
  }
}
</style>
