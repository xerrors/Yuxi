import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { createServer } from 'vite'

/** 服务 SSR 和静态资源测试，关闭客户端预构建并隔离临时缓存。 */
export async function createTestServer(config = {}) {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'yuxi-vite-test-'))
  let server
  try {
    server = await createServer({
      mode: 'test',
      ...config,
      // 测试不依赖热更新，关闭固定 WebSocket 端口以隔离并行服务。
      server: { ...config.server, hmr: false, ws: false },
      plugins: [
        {
          name: 'test-server-resource-owner',
          enforce: 'pre',
          configureServer: {
            order: 'pre',
            handler(created) {
              server = created
            }
          }
        },
        ...(config.plugins || [])
      ],
      optimizeDeps: { noDiscovery: true, include: [] },
      cacheDir
    })
    const close = server.close.bind(server)
    server.close = async () => {
      try {
        await close()
      } finally {
        await rm(cacheDir, { recursive: true, force: true })
      }
    }
    return server
  } catch (error) {
    try {
      await server?.close()
    } finally {
      await rm(cacheDir, { recursive: true, force: true })
    }
    throw error
  }
}
