import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, disposePinia } from 'pinia'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'

import { copyTextToClipboard } from '../../src/shared/lib/clipboard.js'
import { createTestServer } from '../helpers/createTestServer.js'

function createDocumentStub(execResult = true) {
  const appended = []
  const removed = []
  return {
    appended,
    removed,
    execCommand(command) {
      assert.equal(command, 'copy')
      return execResult
    },
    createElement(tag) {
      assert.equal(tag, 'textarea')
      return {
        style: {},
        setAttribute() {},
        focus() {},
        select() {}
      }
    },
    body: {
      appendChild(node) {
        appended.push(node)
      },
      removeChild(node) {
        removed.push(node)
      }
    }
  }
}

function withClipboardGlobals({ secure, writeText, document }, callback) {
  const previousWindow = globalThis.window
  const previousNavigator = globalThis.navigator
  const previousDocument = globalThis.document
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { isSecureContext: secure }
  })
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { clipboard: writeText ? { writeText } : undefined }
  })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document })

  return Promise.resolve(callback()).finally(() => {
    if (previousWindow === undefined) delete globalThis.window
    else Object.defineProperty(globalThis, 'window', { configurable: true, value: previousWindow })
    if (previousNavigator === undefined) delete globalThis.navigator
    else
      Object.defineProperty(globalThis, 'navigator', {
        configurable: true,
        value: previousNavigator
      })
    if (previousDocument === undefined) delete globalThis.document
    else
      Object.defineProperty(globalThis, 'document', {
        configurable: true,
        value: previousDocument
      })
  })
}

test('非安全上下文使用 execCommand 降级复制', async () => {
  const document = createDocumentStub()
  await withClipboardGlobals({ secure: false, document }, async () => {
    await copyTextToClipboard('hello')
  })

  assert.equal(document.appended[0].value, 'hello')
  assert.equal(document.removed.length, 1)
})

test('Clipboard API 被拒绝时继续使用 execCommand 降级复制', async () => {
  const document = createDocumentStub()
  await withClipboardGlobals(
    {
      secure: true,
      writeText: async () => {
        throw new Error('permission denied')
      },
      document
    },
    async () => {
      await copyTextToClipboard('fallback')
    }
  )

  assert.equal(document.appended[0].value, 'fallback')
  assert.equal(document.removed.length, 1)
})

test('消息引用栏在非安全上下文回读复制结果，失败时显示错误', async (t) => {
  const server = await createTestServer({ server: { middlewareMode: true } })
  const pinia = createPinia()
  t.after(async () => {
    disposePinia(pinia)
    await server.close()
  })

  const { message } = await import('ant-design-vue')
  const notices = []
  t.mock.method(message, 'success', () => notices.push('success'))
  t.mock.method(message, 'error', () => notices.push('error'))
  const { default: RefsComponent } = await server.ssrLoadModule(
    '/src/modules/session/ui/RefsComponent.vue'
  )
  let refs
  await renderToString(createSSRApp({
    setup() {
      refs = RefsComponent.setup({ message: { content: '消息正文' } }, { emit() {}, expose() {} })
      return () => h('div')
    }
  }).use(pinia))
  t.mock.timers.enable({ apis: ['setTimeout'] })

  const rejected = createDocumentStub(false)
  await withClipboardGlobals({ secure: false, document: rejected }, () => refs.copyText('消息正文'))
  assert.deepEqual(notices, ['error'])
  assert.equal(refs.isCopied.value, false)

  const copied = createDocumentStub()
  await withClipboardGlobals({ secure: false, document: copied }, () => refs.copyText('消息正文'))
  assert.equal(copied.appended[0].value, '消息正文')
  assert.equal(copied.removed[0], copied.appended[0])
  assert.deepEqual(notices, ['error', 'success'])
  assert.equal(refs.isCopied.value, true)
})
