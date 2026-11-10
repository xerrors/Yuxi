<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { withBase } from 'vitepress'

const GITHUB = 'https://github.com/xerrors/Yuxi'
const DEMO = 'https://www.bilibili.com/video/BV1erE26iEgv/'
const OSS_ASSET_BASE = 'https://xerrors.oss-cn-shanghai.aliyuncs.com/github/yuxi/docs/home'
const MASCOT_IMAGE = `${OSS_ASSET_BASE}/yuxi-mascot-cutout.png`

const docPaths = [
  {
    title: '第一次部署 Yuxi',
    desc: '准备 Docker、Cube 执行节点与模型 API，启动服务并完成首次登录。',
    link: '/intro/quick-start',
    action: '打开快速开始',
    character: `${OSS_ASSET_BASE}/characters/deploy-run.png`,
    characterWidth: 1221,
    characterHeight: 1289,
    pose: 'run',
    featured: true
  },
  {
    title: '让智能体使用知识',
    desc: '创建知识库、上传文档，并验证检索和知识图谱。',
    link: '/intro/knowledge-base',
    action: '学习知识库',
    character: `${OSS_ASSET_BASE}/characters/knowledge-think.png`,
    characterWidth: 1024,
    characterHeight: 1536,
    pose: 'think'
  },
  {
    title: '编排 Agent 能力',
    desc: '配置模型、Skills、MCP、工具与协作会话。',
    link: '/agents/agents-config',
    action: '进入智能体开发',
    character: `${OSS_ASSET_BASE}/characters/agent-confident.png`,
    characterWidth: 1145,
    characterHeight: 1374,
    pose: 'confident'
  },
  {
    title: '理解运行边界',
    desc: '查看 Run、Sandbox、文件系统和知识能力的真实链路。',
    link: '/mechanisms/',
    action: '阅读机制详解',
    character: `${OSS_ASSET_BASE}/characters/runtime-peek.png`,
    characterWidth: 1024,
    characterHeight: 1536,
    pose: 'peek'
  }
]

const productScenes = [
  {
    key: 'workbench',
    title: '统一智能体工作台',
    desc: '在会话中查看知识引用、执行状态、上下文用量、协作成员和交付文件。',
    image: `${OSS_ASSET_BASE}/screenshots/workbench.webp`
  },
  {
    key: 'knowledge',
    title: '知识库与可追溯 RAG',
    desc: '管理目录与文件，查看解析状态，并从检索测试、知识图谱和评估入口持续校准知识质量。',
    image: `${OSS_ASSET_BASE}/screenshots/knowledge.webp`
  },
  {
    key: 'multiagent',
    title: '多智能体与扩展能力',
    desc: '配置智能体与协作成员，并按任务使用 Skills、MCP 和工具。',
    image: `${OSS_ASSET_BASE}/screenshots/multiagent.webp`
  },
  {
    key: 'workspace',
    title: '沙盒工作区与产物',
    desc: '让任务结果落到持久 Workdir，在浏览器内预览文档、表格、图片、PDF 与网页。',
    image: `${OSS_ASSET_BASE}/screenshots/workspace.webp`
  }
]

const activeScene = ref(0)
const scenePanels = []
const sceneVisible = new Set()
let sceneObserver = null

const workflow = [
  { title: '接入知识', desc: '文档、向量检索与知识图谱保持可追溯来源。' },
  { title: '组合能力', desc: '模型、Skills、MCP、工具与协作能力按需装配。' },
  { title: '执行任务', desc: 'LangGraph 与异步 Worker 承载长任务和人工审批。' },
  { title: '交付结果', desc: '回答、引用与文件产物绑定明确的 Turn 和 Run。' }
]

const capabilityGroups = [
  { label: '知识', value: 'RAG、知识图谱、评估' },
  { label: '执行', value: 'LangGraph、ARQ、Sandbox' },
  { label: '扩展', value: 'Skills、MCP、会话协作' },
  { label: '治理', value: '用户、部门共享与 API Key' }
]

const providers = [
  { name: 'OpenAI', icon: '/home/providers/openai.svg' },
  { name: 'DeepSeek', icon: '/home/providers/deepseek.svg' },
  { name: '通义千问', icon: '/home/providers/bailian.svg' },
  { name: '智谱 AI', icon: '/home/providers/zhipu.svg' },
  { name: 'Moonshot', icon: '/home/providers/moonshot.svg' },
  { name: 'MiniMax', icon: '/home/providers/minimax.svg' },
  { name: 'SiliconFlow', icon: '/home/providers/siliconcloud.svg' },
  { name: 'OpenRouter', icon: '/home/providers/openrouter.svg' },
  { name: 'ModelScope', icon: '/home/providers/modelscope.svg' },
  { name: 'OpenCode', icon: '/home/providers/opencode.svg' },
  { name: '小米 MiMo', icon: '/home/providers/xiaomimimo.svg' }
]

const providersBottom = [...providers.slice(5), ...providers.slice(0, 5)]
const providerRows = [providers, providersBottom]
const providerSummary = `支持的模型供应商：${providers.map((provider) => provider.name).join('、')}`

/** 收集右侧面板的 DOM 引用，供滚动定位和可见性观察使用。 */
function registerScenePanel(element, index) {
  if (element) scenePanels[index] = element
  else delete scenePanels[index]
}

/** 点击步骤时把对应面板滚到视口中央；高亮仍由滚动位置决定。 */
function scrollToScene(index) {
  const panel = scenePanels[index]
  if (!panel) return

  // 平滑滚动途中会依次掠过后面的面板，若在此提前置为当前步骤，高亮会先跳过去再跳回来。
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  panel.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' })
}

// 面板进入视口中部判定带时接管当前步骤；当前步骤离开后交给带内仍可见的面板，避免高亮滞留在已滚出中线的截图上。
function observeScenePanels() {
  sceneObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const index = Number(entry.target.dataset.sceneIndex)
      if (Number.isNaN(index)) continue

      if (!entry.isIntersecting) {
        sceneVisible.delete(index)
        continue
      }

      sceneVisible.add(index)
      activeScene.value = index
    }

    if (sceneVisible.has(activeScene.value)) return

    const [fallback] = [...sceneVisible].sort((a, b) => a - b)
    if (fallback !== undefined) activeScene.value = fallback
  }, { rootMargin: '-42% 0px -42% 0px' })

  scenePanels.forEach((panel) => sceneObserver.observe(panel))
}

const revealObservers = new WeakMap()
const lazyImageObservers = new WeakMap()

// 进入视口后只播放一次，并在节点卸载时释放观察器。
const vReveal = {
  mounted(el) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    el.classList.add('yx-reveal')
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      el.classList.add('yx-reveal--visible')
      observer.disconnect()
      revealObservers.delete(el)
    }, { threshold: 0.14 })

    revealObservers.set(el, observer)
    observer.observe(el)
  },
  unmounted(el) {
    revealObservers.get(el)?.disconnect()
    revealObservers.delete(el)
  }
}

// 装饰图真正进入视口后才绑定 src，避免浏览器原生 lazy 预取整段大图。
const vLazyImage = {
  mounted(el, binding) {
    const load = () => {
      if (typeof binding.value === 'string' && binding.value) el.src = binding.value
    }
    if (!('IntersectionObserver' in window)) {
      load()
      return
    }

    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      load()
      observer.disconnect()
      lazyImageObservers.delete(el)
    })
    lazyImageObservers.set(el, observer)
    observer.observe(el)
  },
  unmounted(el) {
    lazyImageObservers.get(el)?.disconnect()
    lazyImageObservers.delete(el)
  }
}

const canvasRef = ref(null)
const LOGICAL_SIZE = 160
const LOOP_DURATION = 7200
const FRAME_INTERVAL = 1000 / 24
const TAU = Math.PI * 2

let animationFrame = 0
let context = null
let lastFrameAt = 0
let loopStartedAt = 0
let motionQuery = null
let themeObserver = null

/** 返回当前主题对应的像素场景色板。 */
function getPalette() {
  const dark = document.documentElement.classList.contains('dark')

  return dark
    ? {
        amber: '#f3ba32',
        amberDeep: '#d89420',
        paper: '#202824',
        structure: '#c4d0cb',
        soft: '#81958c',
        offset: '#101613'
      }
    : {
        amber: '#f3ba32',
        amberDeep: '#d89420',
        paper: '#fffdf8',
        structure: '#455c55',
        soft: '#879991',
        offset: '#d6ddd9'
      }
}

/** 绘制带透明度的整数像素矩形。 */
function drawRect(ctx, x, y, width, height, color, alpha = 1) {
  ctx.globalAlpha = alpha
  ctx.fillStyle = color
  ctx.fillRect(Math.round(x), Math.round(y), Math.round(width), Math.round(height))
  ctx.globalAlpha = 1
}

/** 绘制沿数据路径前进的硬边像素信号及尾迹。 */
function drawSignal(ctx, points, progress, palette) {
  const trail = [0, 0.045, 0.09]

  trail.forEach((offset, index) => {
    const point = pointOnPath(points, Math.max(0, progress - offset))
    const size = index === 0 ? 4 : 2
    drawRect(ctx, point[0], point[1], size, size, index === 0 ? palette.amberDeep : palette.amber, 1 - index * 0.28)
  })
}

/** 沿折线路径返回指定进度的像素坐标。 */
function pointOnPath(points, progress) {
  const segments = []
  let total = 0

  for (let index = 1; index < points.length; index += 1) {
    const from = points[index - 1]
    const to = points[index]
    const length = Math.abs(to[0] - from[0]) + Math.abs(to[1] - from[1])
    segments.push({ from, to, length })
    total += length
  }

  let distance = Math.max(0, Math.min(1, progress)) * total
  for (const segment of segments) {
    if (distance <= segment.length) {
      const ratio = segment.length ? distance / segment.length : 0
      return [
        Math.round(segment.from[0] + (segment.to[0] - segment.from[0]) * ratio),
        Math.round(segment.from[1] + (segment.to[1] - segment.from[1]) * ratio)
      ]
    }
    distance -= segment.length
  }

  return points.at(-1)
}

/** 绘制场景外围持续流动的像素回路。 */
function drawCircuit(ctx, phase, palette) {
  const track = []

  for (let x = 25; x <= 135; x += 4) track.push([x, 17])
  for (let y = 21; y <= 139; y += 4) track.push([139, y])
  for (let x = 135; x >= 25; x -= 4) track.push([x, 143])
  for (let y = 139; y >= 21; y -= 4) track.push([21, y])

  track.forEach(([x, y], index) => {
    if (index % 2 === 0) drawRect(ctx, x, y, 1, 1, palette.soft, 0.28)
  })

  const streams = [phase, (phase + 0.5) % 1]
  streams.forEach((stream, streamIndex) => {
    const head = Math.floor(stream * track.length)
    for (let trail = 0; trail < 5; trail += 1) {
      const point = track[(head - trail + track.length) % track.length]
      const size = trail === 0 ? 3 : 2
      drawRect(ctx, point[0], point[1], size, size, streamIndex === 0 ? palette.amber : palette.structure, 1 - trail * 0.17)
    }
  })
}

/** 绘制文档知识源。 */
function drawDocument(ctx, alpha, palette) {
  drawRect(ctx, 14, 36, 19, 23, palette.offset, alpha)
  drawRect(ctx, 11, 33, 19, 23, palette.structure, alpha)
  drawRect(ctx, 13, 35, 15, 19, palette.paper, alpha)
  drawRect(ctx, 23, 35, 5, 5, palette.offset, alpha)
  drawRect(ctx, 16, 42, 8, 1, palette.soft, alpha)
  drawRect(ctx, 16, 46, 10, 1, palette.soft, alpha)
  drawRect(ctx, 16, 50, 6, 1, palette.amberDeep, alpha)
}

/** 绘制知识图谱来源。 */
function drawGraph(ctx, alpha, palette) {
  ctx.globalAlpha = alpha
  ctx.strokeStyle = palette.soft
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(130, 31)
  ctx.lineTo(137, 31)
  ctx.lineTo(137, 38)
  ctx.lineTo(144, 38)
  ctx.lineTo(144, 45)
  ctx.lineTo(151, 45)
  ctx.stroke()
  ctx.globalAlpha = 1
  drawRect(ctx, 127, 28, 7, 7, palette.structure, alpha)
  drawRect(ctx, 129, 30, 3, 3, palette.paper, alpha)
  drawRect(ctx, 134, 35, 7, 7, palette.soft, alpha)
  drawRect(ctx, 136, 37, 3, 3, palette.paper, alpha)
  drawRect(ctx, 141, 42, 7, 7, palette.structure, alpha)
  drawRect(ctx, 143, 44, 3, 3, palette.paper, alpha)
  drawRect(ctx, 149, 44, 6, 6, palette.amberDeep, alpha)
}

/** 绘制记忆来源。 */
function drawMemory(ctx, alpha, palette) {
  drawRect(ctx, 15, 119, 23, 13, palette.offset, alpha)
  drawRect(ctx, 12, 116, 23, 13, palette.structure, alpha)
  drawRect(ctx, 14, 118, 19, 9, palette.paper, alpha)
  drawRect(ctx, 18, 122, 10, 2, palette.amberDeep, alpha)
  drawRect(ctx, 30, 119, 2, 2, palette.structure, alpha)
}

/** 绘制带完成状态的交付物。 */
function drawResult(ctx, alpha, palette) {
  drawRect(ctx, 133, 115, 21, 26, palette.offset, alpha)
  drawRect(ctx, 130, 112, 21, 26, palette.structure, alpha)
  drawRect(ctx, 132, 114, 17, 22, palette.paper, alpha)
  drawRect(ctx, 143, 114, 6, 6, palette.offset, alpha)
  drawRect(ctx, 135, 120, 8, 8, palette.structure, alpha)
  drawRect(ctx, 137, 123, 2, 2, palette.paper, alpha)
  drawRect(ctx, 139, 121, 2, 4, palette.paper, alpha)
  drawRect(ctx, 135, 131, 10, 1, palette.soft, alpha)
}

/** 绘制单帧知识循环场景。 */
function drawScene(phase) {
  if (!context) return

  const palette = getPalette()
  context.clearRect(0, 0, LOGICAL_SIZE, LOGICAL_SIZE)
  context.imageSmoothingEnabled = false

  drawCircuit(context, phase, palette)
  const documentPulse = 0.48 + Math.max(0, Math.sin((phase + 0.04) * TAU)) * 0.52
  const graphPulse = 0.48 + Math.max(0, Math.sin((phase - 0.08) * TAU)) * 0.52
  const memoryPulse = 0.48 + Math.max(0, Math.sin((phase - 0.2) * TAU)) * 0.52
  const resultPulse = 0.48 + Math.max(0, Math.sin((phase - 0.58) * TAU)) * 0.52

  context.save()
  context.translate(0, -Math.round(Math.sin((phase + 0.04) * TAU) * 2))
  drawDocument(context, documentPulse, palette)
  context.restore()
  context.save()
  context.translate(0, -Math.round(Math.sin((phase - 0.08) * TAU) * 2))
  drawGraph(context, graphPulse, palette)
  context.restore()
  context.save()
  context.translate(0, -Math.round(Math.sin((phase - 0.2) * TAU) * 2))
  drawMemory(context, memoryPulse, palette)
  context.restore()
  context.save()
  context.translate(0, -Math.round(Math.sin((phase - 0.58) * TAU) * 2))
  drawResult(context, resultPulse, palette)
  context.restore()

  if (phase >= 0.12 && phase <= 0.42) {
    drawSignal(context, [[31, 55], [41, 55], [41, 66], [53, 66]], (phase - 0.12) / 0.3, palette)
  }

  if (phase >= 0.48 && phase <= 0.76) {
    drawSignal(context, [[111, 91], [123, 91], [123, 108], [133, 108]], (phase - 0.48) / 0.28, palette)
  }

  const insight = phase >= 0.32 && phase <= 0.62
    ? Math.sin(((phase - 0.32) / 0.3) * Math.PI)
    : 0
  drawRect(context, 78, 6, 4, 11, palette.amberDeep, insight)
  drawRect(context, 74, 10, 12, 4, palette.amberDeep, insight)
  drawRect(context, 89, 11, 3, 3, palette.amber, insight)
  drawRect(context, 68, 7, 3, 3, palette.amber, insight)
  drawRect(context, 93, 17, 2, 2, palette.structure, insight)
}

/** 以固定像素帧率推进循环。 */
function renderLoop(timestamp) {
  if (!loopStartedAt) loopStartedAt = timestamp
  if (timestamp - lastFrameAt >= FRAME_INTERVAL) {
    drawScene(((timestamp - loopStartedAt) % LOOP_DURATION) / LOOP_DURATION)
    // 保留帧间余数，避免 60 Hz 屏幕把 24 FPS 截成 20 FPS。
    lastFrameAt = timestamp - ((timestamp - lastFrameAt) % FRAME_INTERVAL)
  }
  animationFrame = window.requestAnimationFrame(renderLoop)
}

/** 根据系统动效偏好启动动态或静态场景。 */
function restartRendering() {
  window.cancelAnimationFrame(animationFrame)
  animationFrame = 0
  lastFrameAt = 0
  loopStartedAt = 0

  if (motionQuery?.matches) {
    drawScene(0.72)
    return
  }

  animationFrame = window.requestAnimationFrame(renderLoop)
}

/** 在主题变化时刷新静态降级帧。 */
function handleThemeChange() {
  if (motionQuery?.matches) drawScene(0.72)
}

/** 在页面重新可见时恢复动画循环。 */
function handleVisibilityChange() {
  if (document.hidden) {
    window.cancelAnimationFrame(animationFrame)
    animationFrame = 0
    return
  }
  restartRendering()
}

onMounted(() => {
  observeScenePanels()

  const canvas = canvasRef.value
  if (!canvas) return

  canvas.width = LOGICAL_SIZE
  canvas.height = LOGICAL_SIZE
  context = canvas.getContext('2d', { alpha: true })
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionQuery.addEventListener('change', restartRendering)
  document.addEventListener('visibilitychange', handleVisibilityChange)
  themeObserver = new MutationObserver(handleThemeChange)
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  restartRendering()
})

onBeforeUnmount(() => {
  window.cancelAnimationFrame(animationFrame)
  sceneObserver?.disconnect()
  sceneObserver = null
  motionQuery?.removeEventListener('change', restartRendering)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  themeObserver?.disconnect()
})
</script>

<template>
  <div class="yx-home">
    <section class="yx-hero">
      <div class="yx-shell yx-hero__grid">
        <div class="yx-hero__copy">
          <div class="yx-lockup">
            <img
              class="yx-lockup__light"
              :src="withBase('/home/yuxi-lockup-on-light.svg')"
              alt="Yuxi"
              width="240"
              height="79"
            >
            <img
              class="yx-lockup__dark"
              :src="withBase('/home/yuxi-lockup-on-dark.svg')"
              alt="Yuxi"
              width="240"
              height="79"
            >
          </div>
          <h1>让知识真正参与<br>每一次行动</h1>
          <p>把团队知识、工具与多智能体执行接进一个可私有部署的工作台。</p>
          <div class="yx-actions">
            <a class="yx-button yx-button--primary" :href="withBase('/intro/quick-start')">开始部署</a>
            <a class="yx-button yx-button--secondary" :href="GITHUB" target="_blank" rel="noreferrer">查看 GitHub</a>
          </div>
        </div>

        <div class="yx-hero__visual">
          <figure class="pixel-mascot" role="img" aria-label="Yuxi 在循环流动的文档、知识图谱、记忆与交付信号中持续工作">
            <canvas ref="canvasRef" class="pixel-mascot__canvas" aria-hidden="true"></canvas>
            <img
              class="pixel-mascot__character"
              :src="MASCOT_IMAGE"
              alt=""
              aria-hidden="true"
              width="1172"
              height="1342"
              draggable="false"
            >
          </figure>
        </div>
      </div>
    </section>

    <main>
      <section class="yx-section yx-start">
        <div class="yx-shell">
          <header v-reveal class="yx-heading">
            <p class="yx-kicker">文档入口</p>
            <h2>从你要完成的事开始</h2>
            <p>直接进入一条可验证的路径，再按需深入配置与运行机制。</p>
          </header>

          <div class="yx-paths">
            <a
              v-for="path in docPaths"
              :key="path.title"
              v-reveal
              class="yx-path"
              :class="[{ 'yx-path--featured': path.featured }, `yx-path--${path.pose}`]"
              :href="withBase(path.link)"
            >
              <span class="yx-path__body">
                <strong>{{ path.title }}</strong>
                <span>{{ path.desc }}</span>
              </span>
              <span class="yx-path__action">{{ path.action }} <span aria-hidden="true">→</span></span>
              <img
                v-lazy-image="path.character"
                class="yx-path__character"
                :width="path.characterWidth"
                :height="path.characterHeight"
                loading="lazy"
                decoding="async"
                alt=""
                aria-hidden="true"
              >
            </a>
          </div>
        </div>
      </section>

      <section class="yx-section yx-tour">
        <div class="yx-shell">
          <header v-reveal class="yx-heading yx-heading--wide">
            <h2>从检索到交付，沿一条任务链发生</h2>
            <p>从真实产品界面查看知识管理、多智能体编排与文件交付怎样汇成同一个结果。</p>
          </header>

          <div v-reveal class="yx-tour__layout">
            <div class="yx-tour__steps" role="group" aria-label="产品界面">
              <button
                v-for="(scene, index) in productScenes"
                :id="`yx-step-${scene.key}`"
                :key="scene.key"
                class="yx-tour__step"
                :class="{ 'yx-tour__step--active': activeScene === index }"
                type="button"
                :aria-current="activeScene === index ? 'step' : undefined"
                @click="scrollToScene(index)"
              >
                <strong>{{ scene.title }}</strong>
                <span>{{ scene.desc }}</span>
              </button>
            </div>

            <div class="yx-tour__panels">
              <figure
                v-for="(scene, index) in productScenes"
                :id="`yx-panel-${scene.key}`"
                :key="scene.key"
                :ref="(element) => registerScenePanel(element, index)"
                class="yx-screen"
                :class="{ 'yx-screen--active': activeScene === index }"
                :data-scene-index="index"
              >
                <div class="yx-screen__backdrop">
                  <img
                    class="yx-screen__visual"
                    :src="scene.image"
                    :alt="`${scene.title}界面截图`"
                    width="2940"
                    height="1670"
                    loading="lazy"
                    decoding="async"
                  >
                </div>
                <figcaption class="yx-screen__caption">{{ scene.title }}</figcaption>
              </figure>
            </div>
          </div>
        </div>
      </section>

      <section class="yx-section yx-providers">
        <div class="yx-shell">
          <header v-reveal class="yx-heading">
            <h2 id="yx-providers-title">一处接入，随处切换</h2>
            <p>统一模型配置，覆盖主流供应商，也支持接入兼容 OpenAI 协议的自定义服务。</p>
          </header>

          <div
            v-reveal
            class="yx-marquee"
            role="region"
            tabindex="0"
            aria-labelledby="yx-providers-title"
            aria-describedby="yx-providers-motion yx-providers-summary"
          >
            <p id="yx-providers-motion" class="yx-sr-only">模型供应商自动滚动展示，聚焦或按住此区域时暂停。</p>
            <p id="yx-providers-summary" class="yx-sr-only">{{ providerSummary }}</p>
            <div
              v-for="(row, rowIndex) in providerRows"
              :key="rowIndex"
              class="yx-marquee__row"
              :class="{ 'yx-marquee__row--reverse': rowIndex === 1 }"
              aria-hidden="true"
            >
              <div class="yx-marquee__track">
                <div v-for="copy in 2" :key="copy" class="yx-marquee__group">
                  <div v-for="provider in row" :key="`${copy}-${provider.name}`" class="yx-provider">
                    <img :src="withBase(provider.icon)" alt="" width="24" height="24">
                    <span>{{ provider.name }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="yx-section yx-flow">
        <div class="yx-shell">
          <header v-reveal class="yx-heading">
            <h2>一条完整的知识智能体链路</h2>
            <p>Yuxi 把知识、执行、交付和团队治理放在同一套可部署边界内。</p>
          </header>

          <ol class="yx-workflow">
            <li v-for="item in workflow" :key="item.title" v-reveal>
              <strong>{{ item.title }}</strong>
              <span>{{ item.desc }}</span>
            </li>
          </ol>

          <div v-reveal class="yx-capabilities">
            <div class="yx-capabilities__intro">
              <span>可私有部署</span>
              <h3>数据、模型与权限由你的团队掌握</h3>
              <a :href="withBase('/intro/project-overview')">认识 Yuxi <span aria-hidden="true">→</span></a>
            </div>
            <dl>
              <div v-for="group in capabilityGroups" :key="group.label">
                <dt>{{ group.label }}</dt>
                <dd>{{ group.value }}</dd>
              </div>
            </dl>
          </div>
        </div>
      </section>

      <section class="yx-section yx-quickstart">
        <div class="yx-shell yx-quickstart__layout">
          <div v-reveal class="yx-quickstart__copy">
            <p class="yx-kicker">快速开始</p>
            <h2>用 Docker Compose 启动完整拓扑</h2>
            <p>准备 Docker 和模型 API，默认使用 Container 沙盒，无需额外执行节点。0.8 使用独立数据目录全新部署。</p>
            <div class="yx-inline-links">
              <a :href="withBase('/intro/quick-start')">阅读完整教程</a>
              <a :href="withBase('/advanced/container-sandbox')">了解 Container 沙盒</a>
              <a :href="withBase('/develop-guides/releases/0.8.0')">0.8.0 更新说明</a>
              <a :href="withBase('/advanced/deployment')">生产部署与升级</a>
            </div>
          </div>

          <div v-reveal class="yx-terminal" aria-label="快速启动命令">
            <div class="yx-terminal__bar">
              <span>Terminal</span>
              <span>Docker Compose</span>
            </div>
            <pre><code><span># 获取 0.8.0 源码</span>
git clone --branch v0.8.0 --depth 1 \
  https://github.com/xerrors/Yuxi.git
cd Yuxi

<span># 初始化配置</span>
./scripts/init.sh
<span># 默认 Container 沙盒</span>
docker compose --profile container config --quiet
docker compose --profile container up --build -d</code></pre>
          </div>
        </div>
      </section>

      <section class="yx-community">
        <div class="yx-shell yx-community__inner">
          <header v-reveal class="yx-heading yx-heading--wide">
            <h2>由开源社区共同构建</h2>
            <p>感谢每一位参与代码、文档、测试和讨论的贡献者。</p>
          </header>

          <a
            v-reveal
            class="yx-contributors"
            :href="`${GITHUB}/graphs/contributors`"
            target="_blank"
            rel="noreferrer"
          >
            <img
              src="https://contrib.rocks/image?repo=xerrors/Yuxi&max=60&columns=12"
              alt="Yuxi 贡献者头像墙"
              width="812"
              height="268"
              loading="lazy"
              decoding="async"
            >
          </a>

          <div v-reveal class="yx-community__footer">
            <div>
            <h2>继续探索，也欢迎一起构建</h2>
            <p>查看演示、提交 Issue，或从贡献指南开始参与 Yuxi。</p>
            </div>
            <div class="yx-community__actions">
              <a class="yx-button yx-button--primary" :href="DEMO" target="_blank" rel="noreferrer">观看演示</a>
              <a class="yx-button yx-button--secondary" :href="withBase('/develop-guides/contributing')">参与贡献</a>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.yx-home {
  --yx-amber: #f3ba32;
  --yx-amber-strong: #925f08;
  --yx-amber-soft: #fff2c9;
  --yx-ink: #272c2a;
  --yx-ink-soft: #59605d;
  --yx-paper: #f7f6f1;
  --yx-surface: #fffefa;
  --yx-line: #dcded8;
  --yx-shadow: rgb(47 44 32 / 12%);
  --yx-focus: var(--yx-ink);
  --yx-radius: 22px;
  --yx-shell: 1180px;
  color: var(--yx-ink);
  background: var(--yx-paper);
  font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 16px;
  line-height: 1.65;
  overflow: clip;
}

.yx-home *,
.yx-home *::before,
.yx-home *::after {
  box-sizing: border-box;
}

.yx-home a {
  color: inherit;
  text-decoration: none;
}

.yx-shell {
  width: min(100% - 48px, var(--yx-shell));
  margin-inline: auto;
}

.yx-section {
  padding: 104px 0;
}

.yx-heading {
  max-width: 680px;
  margin-bottom: 46px;
}

.yx-heading--wide {
  max-width: 760px;
}

.yx-heading h2,
.yx-quickstart h2,
.yx-community h2 {
  margin: 0;
  color: var(--yx-ink);
  font-size: clamp(32px, 4vw, 52px);
  font-weight: 760;
  line-height: 1.16;
  letter-spacing: -.035em;
  text-wrap: balance;
}

.yx-heading > p:last-child,
.yx-quickstart__copy > p,
.yx-community p {
  max-width: 62ch;
  margin: 16px 0 0;
  color: var(--yx-ink-soft);
  font-size: 17px;
}

.yx-kicker {
  margin: 0 0 12px;
  color: var(--yx-amber-strong);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .12em;
}

.yx-hero {
  position: relative;
  min-height: min(760px, calc(100dvh - 64px));
  display: grid;
  align-items: center;
  padding: 56px 0;
  background:
    radial-gradient(circle at 12% 16%, rgb(243 186 50 / 20%), transparent 28%),
    var(--yx-paper);
}

.yx-hero::after {
  position: absolute;
  right: -7vw;
  bottom: -22vw;
  width: min(54vw, 760px);
  aspect-ratio: 1;
  border-radius: 50%;
  background: var(--yx-amber-soft);
  content: "";
  pointer-events: none;
}

.yx-hero__grid {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, .92fr) minmax(360px, .75fr);
  gap: clamp(44px, 7vw, 100px);
  align-items: center;
}

.yx-lockup {
  display: block;
  width: 190px;
  margin-bottom: 34px;
}

.yx-lockup img {
  display: block;
  width: 100%;
  height: auto;
}

.yx-lockup__dark {
  display: none !important;
}

.yx-hero h1 {
  max-width: 700px;
  margin: 0;
  color: var(--yx-ink);
  font-size: clamp(48px, 6.1vw, 78px);
  font-weight: 820;
  line-height: 1.08;
  letter-spacing: -.055em;
  text-wrap: balance;
}

.yx-hero__copy > p {
  max-width: 520px;
  margin: 24px 0 0;
  color: var(--yx-ink-soft);
  font-size: clamp(17px, 1.8vw, 20px);
  line-height: 1.7;
}

.yx-actions,
.yx-community__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 34px;
}

.yx-button {
  min-height: 46px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 24px;
  border: 1px solid var(--yx-line);
  border-radius: 999px;
  font-size: 15px;
  font-weight: 700;
  white-space: nowrap;
  transition: transform .22s ease, border-color .22s ease, background-color .22s ease;
}

.yx-button:hover {
  transform: translateY(-2px);
}

.yx-button:active {
  transform: translateY(1px);
}

.yx-button:focus-visible,
.yx-path:focus-visible,
.yx-tour__step:focus-visible,
.yx-inline-links a:focus-visible,
.yx-capabilities a:focus-visible {
  outline: 3px solid var(--yx-focus);
  outline-offset: 4px;
}

.yx-capabilities a:focus-visible {
  outline-color: var(--yx-amber);
}

.yx-home .yx-button--primary {
  border-color: var(--yx-ink);
  color: var(--yx-paper);
  background: var(--yx-ink);
}

.yx-button--secondary {
  color: var(--yx-ink);
  background: color-mix(in srgb, var(--yx-surface) 82%, transparent);
}

.yx-button--secondary:hover {
  border-color: var(--yx-ink);
}

.yx-hero__visual {
  position: relative;
  width: 100%;
  max-width: 520px;
  aspect-ratio: 1;
  min-height: 0;
  justify-self: end;
  overflow: visible;
}

.yx-hero__visual > * {
  width: 100%;
  height: 100%;
}

.pixel-mascot {
  position: relative;
  width: 100%;
  height: 100%;
  aspect-ratio: 1;
  margin: 0;
  background: transparent;
}

.pixel-mascot__canvas {
  display: block;
  width: 100%;
  height: 100%;
  image-rendering: crisp-edges;
  image-rendering: pixelated;
}

.pixel-mascot__character {
  position: absolute;
  left: 20%;
  top: 16%;
  width: 60%;
  height: 74%;
  object-fit: contain;
  transform-origin: 50% 95%;
  animation: mascot-sway 7.2s ease-in-out infinite;
  pointer-events: none;
}

@keyframes mascot-sway {
  0%, 100% { transform: translateY(0) rotate(-2deg); }
  25% { transform: translateY(-8px) rotate(2deg); }
  50% { transform: translateY(-3px) rotate(-1deg); }
  75% { transform: translateY(-10px) rotate(1.5deg); }
}

.yx-start {
  position: relative;
  background: var(--yx-surface);
}

.yx-paths {
  display: grid;
  grid-template-columns: 1.16fr .84fr;
  gap: 16px;
}

.yx-path {
  position: relative;
  min-height: 180px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 34px;
  padding: 28px;
  border: 1px solid var(--yx-line);
  border-radius: var(--yx-radius);
  background: var(--yx-paper);
  overflow: hidden;
  isolation: isolate;
  transition: transform .24s ease, border-color .24s ease, box-shadow .24s ease;
}

.yx-path--featured {
  grid-row: span 3;
  min-height: 572px;
  padding: 38px;
  background: #e6ebe6;
  border-color: #d4dbd4;
  color: #272c2a;
}

.yx-path--featured::before {
  position: absolute;
  right: -13%;
  bottom: -24%;
  z-index: -1;
  width: 78%;
  aspect-ratio: 1;
  border-radius: 50%;
  background: rgb(243 186 50 / 16%);
  content: '';
}

.yx-path:hover {
  z-index: 1;
  transform: translateY(-4px);
  border-color: var(--yx-amber-strong);
  box-shadow: 0 24px 48px -34px var(--yx-shadow);
}

.yx-path__body {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 12px;
}

.yx-path__body strong {
  font-size: clamp(22px, 2.4vw, 31px);
  line-height: 1.22;
  letter-spacing: -.025em;
}

.yx-path__body > span {
  max-width: 35ch;
  color: var(--yx-ink-soft);
}

.yx-path--featured .yx-path__body > span {
  color: rgb(39 44 42 / 78%);
  font-size: 18px;
}

.yx-path__action {
  position: relative;
  z-index: 2;
  font-size: 14px;
  font-weight: 700;
}

.yx-path__character {
  position: absolute;
  right: -8px;
  bottom: -18px;
  z-index: 0;
  width: 132px;
  max-width: 38%;
  height: 148px;
  object-fit: contain;
  object-position: right bottom;
  pointer-events: none;
  transition: transform .3s cubic-bezier(.2, .7, .2, 1);
}

.yx-path--featured .yx-path__body {
  max-width: 58%;
}

.yx-path--featured .yx-path__character {
  right: -3%;
  bottom: -5%;
  width: 61%;
  max-width: none;
  height: 79%;
}

.yx-path--think .yx-path__character {
  right: 2px;
  bottom: -25px;
  height: 160px;
}

.yx-path--confident .yx-path__character {
  right: -4px;
  bottom: -18px;
}

.yx-path--peek .yx-path__character {
  right: -15px;
  bottom: -30px;
  height: 158px;
  transform: rotate(-4deg);
}

.yx-path:not(.yx-path--featured) .yx-path__body {
  max-width: calc(100% - 92px);
}

.yx-path:hover .yx-path__character {
  transform: translateY(-7px) rotate(-1deg);
}

.yx-path--peek:hover .yx-path__character {
  transform: translateY(-7px) rotate(-4deg);
}

.yx-tour {
  background: var(--yx-paper);
}

.yx-tour__layout {
  display: grid;
  grid-template-columns: minmax(260px, .35fr) minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

/* 左侧步骤在整段阅读中保持固定，右侧面板滚动时推进当前步骤。 */
.yx-tour__steps {
  position: sticky;
  top: calc(var(--vp-nav-height, 64px) + 40px);
  display: grid;
  align-content: start;
  gap: 8px;
}

.yx-tour__step {
  position: relative;
  width: 100%;
  display: grid;
  gap: 6px;
  padding: 18px 20px;
  border: 1px solid transparent;
  border-radius: 16px;
  color: var(--yx-ink-soft);
  background: transparent;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color .25s ease, color .25s ease, background-color .25s ease, transform .25s ease;
}

.yx-tour__step::before {
  content: "";
  position: absolute;
  top: 18px;
  bottom: 18px;
  left: 6px;
  width: 2px;
  border-radius: 2px;
  background: var(--yx-amber);
  opacity: 0;
  transform: scaleY(.35);
  transition: opacity .3s ease, transform .3s cubic-bezier(.2, .7, .2, 1);
}

.yx-tour__step:hover {
  color: var(--yx-ink);
  transform: translateX(3px);
}

.yx-tour__step--active {
  border-color: var(--yx-line);
  color: var(--yx-ink);
  background: var(--yx-surface);
}

.yx-tour__step--active::before {
  opacity: 1;
  transform: scaleY(1);
}

.yx-tour__step strong {
  font-size: 17px;
}

.yx-tour__step span {
  font-size: 13.5px;
  line-height: 1.55;
}

.yx-tour__panels {
  display: grid;
  gap: clamp(28px, 7vh, 64px);
}

.yx-screen {
  min-width: 0;
  min-height: min(76vh, 660px);
  margin: 0;
  display: grid;
  align-content: center;
  opacity: .55;
  transform: scale(.98);
  transition: opacity .45s ease, transform .45s cubic-bezier(.2, .7, .2, 1);
}

.yx-screen--active {
  opacity: 1;
  transform: none;
}

.yx-screen__backdrop {
  padding: clamp(18px, 3vw, 34px);
  overflow: hidden;
  border: 1px solid rgb(39 44 42 / 14%);
  border-radius: calc(var(--yx-radius) + 8px);
  background:
    radial-gradient(circle at 82% 8%, rgb(243 186 50 / 18%), transparent 38%),
    color-mix(in srgb, var(--yx-paper) 90%, var(--yx-amber));
  box-shadow: 0 34px 90px -48px var(--yx-shadow);
}

.yx-screen__visual {
  width: 100%;
  height: auto;
  display: block;
  border: 1px solid var(--yx-line);
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 30px 80px -42px var(--yx-shadow);
}

.yx-screen__caption {
  margin-top: 12px;
  margin-bottom: 0;
  padding-inline: clamp(18px, 3vw, 34px);
  color: var(--yx-ink-soft);
  font-size: 13px;
}

.yx-providers {
  overflow: hidden;
  color: #272c2a;
  background: var(--yx-amber);
}

.yx-providers .yx-heading {
  margin-bottom: 42px;
}

.yx-providers .yx-heading h2,
.yx-providers .yx-heading > p:last-child {
  color: #272c2a;
}

.yx-marquee {
  display: grid;
  gap: 12px;
  border-radius: 18px;
}

.yx-marquee:focus-visible {
  outline: 2px solid #272c2a;
  outline-offset: 6px;
}

.yx-marquee__row {
  width: 100%;
  overflow: hidden;
  mask-image: linear-gradient(to right, transparent, #000 7%, #000 93%, transparent);
}

.yx-marquee__track {
  width: max-content;
  display: flex;
  gap: 12px;
  animation: yx-marquee 34s linear infinite;
}

.yx-marquee__row--reverse .yx-marquee__track {
  animation-direction: reverse;
  animation-duration: 40s;
}

.yx-marquee:is(:hover, :focus-within, :active) .yx-marquee__track {
  animation-play-state: paused;
}

.yx-marquee__group {
  display: flex;
  gap: 12px;
}

.yx-provider {
  min-height: 58px;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 20px;
  border: 1px solid rgb(39 44 42 / 13%);
  border-radius: 999px;
  color: #272c2a;
  background: #fffefa;
  box-shadow: 0 16px 34px -24px rgb(39 44 42 / 46%);
  white-space: nowrap;
}

.yx-provider img {
  width: 24px;
  height: 24px;
  object-fit: contain;
}

.yx-provider span {
  font-size: 15px;
  font-weight: 700;
}

.yx-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@keyframes yx-marquee {
  to {
    transform: translateX(calc(-50% - 6px));
  }
}

.yx-flow {
  background: var(--yx-surface);
}

.yx-workflow {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0;
  margin: 0;
  padding: 0;
  list-style: none;
}

.yx-workflow li {
  position: relative;
  min-height: 178px;
  padding: 22px 26px 22px 0;
  border-top: 1px solid var(--yx-line);
}

.yx-workflow li:not(:last-child)::after {
  position: absolute;
  top: -13px;
  right: 18px;
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #272c2a;
  background: var(--yx-amber);
  content: "→";
  font-size: 14px;
}

.yx-workflow strong,
.yx-workflow span {
  display: block;
}

.yx-workflow strong {
  color: var(--yx-ink);
  font-size: 20px;
}

.yx-workflow span {
  max-width: 25ch;
  margin-top: 10px;
  color: var(--yx-ink-soft);
  font-size: 14px;
}

.yx-capabilities {
  display: grid;
  grid-template-columns: .9fr 1.1fr;
  gap: clamp(36px, 7vw, 86px);
  margin-top: 64px;
  padding: clamp(34px, 6vw, 68px);
  border-radius: var(--yx-radius);
  color: #f1f0ea;
  background: #272c2a;
}

.yx-capabilities__intro > span {
  color: var(--yx-amber);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .1em;
}

.yx-capabilities h3 {
  max-width: 12ch;
  margin: 14px 0 26px;
  color: #f1f0ea;
  font-size: clamp(28px, 3.8vw, 45px);
  line-height: 1.18;
  letter-spacing: -.035em;
}

.yx-capabilities a {
  font-size: 14px;
  font-weight: 700;
}

.yx-capabilities dl {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 28px 36px;
  margin: 0;
}

.yx-capabilities dl > div {
  padding-top: 18px;
  border-top: 1px solid rgb(241 240 234 / 22%);
}

.yx-capabilities dt {
  color: var(--yx-amber);
  font-size: 13px;
  font-weight: 700;
}

.yx-capabilities dd {
  margin: 8px 0 0;
  color: #f1f0ea;
  font-size: 15px;
}

.yx-quickstart {
  background: var(--yx-paper);
}

.yx-quickstart__layout {
  display: grid;
  grid-template-columns: .78fr 1.22fr;
  gap: clamp(44px, 7vw, 96px);
  align-items: center;
}

.yx-quickstart__copy > p {
  margin-top: 20px;
}

.yx-inline-links {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 24px;
  margin-top: 28px;
}

.yx-inline-links a {
  color: var(--yx-amber-strong);
  font-size: 14px;
  font-weight: 700;
  text-decoration: underline;
  text-decoration-thickness: 1px;
  text-underline-offset: 5px;
}

.yx-terminal {
  overflow: hidden;
  border: 1px solid #414743;
  border-radius: var(--yx-radius);
  background: #202523;
  box-shadow: 0 30px 80px -42px var(--yx-shadow);
}

.yx-terminal__bar {
  min-height: 46px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 22px;
  border-bottom: 1px solid #414743;
  color: #aeb4b0;
  font-size: 12px;
}

.yx-terminal pre {
  margin: 0;
  padding: 30px;
  overflow-x: auto;
  color: #f1f0ea;
  font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 14px;
  line-height: 1.8;
}

.yx-terminal code {
  color: inherit;
  background: transparent;
}

.yx-terminal code span {
  color: #d6a934;
}

.yx-community {
  padding: 104px 0 70px;
  border-top: 1px solid var(--yx-line);
  background: var(--yx-surface);
}

.yx-community__inner {
  display: block;
}

.yx-community h2 {
  font-size: clamp(27px, 3.5vw, 42px);
}

.yx-community__actions {
  flex: none;
  margin-top: 0;
}

.yx-contributors {
  display: block;
  max-width: 860px;
  padding: clamp(18px, 3vw, 30px);
  border: 1px solid var(--yx-line);
  border-radius: var(--yx-radius);
  background: var(--yx-paper);
  transition: border-color .2s ease, transform .2s ease;
}

.yx-contributors:hover {
  transform: translateY(-3px);
  border-color: var(--yx-amber-strong);
}

.yx-contributors:focus-visible {
  outline: 3px solid var(--yx-focus);
  outline-offset: 4px;
}

.yx-contributors img {
  width: 100%;
  height: auto;
  display: block;
}

.yx-community__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 40px;
  margin-top: 58px;
  padding-top: 48px;
  border-top: 1px solid var(--yx-line);
}

.yx-reveal {
  opacity: 0;
  transform: translateY(18px);
  transition: opacity .55s cubic-bezier(.2, .7, .2, 1), transform .55s cubic-bezier(.2, .7, .2, 1);
}

.yx-reveal--visible {
  opacity: 1;
  transform: translateY(0);
}

@media (max-width: 900px) {
  .yx-hero {
    min-height: auto;
    padding: 72px 0;
  }

  .yx-hero__grid,
  .yx-quickstart__layout {
    grid-template-columns: 1fr;
  }

  .yx-hero__visual {
    width: min(100%, 520px);
    justify-self: center;
  }

  .yx-tour__layout {
    grid-template-columns: 1fr;
  }

  /* 单列布局里左侧不再固定，步骤改为两列概览。 */
  .yx-tour__steps {
    position: static;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .yx-tour__step:hover {
    transform: translateY(-2px);
  }

  .yx-screen {
    min-height: 0;
    opacity: 1;
    transform: none;
  }

  .yx-workflow {
    grid-template-columns: repeat(2, 1fr);
    gap: 28px 0;
  }

  .yx-workflow li:nth-child(2)::after {
    display: none;
  }
}

@media (max-width: 680px) {
  .yx-shell {
    width: min(100% - 32px, var(--yx-shell));
  }

  .yx-section {
    padding: 76px 0;
  }

  .yx-heading {
    margin-bottom: 34px;
  }

  .yx-hero {
    padding: 48px 0 64px;
  }

  .yx-hero__grid {
    gap: 38px;
  }

  .yx-lockup {
    width: 150px;
    margin-bottom: 24px;
  }

  .yx-hero h1 {
    font-size: clamp(42px, 13vw, 56px);
  }

  .yx-hero__copy > p {
    margin-top: 18px;
  }

  .yx-actions {
    margin-top: 28px;
  }

  .yx-button {
    min-height: 48px;
    padding-inline: 21px;
  }

  .yx-hero__visual {
    width: min(100%, 360px);
  }

  .yx-paths,
  .yx-tour__steps,
  .yx-workflow,
  .yx-capabilities,
  .yx-capabilities dl {
    grid-template-columns: 1fr;
  }

  .yx-path,
  .yx-path--featured {
    min-height: 200px;
    grid-row: auto;
    padding: 25px;
  }

  .yx-path--featured {
    min-height: 330px;
  }

  .yx-path--featured .yx-path__body {
    max-width: 72%;
  }

  .yx-path--featured .yx-path__character {
    right: -5%;
    width: 54%;
    height: 68%;
  }

  .yx-path:not(.yx-path--featured) .yx-path__body {
    max-width: calc(100% - 82px);
  }

  .yx-tour__step {
    min-height: 84px;
  }

  .yx-tour__step span {
    display: none;
  }

  .yx-workflow {
    gap: 0;
  }

  .yx-workflow li {
    min-height: 0;
    padding: 22px 0 30px;
  }

  .yx-workflow li:not(:last-child)::after {
    display: none;
  }

  .yx-capabilities {
    gap: 36px;
    margin-top: 44px;
    padding: 30px 24px;
  }

  .yx-capabilities h3 {
    max-width: none;
  }

  .yx-quickstart__layout {
    gap: 38px;
  }

  .yx-terminal pre {
    padding: 24px 20px;
    font-size: 12px;
  }

  .yx-community__inner {
    display: block;
  }

  .yx-community__footer {
    display: grid;
    gap: 24px;
  }

  .yx-community__actions {
    justify-content: flex-start;
  }
}

/* 视口较矮时收紧步骤内边距与间距，让固定的步骤列整体留在视口内。
   步骤描述在某些宽度下折成两行、列更高，阈值按折行后的最坏情况留余量。 */
@media (min-width: 901px) and (max-height: 660px) {
  .yx-tour__steps {
    gap: 4px;
  }

  .yx-tour__step {
    padding: 8px 18px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .yx-home *,
  .yx-home *::before,
  .yx-home *::after {
    scroll-behavior: auto !important;
    transition-duration: .01ms !important;
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
  }

  .yx-marquee__row {
    overflow-x: auto;
    mask-image: none;
  }

  .yx-marquee__track {
    animation: none;
  }

  .pixel-mascot__character {
    animation: none;
  }

  .yx-reveal {
    opacity: 1;
    transform: none;
  }
}
</style>

<style>
html.dark .yx-home {
  --yx-amber-strong: #f3ba32;
  --yx-amber-soft: #39311d;
  --yx-ink: #f1f0ea;
  --yx-ink-soft: #b9bcb7;
  --yx-paper: #151918;
  --yx-surface: #202523;
  --yx-line: #3d433f;
  --yx-shadow: rgb(0 0 0 / 30%);
  --yx-focus: var(--yx-amber);
}

html.dark .yx-path--featured {
  color: #f4f2ea;
  background: #2b302b;
  border-color: #4a514b;
}

html.dark .yx-path--featured::before {
  background: rgb(243 186 50 / 12%);
}

html.dark .yx-path--featured .yx-path__body strong,
html.dark .yx-path--featured .yx-path__action {
  color: #f4f2ea;
}

html.dark .yx-path--featured .yx-path__body > span {
  color: #c3c8c2;
}

html.dark .yx-screen__backdrop {
  border-color: var(--yx-line);
}

html.dark .yx-lockup__light {
  display: none !important;
}

html.dark .yx-lockup__dark {
  display: block !important;
}
</style>
