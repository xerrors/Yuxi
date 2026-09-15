import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { parse } from 'vue/compiler-sfc'
import { ref, computed } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

const script = parse(
  readFileSync(
    new URL('../../src/components/extensions/ToolsCardList.vue', import.meta.url),
    'utf8'
  )
).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
function panel(failSave = false, failRefresh = false) {
  const events = []
  const setup = new Function(
    'ref',
    'computed',
    'onMounted',
    'defineExpose',
    'useUserStore',
    'useAgentStore',
    'toolApi',
    'message',
    'Wrench',
    `${script}; return { currentTool, openDisplayName, saveDisplayName, nameOpen, nameError, displayName, tools }`
  )
  return {
    events,
    ...setup(
      ref,
      computed,
      () => {},
      () => {},
      () => ({ isAdmin: true }),
      () => ({
        refreshToolMetadata: async () => {
          events.push('refresh')
          if (failRefresh) throw new Error('offline')
          return [{ slug: 'stable', name: '新名' }]
        }
      }),
      {
        setDisplayName: async (...args) => {
          events.push(args)
          if (failSave) throw new Error('写入失败')
        }
      },
      { success: (text) => events.push(text), warning: (text) => events.push(text) },
      {}
    )
  }
}

test('工具改名同步共享store；取消不写，写失败不刷新', async () => {
  const p = panel()
  p.currentTool.value = { slug: 'stable', name: '旧名' }
  p.openDisplayName()
  p.nameOpen.value = false
  assert.deepEqual(p.events, [])
  p.openDisplayName()
  p.displayName.value = '新名'
  await p.saveDisplayName()
  assert.deepEqual(p.events.slice(0, 2), [['stable', '新名'], 'refresh'])
  assert.equal(p.currentTool.value.name, '新名')
  const bad = panel(true)
  bad.currentTool.value = { slug: 'stable', name: '旧名' }
  bad.openDisplayName()
  await bad.saveDisplayName()
  assert.equal(bad.nameError.value, '写入失败')
  assert.equal(bad.nameOpen.value, true)
  assert.equal(bad.events.length, 1)
})

test('PUT成功刷新失败报告已保存，不冒充保存失败', async () => {
  const p = panel(false, true)
  p.currentTool.value = { slug: 'stable', name: '旧名' }
  p.openDisplayName()
  await p.saveDisplayName()
  assert.equal(p.nameError.value, '')
  assert.equal(p.nameOpen.value, false)
  assert.ok(p.events.at(-1).includes('已保存，但界面刷新失败'))
})

test('真实agent store刷新工具名失败保留旧有效缓存', async (t) => {
  const previousStorage = globalThis.localStorage
  globalThis.localStorage = {
    getItem() {
      return null
    },
    setItem() {},
    removeItem() {}
  }
  const server = await createServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  setActivePinia(createPinia())
  try {
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    const { toolApi } = await server.ssrLoadModule('/src/apis/tool_api.js')
    let fail = false
    t.mock.method(toolApi, 'getTools', async () => {
      if (fail) throw new Error('offline')
      return { data: [{ slug: 'stable', name: '新名' }] }
    })
    const store = useAgentStore()
    await store.refreshToolMetadata()
    assert.equal(store.toolMetadata[0].name, '新名')
    fail = true
    await assert.rejects(store.refreshToolMetadata(), /offline/)
    assert.equal(store.toolMetadata[0].name, '新名')
  } finally {
    await server.close()
    if (previousStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = previousStorage
  }
})
