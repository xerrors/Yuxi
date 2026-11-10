import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

async function withServer(run) {
  const server = await createTestServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [
      {
        name: 'test-message-api',
        enforce: 'pre',
        resolveId(id) {
          return id === 'ant-design-vue' ? '\0test-message-api' : null
        },
        load(id) {
          if (id !== '\0test-message-api') return null
          return `export const message = {
            error(value) { globalThis.__apiBoundaryMessages.push(value) }
          }`
        }
      }
    ]
  })

  try {
    await run(server)
  } finally {
    await server.close()
  }
}

test('公开登录 401 保留服务端错误且不清理当前会话', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'existing-token')
    globalThis.__apiBoundaryMessages = []
    globalThis.window = { location: { href: '/current' } }
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: '用户名或密码错误' }), {
        status: 401,
        headers: { 'content-type': 'application/json' }
      })

    const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')

    await assert.rejects(
      authApi.login({ loginId: 'someone', password: 'wrong' }),
      (error) => error.message === '用户名或密码错误' && error.status === 401
    )
    assert.equal(storageValues.get('user_token'), 'existing-token')
    assert.equal(window.location.href, '/current')
    assert.deepEqual(globalThis.__apiBoundaryMessages, [])
  })
})

test('注销文案仅映射公开登录 403，其他权限拒绝继续脱敏', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'fixture-token')
    globalThis.__apiBoundaryMessages = []
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'private account data' }), {
      status: 403, headers: { 'content-type': 'application/json' }
    })
    const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')
    const { apiGet } = await server.ssrLoadModule('/src/apis/base.js')
    await assert.rejects(authApi.login({ loginId: 'fixture', password: 'wrong' }), {
      message: '账号已注销，请联系管理员', status: 403
    })
    for (const [path, authenticated] of [['/api/users', true], ['/api/other-public', false], ['/api/auth/token', true]]) {
      await assert.rejects(apiGet(path, {}, authenticated), (error) => {
        assert.equal(error.message, '没有权限执行此操作')
        assert.deepEqual(error.response.data, { detail: '没有权限执行此操作' })
        return true
      })
    }
    assert.deepEqual(globalThis.__apiBoundaryMessages, [])
  })
})

test('创建有 ZIP 使用 multipart，其余保持 JSON，均只发送一次请求', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'fixture-token')
    const requests = []
    globalThis.fetch = async (url, options) => {
      requests.push({ url, options })
      return new Response(JSON.stringify({ agent: { agent_id: 'created' } }), { headers: { 'content-type': 'application/json' } })
    }
    const { agentApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
    const payload = { name: 'Create', mcp_servers: [] }
    await agentApi.createAgent(payload)
    const file = new File(['zip'], 'guide.zip', { type: 'application/zip' })
    await agentApi.createAgent(payload, file)
    assert.equal(requests.length, 2)
    assert.equal(requests[0].url, '/api/agent')
    assert.deepEqual(JSON.parse(requests[0].options.body), payload)
    assert.equal(requests[1].url, '/api/agent/with-skill')
    assert.equal(requests[1].options.headers['Content-Type'], undefined)
    assert.deepEqual(JSON.parse(requests[1].options.body.get('agent')), payload)
    assert.equal(await requests[1].options.body.get('file').text(), 'zip')
  })
})

test('创建业务错误显示可修正的原因，校验错误继续脱敏', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'fixture-token')
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code: 'agent_creation_invalid', message: '无效 ZIP 文件' } }), { status: 422, headers: { 'content-type': 'application/json' } })
    const { agentApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
    await assert.rejects(agentApi.createAgent({ name: 'Create' }, new File(['bad'], 'bad.zip')), (error) => error.status === 422 && error.message === '无效 ZIP 文件')
  })
})

test('登录 423 保留锁定状态、文案和剩余时间响应头', async () => {
  await withServer(async (server) => {
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: '账户已锁定 60 秒' }), {
        status: 423,
        headers: {
          'content-type': 'application/json',
          'X-Lock-Remaining': '60'
        }
      })

    const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')

    await assert.rejects(authApi.login({ loginId: 'someone', password: 'wrong' }), (error) => {
      assert.equal(error.message, '账户已锁定 60 秒')
      assert.equal(error.status, 423)
      assert.equal(error.headers.get('X-Lock-Remaining'), '60')
      return true
    })
  })
})

test('受保护请求的 401 才清理会话并跳转登录页', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'expired-token')
    globalThis.__apiBoundaryMessages = []
    globalThis.window = { location: { href: '/agent' } }
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: '令牌已过期' }), {
        status: 401,
        headers: { 'content-type': 'application/json' }
      })

    const originalSetTimeout = globalThis.setTimeout
    try {
      setActivePinia(createPinia())
      const { apiGet } = await server.ssrLoadModule('/src/apis/base.js')
      const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
      const userStore = useUserStore()
      globalThis.setTimeout = (callback) => {
        callback()
        return 0
      }

      await assert.rejects(apiGet('/api/protected'), (error) => error.status === 401)
      assert.equal(userStore.isLoggedIn, false)
      assert.equal(storageValues.has('user_token'), false)
      assert.equal(window.location.href, '/login')
      assert.deepEqual(globalThis.__apiBoundaryMessages, ['登录已过期，请重新登录'])
    } finally {
      globalThis.setTimeout = originalSetTimeout
    }
  })
})

test('用户 Store 的 422 传播链不泄露认证头、密码或 Pydantic input', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const secretToken = 'secret-bearer-token'
    const secretPassword = 'secret-password'
    const secretResponse = 'secret-response-context'
    const logged = []
    const originalConsoleError = console.error

    try {
      console.error = (...values) => logged.push(values)
      globalThis.fetch = async () =>
        new Response(
          JSON.stringify({
            detail: [
              {
                loc: ['body', 'password'],
                msg: 'Value error',
                type: 'value_error',
                input: secretPassword,
                ctx: { secret: secretResponse }
              }
            ]
          }),
          {
            status: 422,
            statusText: 'Unprocessable Entity',
            headers: { 'content-type': 'application/json' }
          }
        )

      setActivePinia(createPinia())
      const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
      const userStore = useUserStore()
      userStore.token = secretToken
      userStore.userId = 1

      await assert.rejects(
        userStore.createUser({ username: 'new-user', password: secretPassword }),
        (error) => error.status === 422
      )

      const serializedLogs = JSON.stringify(logged)
      assert.equal(serializedLogs.includes(secretToken), false)
      assert.equal(serializedLogs.includes(secretPassword), false)
      assert.equal(serializedLogs.includes(secretResponse), false)
      assert.equal(serializedLogs.includes('/api/auth/users'), true)
      assert.equal(serializedLogs.includes('422'), true)
    } finally {
      console.error = originalConsoleError
    }
  })
})

test('用户 Store 的普通错误传播链不附着或记录服务端任意响应上下文', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const secretToken = 'secret-bearer-token'
    const secretPassword = 'secret-password'
    const secretResponse = 'secret-response-context'
    const logged = []
    const originalConsoleError = console.error

    try {
      console.error = (...values) => logged.push(values)
      globalThis.fetch = async () =>
        new Response(
          JSON.stringify({
            detail: {
              message: secretResponse,
              context: { note: secretPassword }
            }
          }),
          {
            status: 400,
            statusText: secretResponse,
            headers: { 'content-type': 'application/json' }
          }
        )

      setActivePinia(createPinia())
      const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
      const userStore = useUserStore()
      userStore.token = secretToken
      userStore.userId = 1

      await assert.rejects(
        userStore.createUser({ username: 'new-user', password: secretPassword }),
        (error) => {
          assert.equal(error.status, 400)
          assert.equal(error.message, '请求参数错误')
          assert.deepEqual(error.response.data, { detail: '请求参数错误' })
          return true
        }
      )

      const serializedLogs = JSON.stringify(logged)
      assert.equal(serializedLogs.includes(secretToken), false)
      assert.equal(serializedLogs.includes(secretPassword), false)
      assert.equal(serializedLogs.includes(secretResponse), false)
      assert.equal(serializedLogs.includes('/api/auth/users'), true)
      assert.equal(serializedLogs.includes('400'), true)
    } finally {
      console.error = originalConsoleError
    }
  })
})

test('Skill 业务错误展示具体原因且丢弃额外响应字段', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'fixture-token')
    const detail = {
      code: 'skill_request_invalid',
      message: 'SKILL.md frontmatter.slug 必须是小写字母/数字/短横线',
      context: { secret: 'should-drop' }
    }
    globalThis.fetch = async () => new Response(JSON.stringify({ detail }), {
      status: 400, headers: { 'content-type': 'application/json' }
    })
    const { prepareSkillUpload } = await server.ssrLoadModule('/src/apis/skill_api.js')
    const { resolveApiErrorMessage } = await server.ssrLoadModule('/src/apis/base.js')
    await assert.rejects(prepareSkillUpload(new Blob(['invalid skill'])), (error) => {
      assert.equal(error.status, 400)
      assert.equal(resolveApiErrorMessage(error, '解析 Skill 失败'), detail.message)
      assert.deepEqual(error.response.data.detail, { code: detail.code, message: detail.message })
      assert.equal(JSON.stringify(error.response.data).includes('should-drop'), false)
      return true
    })
  })
})

test('知识库 URL 导入保留业务拒绝原因，额外响应字段不进入页面错误', async () => {
  await withServer(async (server) => {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'fixture-token', isAdmin: () => true })
    const { fileApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    const { resolveApiErrorMessage } = await server.ssrLoadModule('/src/apis/base.js')
    const detail = {
      code: 'url_fetch_rejected', message: 'URL 导入未启用，请联系管理员配置域名白名单',
      context: { secret: 'private-url-diagnostic' }
    }
    globalThis.fetch = async () => new Response(JSON.stringify({ detail }), {
      status: 400, headers: { 'content-type': 'application/json' }
    })
    await assert.rejects(fileApi.fetchUrl('https://example.test/page'), (error) => {
      assert.equal(error.status, 400)
      assert.equal(resolveApiErrorMessage(error), detail.message)
      assert.deepEqual(error.response.data.detail, { code: detail.code, message: detail.message })
      assert.equal(JSON.stringify(error).includes('private-url-diagnostic'), false)
      return true
    })
  })
})

test('错误文案解析保留脱敏字符串、冲突提示和默认文案', async () => {
  await withServer(async (server) => {
    const { resolveApiErrorMessage } = await server.ssrLoadModule('/src/apis/base.js')
    assert.equal(resolveApiErrorMessage({ response: { data: { detail: '请求参数错误' } } }), '请求参数错误')
    assert.equal(resolveApiErrorMessage(new Error('请求冲突，请刷新后重试')), '请求冲突，请刷新后重试')
    assert.equal(resolveApiErrorMessage({}, '解析 Skill 失败'), '解析 Skill 失败')
  })
})

test('409 冲突优先展示后端业务文案而不是刷新兜底', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'fixture-token')
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: 'Thread 已归档' }), {
        status: 409,
        headers: { 'content-type': 'application/json' }
      })
    const { apiRequest } = await server.ssrLoadModule('/src/apis/base.js')
    await assert.rejects(apiRequest('/api/v1/agents/sessions/x/events', { method: 'POST' }), (error) => {
      assert.equal(error.status, 409)
      assert.equal(error.message, 'Thread 已归档')
      return true
    })
  })
})

test('用户管理分页 API 只请求当前页并编码服务端筛选条件', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'test-token')
    const requests = []
    globalThis.fetch = async (url) => {
      requests.push(String(url))
      return new Response(JSON.stringify({ items: [], total: 0, limit: 20, offset: 40 }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }

    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
    useUserStore().userRole = 'superadmin'
    const { authApi } = await server.ssrLoadModule('/src/apis/auth_api.js')

    const page = await authApi.getUsersPage({
      offset: 40,
      limit: 20,
      search: '张 三',
      departmentId: 3,
      role: 'admin'
    })

    assert.deepEqual(requests, [
      '/api/auth/users/page?offset=40&limit=20&search=%E5%BC%A0+%E4%B8%89&department_id=3&role=admin&user_kind=human'
    ])
    assert.equal(page.total, 0)
    await authApi.getUsersPage({ userKind: 'all' })
    assert.equal(requests.at(-1), '/api/auth/users/page?offset=0&limit=50&user_kind=all')
  })
})

test('用户管理组件不再通过 Store 全量加载用户', async () => {
  const source = await import('node:fs/promises').then((fs) =>
    fs.readFile(
      new URL('../../src/modules/settings/ui/UserManagementComponent.vue', import.meta.url),
      'utf8'
    )
  )

  assert.equal(source.includes('authApi.getUsersPage('), true)
  assert.equal(source.includes('userStore.getUsers()'), false)
  assert.equal(source.includes('filteredUsers'), false)
  assert.equal(source.includes('paginatedUsers'), false)
})

test('知识库 API 单一构造并编码文件上传端点', async () => {
  await withServer(async (server) => {
    const { fileApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')

    assert.equal(fileApi.getUploadUrl(), '/api/knowledge/files/upload')
    assert.equal(
      fileApi.getUploadUrl('kb/with space'),
      '/api/knowledge/files/upload?kb_id=kb%2Fwith%20space'
    )
  })
})

test('共享查询参数边界保留 false/0 并省略 undefined/null/空字符串', async () => {
  await withServer(async (server) => {
    const { buildQuery } = await server.ssrLoadModule('/src/apis/base.js')

    assert.equal(
      buildQuery({
        false_value: false,
        zero_value: 0,
        empty_value: '',
        null_value: null,
        undefined_value: undefined,
        text: 'a b/c'
      }),
      'false_value=false&zero_value=0&text=a+b%2Fc'
    )
  })
})

test('四个 API 模块复用查询参数边界并保持 endpoint 问号语义', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'test-token')
    const requests = []
    globalThis.fetch = async (url) => {
      requests.push(String(url))
      return new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }

    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
    useUserStore().userRole = 'admin'
    const { projectApi } = await server.ssrLoadModule('/src/apis/project_api.js')
    const { searchViewerFiles } = await server.ssrLoadModule('/src/apis/viewer_filesystem.js')
    const { getWorkspaceKnowledgeTree } = await server.ssrLoadModule('/src/apis/workspace_api.js')
    const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')

    await projectApi.getHistoryCandidates({ query: '', limit: 0, offset: 0 })
    await searchViewerFiles('thread-1', '')
    await getWorkspaceKnowledgeTree('kb-1', {
      page: 0,
      pageSize: false,
      recursive: false,
      filesOnly: false
    })
    await documentApi.listDocuments('kb-1', {
      page: 0,
      page_size: false,
      empty: '',
      ignored: null
    })
    await documentApi.listDocuments('kb-1')

    assert.deepEqual(requests, [
      '/api/projects/history-candidates?limit=0&offset=0',
      '/api/viewer/filesystem/search?thread_id=thread-1',
      '/api/workspace/knowledge/tree?kb_id=kb-1&page=0&page_size=false&recursive=false&files_only=false',
      '/api/knowledge/knowledge-bases/kb-1/documents?page=0&page_size=false',
      '/api/knowledge/knowledge-bases/kb-1/documents'
    ])
  })
})

test('个人空间文件夹上传发送根目录和逐文件相对路径，普通上传保留原协议', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'test-token')
    const requests = []
    globalThis.fetch = async (url, options) => {
      requests.push({ url, options })
      return new Response(JSON.stringify({ success: true }), {
        headers: { 'content-type': 'application/json' }
      })
    }
    const { uploadWorkspaceFiles } = await server.ssrLoadModule('/src/apis/workspace_api.js')
    const files = [new File(['alpha'], 'same.txt'), new File(['beta'], 'same.txt')]
    const paths = [' 根目录/甲/same.txt', ' 根目录/乙/same.txt']

    await uploadWorkspaceFiles('/目标目录', files, paths)
    await uploadWorkspaceFiles('/目标目录', files.slice(0, 1))

    assert.equal(requests[0].url, '/api/workspace/upload')
    assert.equal(requests[0].options.body.get('parent_path'), '/目标目录')
    assert.deepEqual(requests[0].options.body.getAll('relative_paths'), paths)
    const sent = requests[0].options.body.getAll('files')
    assert.deepEqual(sent.map((file) => file.name), ['same.txt', 'same.txt'])
    assert.deepEqual(await Promise.all(sent.map((file) => file.text())), ['alpha', 'beta'])
    assert.equal(requests[1].options.body.has('relative_paths'), false)
  })
})

test('Project 与 Workspace API 按 xhome 契约构造请求', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'test-token')
    const requests = []
    globalThis.fetch = async (url, options = {}) => {
      requests.push({ url, options })
      return new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' }
      })
    }

    setActivePinia(createPinia())
    const { projectApi } = await server.ssrLoadModule('/src/apis/project_api.js')
    const { createWorkspaceDirectory, getWorkspaceTree } = await server.ssrLoadModule(
      '/src/apis/workspace_api.js'
    )

    await projectApi.getProjects()
    await projectApi.createProject({
      requestId: 'request-1',
      name: '客户交付',
      mode: 'linked',
      path: '/clients/acme'
    })
    await projectApi.getHistoryCandidates({ query: '交付', limit: 10, offset: 20 })
    await getWorkspaceTree('/projects')
    await getWorkspaceTree('/projects', false, false, true)
    await createWorkspaceDirectory('/projects', '客户交付')

    assert.equal(requests[0].url, '/api/projects')
    assert.deepEqual(JSON.parse(requests[1].options.body), {
      request_id: 'request-1',
      name: '客户交付',
      workdir: { mode: 'linked', path: 'clients/acme' }
    })
    assert.equal(
      requests[2].url,
      '/api/projects/history-candidates?q=%E4%BA%A4%E4%BB%98&limit=10&offset=20'
    )
    assert.equal(
      requests[3].url,
      '/api/workspace/tree?path=%2Fprojects&recursive=false&files_only=false'
    )
    assert.equal(
      requests[4].url,
      '/api/workspace/tree?path=%2Fprojects&recursive=false&files_only=false&include_unbound_project_dirs=true'
    )
    assert.deepEqual(JSON.parse(requests[5].options.body), {
      parent_path: '/projects',
      name: '客户交付'
    })
  })
})

test('工具元数据 API 使用普通用户认证且普通用户可正常请求', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    storageValues.set('user_token', 'user-token')
    globalThis.fetch = async (url) => {
      assert.equal(url, '/api/system/tools')
      return new Response(
        JSON.stringify({ success: true, data: [{ name: '搜索', slug: 'web_search' }] }),
        {
          status: 200,
          headers: { 'content-type': 'application/json' }
        }
      )
    }

    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
    const userStore = useUserStore()
    userStore.token = 'user-token'
    userStore.role = 'user' // 非管理员

    const { toolApi } = await server.ssrLoadModule('/src/apis/tool_api.js')
    const result = await toolApi.getTools()
    assert.equal(result.success, true)
    assert.equal(result.data.length, 1)
    assert.equal(result.data[0].slug, 'web_search')
  })
})

test('服务端 5xx 文案不泄露部署命令且保留错误状态', async () => {
  await withServer(async (server) => {
    storageValues.set('user_token', 'test-token')
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ detail: 'internal details' }), {
        status: 500,
        headers: { 'content-type': 'application/json' }
      })

    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/modules/identity/model/user.js')
    useUserStore().token = 'test-token'
    const { apiGet } = await server.ssrLoadModule('/src/apis/base.js')

    await assert.rejects(apiGet('/api/failing'), (error) => {
      assert.equal(error.status, 500)
      assert.equal(error.message, '服务器内部错误，请稍后重试')
      assert.equal(error.message.includes('docker compose'), false)
      return true
    })
  })
})

test('Skill 详情通过实际 skillApi 对象读取隐藏的单个绑定资源', async () => {
  await withServer(async (server) => {
    globalThis.__apiBoundaryMessages = []
    let requestedPath
    globalThis.fetch = async (path) => {
      requestedPath = path
      return new Response(
        JSON.stringify({ data: { slug: 'agent-bound-fixture', bound_agent_id: 1 } }),
        {
          status: 200,
          headers: { 'content-type': 'application/json' }
        }
      )
    }
    const { skillApi } = await server.ssrLoadModule('/src/apis/skill_api.js')
    const detail = await skillApi.getSkillDetail('agent-bound-fixture')
    assert.equal(requestedPath, '/api/system/skills/agent-bound-fixture')
    assert.equal(detail.data.bound_agent_id, 1)
  })
})
