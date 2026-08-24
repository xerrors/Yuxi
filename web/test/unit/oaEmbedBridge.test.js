import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_OA_EMBED_MODE,
  createOAEmbedBridge,
  getOAEmbedRenewalDelay,
  parseOAEmbedAllowedOrigins
} from '../../src/utils/oaEmbedBridge.js'
import {
  resolveAppNavigationPath,
  resolveAppSurface,
  shouldShowAppSidebar
} from '../../src/composables/useEmbedMode.js'

function createBrowserHarness() {
  const messages = []
  const timers = []
  const clearedTimers = []
  let messageListener = null
  const parent = {
    postMessage(message, targetOrigin) {
      messages.push({ message, targetOrigin })
    }
  }
  const browserWindow = {
    parent,
    addEventListener(type, listener) {
      if (type === 'message') messageListener = listener
    },
    removeEventListener(type, listener) {
      if (type === 'message' && messageListener === listener) messageListener = null
    }
  }
  const createBridge = (options) =>
    createOAEmbedBridge({
      ...options,
      browserWindow,
      setTimer(callback) {
        timers.push(callback)
        return timers.length
      },
      clearTimer(timerId) {
        clearedTimers.push(timerId)
      }
    })

  return {
    browserWindow,
    messages,
    timers,
    clearedTimers,
    createBridge,
    dispatchMessage(event) {
      return messageListener(event)
    }
  }
}

test('OA origin config only keeps exact HTTP origins', () => {
  assert.equal(DEFAULT_OA_EMBED_MODE, 'fixed')
  assert.deepEqual(
    parseOAEmbedAllowedOrigins(
      'https://oa.example.test, http://localhost:4173 https://oa.example.test/path ftp://oa.test *'
    ),
    ['https://oa.example.test', 'http://localhost:4173']
  )
})

test('route metadata is the only standalone and OA embed surface boundary', () => {
  assert.equal(resolveAppSurface({ matched: [{ meta: { embed: true } }] }), 'oa-embed')
  assert.equal(resolveAppSurface({ matched: [{ meta: { requiresAuth: true } }] }), 'standalone')
})

test('OA embed only shows the complete app sidebar in fullscreen mode', () => {
  assert.equal(shouldShowAppSidebar(true, 'fixed'), false)
  assert.equal(shouldShowAppSidebar(true, 'floating'), false)
  assert.equal(shouldShowAppSidebar(true, 'fullscreen'), true)
  assert.equal(shouldShowAppSidebar(false, 'fixed'), true)
})

test('OA fullscreen keeps PC feature navigation inside the iframe', () => {
  assert.equal(resolveAppNavigationPath(true, '/agent'), '/embed')
  assert.equal(resolveAppNavigationPath(true, '/agent-manage'), '/embed/agent-manage')
  assert.equal(resolveAppNavigationPath(true, '/workspace'), '/embed/workspace')
  assert.equal(resolveAppNavigationPath(true, '/settings/account'), '/embed/settings/account')
  assert.equal(
    resolveAppNavigationPath(true, '/extensions/skill/knowledge-base'),
    '/embed/extensions/skill/knowledge-base'
  )
  assert.equal(resolveAppNavigationPath(false, '/workspace'), '/workspace')
})

test('OA bridge completes the formal parent login handshake with an allowed account', async () => {
  const harness = createBrowserHarness()
  const acceptedAccounts = []
  const bridge = harness.createBridge({
    allowedOrigins: ['https://oa.example.test'],
    onAccount: async (account) => acceptedAccounts.push(account)
  })

  bridge.start()
  harness.timers[0]()
  await harness.dispatchMessage({
    source: harness.browserWindow.parent,
    origin: 'https://attacker.example.test',
    data: { type: 'login-params', data: { userInfo: { account: 'attacker' } } }
  })
  await harness.dispatchMessage({
    source: harness.browserWindow.parent,
    origin: 'https://oa.example.test',
    data: { type: 'login-params', data: { userInfo: { account: ' oa-user-1 ' } } }
  })

  assert.deepEqual(acceptedAccounts, ['oa-user-1'])
  assert.deepEqual(harness.messages.map(({ message }) => message.type), ['ready', 'request-login-params'])
  assert.equal(typeof harness.messages[1].message.data.timestamp, 'number')
  assert.equal(harness.messages[1].targetOrigin, 'https://oa.example.test')
  assert.equal(
    harness.messages.some(({ targetOrigin }) => targetOrigin === '*'),
    false
  )
})

test('OA bridge rejects missing accounts and asks the authenticated parent to renew', async () => {
  const harness = createBrowserHarness()
  const acceptedAccounts = []
  const bridge = harness.createBridge({
    allowedOrigins: ['https://oa.example.test'],
    onAccount: async (account) => acceptedAccounts.push(account)
  })

  bridge.start()
  harness.timers[0]()
  await harness.dispatchMessage({
    source: harness.browserWindow.parent,
    origin: 'https://oa.example.test',
    data: { type: 'login-params', data: { userInfo: { account: '   ' } } }
  })
  await harness.dispatchMessage({
    source: harness.browserWindow.parent,
    origin: 'https://oa.example.test',
    data: { type: 'login-params', data: { userInfo: { account: 'oa-user-1' } } }
  })
  bridge.requestAuthRequired()

  assert.deepEqual(acceptedAccounts, ['oa-user-1'])
  assert.deepEqual(harness.messages.at(-1), {
    message: {
      type: 'request-login-params',
      data: { timestamp: harness.messages.at(-1).message.data.timestamp }
    },
    targetOrigin: 'https://oa.example.test'
  })
})

test('OA embed renews one hour before a valid JWT expires', () => {
  const accessToken = `header.${btoa(JSON.stringify({ exp: 7200 }))}.signature`
  assert.equal(getOAEmbedRenewalDelay(accessToken, 0), 3600000)
  assert.equal(getOAEmbedRenewalDelay('invalid-token', 0), null)
})

test('OA bridge requests login parameters after the formal handshake delay and clears its timer', () => {
  const harness = createBrowserHarness()
  const bridge = harness.createBridge({
    allowedOrigins: ['https://oa.example.test'],
    onAccount: async () => {}
  })

  bridge.start()
  harness.timers[0]()
  bridge.stop()

  assert.deepEqual(
    harness.messages.map(({ message }) => message.type),
    ['ready', 'request-login-params']
  )
  assert.equal(harness.timers.length, 1)
  assert.deepEqual(harness.clearedTimers, [1])
})

test('OA bridge sends renewal requests only to the parent that supplied the account', async () => {
  const harness = createBrowserHarness()
  const bridge = harness.createBridge({
    allowedOrigins: ['https://oa.example.test', 'https://oa-backup.example.test'],
    onAccount: async () => {}
  })

  bridge.start()
  await harness.dispatchMessage({
    source: harness.browserWindow.parent,
    origin: 'https://oa-backup.example.test',
    data: { type: 'login-params', data: { userInfo: { account: 'oa-user-1' } } }
  })
  bridge.requestAuthRequired()

  assert.equal(harness.messages.at(-1).message.type, 'request-login-params')
  assert.equal(harness.messages.at(-1).targetOrigin, 'https://oa-backup.example.test')
  assert.equal(
    harness.messages.some(({ targetOrigin }) => targetOrigin === '*'),
    false
  )
})

test('OA bridge maps formal window events and sends nested parent commands', async () => {
  const harness = createBrowserHarness()
  const confirmedModes = []
  const bridge = harness.createBridge({
    allowedOrigins: ['https://oa.example.test'],
    onAccount: async () => {},
    onModeChanged: (mode) => confirmedModes.push(mode)
  })

  bridge.start()
  await harness.dispatchMessage({
    source: harness.browserWindow.parent,
    origin: 'https://oa.example.test',
    data: { type: 'initial-mode', mode: 'floating' }
  })
  assert.equal(bridge.requestMode('fixed'), true)
  assert.equal(bridge.requestMode('fullscreen'), true)
  assert.equal(bridge.requestMode('fixed'), true)
  assert.equal(bridge.requestMode('fixed'), false)
  assert.equal(bridge.requestMode('invalid'), false)
  bridge.requestClose('thread-1')

  assert.deepEqual(confirmedModes, ['floating'])
  assert.deepEqual(harness.messages.slice(-4), [
    {
      message: { type: 'toggle-floating', data: { isFloating: false } },
      targetOrigin: 'https://oa.example.test'
    },
    {
      message: { type: 'toggle-window-size', data: { isEnlarge: true } },
      targetOrigin: 'https://oa.example.test'
    },
    {
      message: { type: 'toggle-window-size', data: { isEnlarge: false } },
      targetOrigin: 'https://oa.example.test'
    },
    {
      message: { type: 'close', data: { threadId: 'thread-1' } },
      targetOrigin: 'https://oa.example.test'
    }
  ])
})
