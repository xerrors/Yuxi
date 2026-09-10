import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { providers } from '@opencode-ai/models/snapshot'

// 快照只在构建进程读取，浏览器只接收展示字段；模型覆盖随锁定依赖更新。
const modelMetadataPlugin = {
  name: 'model-display-metadata',
  resolveId(id) {
    if (id === 'virtual:model-display-metadata') return '\0' + id
  },
  load(id) {
    if (id !== '\0virtual:model-display-metadata') return
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
    return `export const providers = ${JSON.stringify(catalog)}`
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
    plugins: [vue(), modelMetadataPlugin, pdfjsCmapsPlugin],
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
        ignored: ['**/node_modules/**', '**/dist/**']
      },
      host: '0.0.0.0'
    }
  }
})
