import fs from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath, URL } from 'node:url'
import { constants } from 'node:zlib'
import compression from 'compression'
import { defineConfig, loadEnv } from 'vite'
import esbuild from 'esbuild'
import vue from '@vitejs/plugin-vue'
import { fileViewerRenderers } from '@file-viewer/vite-plugin'

let jszipEsmPromise
const jszipBrowserBuild = () => {
  const wordRenderer = createRequire(import.meta.url).resolve('@file-viewer/renderer-word')
  const docx = createRequire(wordRenderer).resolve('@file-viewer/docx')
  return createRequire(docx).resolve('jszip/dist/jszip.min.js')
}
const jszipEsmPlugin = {
  name: 'yuxi-jszip-esm',
  enforce: 'pre',
  resolveId(id) {
    const normalized = String(id || '').split('?')[0]
    if (
      normalized === 'jszip' ||
      normalized.endsWith('/jszip/lib/index.js') ||
      normalized.endsWith('/jszip/dist/jszip.js') ||
      normalized.endsWith('/jszip/dist/jszip.min.js')
    ) {
      return '\0yuxi-jszip-esm'
    }
  },
  async load(id) {
    if (id !== '\0yuxi-jszip-esm') return
    jszipEsmPromise ??= esbuild
      .build({
        entryPoints: [jszipBrowserBuild()],
        bundle: true,
        format: 'esm',
        platform: 'browser',
        write: false,
        logLevel: 'silent'
      })
      .then((result) => result.outputFiles[0].text)
    return jszipEsmPromise
  }
}

// 只压缩开发模块及其内联 source map，API 代理和 SSE 直接流式交付。
const devModuleCompression = {
  name: 'dev-module-compression',
  configureServer(server) {
    const compress = compression({
      level: constants.Z_BEST_SPEED,
      brotli: { params: { [constants.BROTLI_PARAM_QUALITY]: 1 } }
    })
    server.middlewares.use('/src', compress)
    server.middlewares.use('/node_modules', compress)
    server.middlewares.use('/@vite', compress)
    server.middlewares.use('/@id', compress)
  }
}

// 快照只在构建进程读取，浏览器只接收展示字段；模型覆盖随锁定依赖更新。
const modelMetadataPlugin = {
  name: 'model-display-metadata',
  resolveId(id) {
    if (id === 'virtual:model-display-metadata') return '\0' + id
  },
  async load(id) {
    if (id !== '\0virtual:model-display-metadata') return
    const { providers } = await import('@opencode-ai/models/snapshot')
    const catalog = Object.fromEntries(
      Object.entries(providers).map(([providerId, provider]) => [
        providerId,
        {
          models: Object.fromEntries(
            Object.entries(provider.models).map(([modelId, model]) => [
              modelId,
              {
                modalities: { input: model.modalities?.input },
                limit: { context: model.limit?.context },
                cost: model.cost
              }
            ])
          )
        }
      ])
    )
    // 这是从快照生成的纯数据，逐字段源码映射会重复传输整份目录。
    return { code: `export const providers = ${JSON.stringify(catalog)}`, map: { mappings: '' } }
  }
}

const PDFJS_CMAPS_REQUEST_PATH = '/assets/pdfjs-cmaps/'

const pdfjsCmapsDir = fileURLToPath(new URL('./node_modules/pdfjs-dist/cmaps', import.meta.url))

// basename 限制请求只落在 cmaps 目录内的 .bcmap 文件上。
const resolvePdfjsCmapFile = (requestUrl) => {
  try {
    const name = path.basename(decodeURIComponent(String(requestUrl || '')))
    if (!name.endsWith('.bcmap')) return null
    const file = path.join(pdfjsCmapsDir, name)
    return fs.existsSync(file) ? file : null
  } catch {
    return null
  }
}

// pdf.js 的 CMap 资源随应用本地发布，预览不依赖外部 CDN；
// 开发态由中间件直读依赖目录，构建态复制进静态产物。
const pdfjsCmapsPlugin = {
  name: 'yuxi-pdfjs-cmaps',
  configureServer(server) {
    server.middlewares.use(PDFJS_CMAPS_REQUEST_PATH, (req, res, next) => {
      const file = resolvePdfjsCmapFile(req.url)
      if (!file) {
        next()
        return
      }
      res.setHeader('Content-Type', 'application/octet-stream')
      res.setHeader('Cache-Control', 'public, max-age=31536000, immutable')
      fs.createReadStream(file).pipe(res)
    })
  },
  generateBundle() {
    for (const entry of fs.readdirSync(pdfjsCmapsDir, { withFileTypes: true })) {
      if (!entry.isFile()) continue
      this.emitFile({
        type: 'asset',
        fileName: 'assets/pdfjs-cmaps/' + entry.name,
        source: fs.readFileSync(path.join(pdfjsCmapsDir, entry.name))
      })
    }
  }
}

export default defineConfig(({ mode }) => {
  // eslint-disable-next-line no-undef
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [
      jszipEsmPlugin,
      vue(),
      modelMetadataPlugin,
      pdfjsCmapsPlugin,
      // 只装配 Word / 表格 / PPTX，并把 Worker、WASM、字体复制到同源产物。
      // inject 关闭后，入口不会静态引入渲染器，预览组件首次打开时再加载虚拟模块。
      fileViewerRenderers({
        formats: ['docx', 'xlsx', 'xls', 'pptx'],
        inject: false,
        // SSR 单测不消费浏览器 Worker，生产构建与开发服务器各自发布。
        copyAssets: mode === 'test' ? false : { publicDir: 'node_modules/.cache/file-viewer' },
        // 按渲染器命名分包会把 Vite preload helper 放进演示文稿 chunk，入口随之静态加载该 chunk。
        // 保持默认拆包，预览组件的动态 import 仍把渲染器留在异步图里。
        chunkStrategy: 'none',
        missingRenderer: 'error'
      }),
      // 插件会把 docx/jszip 放进 optimizeDeps.include，SSR 测试服务器因此启动客户端预构建。
      // 这些依赖只在 Office 预览动态导入时才需要，开发态由 Vite 按需优化。
      {
        name: 'yuxi-office-deps-on-demand',
        enforce: 'post',
        configResolved(config) {
          const include = config.optimizeDeps?.include
          if (!Array.isArray(include)) return
          config.optimizeDeps.include = include.filter(
            (id) =>
              !id.startsWith('@file-viewer/') &&
              !id.startsWith('@xmldom/') &&
              id !== 'jszip' &&
              id !== 'pdf-lib'
          )
          // 按需预构建时仍按 CJS 互操作处理，避免浏览器把默认导入改写成不存在的具名导出。
          const needsInterop = new Set(config.optimizeDeps.needsInterop || [])
          needsInterop.add('@xmldom/xmldom')
          config.optimizeDeps.needsInterop = [...needsInterop]
          const exclude = new Set(config.optimizeDeps.exclude || [])
          exclude.add('jszip')
          exclude.add('@file-viewer/docx')
          config.optimizeDeps.exclude = [...exclude]
        }
      },
      devModuleCompression
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      proxy: {
        '^/api': {
          target: env.VITE_API_URL || 'http://api:5050',
          changeOrigin: true
        },
        '^/minio/public/': {
          target: env.VITE_MINIO_URL || 'http://minio:9000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/minio/, '')
        }
      },
      watch: {
        usePolling: true,
        interval: 1000,
        ignored: ['**/node_modules/**', '**/dist/**']
      },
      host: '0.0.0.0'
    }
  }
})
