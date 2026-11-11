import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

test('文件 mention 来自附件元数据并按路径去重', async () => {
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { useAgentMentionConfig } = await server.ssrLoadModule(
      '/src/modules/session/model/useAgentMentionConfig.js'
    )
    const attachments = ref([
      {
        path: '/home/user/workspace/projects/example/report.txt',
        file_name: 'report.txt',
        file_size: 42,
        uploaded_at: '2026-09-30T00:00:00Z',
        artifact_url: '/api/example/artifact',
        status: 'ready'
      },
      { path: '/home/user/workspace/projects/example/report.txt', file_name: 'duplicate.txt' },
      { file_name: 'missing-path.txt' }
    ])
    const { mentionConfig } = useAgentMentionConfig({
      currentThreadAttachments: attachments,
      configurableItems: ref({}),
      agentConfig: ref({})
    })

    assert.deepEqual(mentionConfig.value.files, [
      {
        path: '/home/user/workspace/projects/example/report.txt',
        file_name: 'report.txt',
        size: 42,
        modified_at: '2026-09-30T00:00:00Z',
        artifact_url: '/api/example/artifact',
        status: 'ready'
      }
    ])
    attachments.value = []
    assert.deepEqual(mentionConfig.value.files, [])
  } finally {
    await server.close()
  }
})

test('连接器 mention 来自本人可用目录，Skills 仍按智能体选择生成', async () => {
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { useAgentMentionConfig } = await server.ssrLoadModule(
      '/src/modules/session/model/useAgentMentionConfig.js'
    )
    const getMentionMcps = (agentConfig) => {
      const { mentionConfig } = useAgentMentionConfig({
        currentThreadAttachments: ref([]),
        configurableItems: ref({
          mcps: {
            kind: 'mcps',
            options: [
              { key: 'mcp-a', name: 'MCP A' },
              { key: 'mcp-b', name: 'MCP B' }
            ]
          }
        }),
        agentConfig: ref(agentConfig),
        mcpServers: ref([
          { slug: 'mcp-a', name: '连接器 A', available: true },
          { slug: 'mcp-b', name: '连接器 B', available: true },
          { slug: 'disabled', available: false },
          { slug: 'other-user', available: false },
          { slug: 'revoked', available: false },
          { slug: 'knowledge', enabled: true, connection_status: 'not_required', available: false }
        ])
      })
      return mentionConfig.value.mcps
    }

    const expected = [
      { slug: 'mcp-a', name: '连接器 A', description: '' },
      { slug: 'mcp-b', name: '连接器 B', description: '' }
    ]
    assert.deepEqual(getMentionMcps({}), expected)
    assert.deepEqual(getMentionMcps({ mcps: [] }), expected)
    assert.deepEqual(getMentionMcps({ mcps: ['mcp-b'] }), expected)
    assert.deepEqual(getMentionMcps({ mcps: 'all' }), expected)

    const getMentionSkills = (agentConfig) => {
      const options = [
        { key: 'skill-a', name: 'Skill A' },
        { key: 'skill-b', name: 'Skill B' }
      ]
      const { mentionConfig } = useAgentMentionConfig({
        currentThreadAttachments: ref([]),
        configurableItems: ref({
          skills: { kind: 'skills', options, default: 'all', supports_all: true },
          preload_skills: { kind: 'skills', options, default: 'all', supports_all: true }
        }),
        agentConfig: ref(agentConfig)
      })
      return mentionConfig.value.skills
    }

    assert.deepEqual(getMentionSkills({ skills: [], preload_skills: ['skill-a'] }), [])
    assert.deepEqual(getMentionSkills({ skills: ['skill-b'], preload_skills: ['skill-a'] }), [
      { slug: 'skill-b', name: 'Skill B', description: '' }
    ])
    assert.deepEqual(getMentionSkills({ skills: 'all', preload_skills: [] }), [
      { slug: 'skill-a', name: 'Skill A', description: '' },
      { slug: 'skill-b', name: 'Skill B', description: '' }
    ])
    assert.deepEqual(getMentionSkills({ preload_skills: [] }), [
      { slug: 'skill-a', name: 'Skill A', description: '' },
      { slug: 'skill-b', name: 'Skill B', description: '' }
    ])
  } finally {
    await server.close()
  }
})

test('智能体 mention 只包含可运行目录并响应目录更新', async () => {
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  })
  try {
    const { useAgentMentionConfig } = await server.ssrLoadModule(
      '/src/modules/session/model/useAgentMentionConfig.js'
    )
    const agents = ref([
      { agent_id: 'researcher', name: '研究员', can_run: true },
      { agent_id: 'private', name: '不可运行', can_run: false },
      { name: '缺少标识', can_run: true }
    ])
    const { mentionConfig } = useAgentMentionConfig({
      currentThreadAttachments: ref([]),
      configurableItems: ref({}),
      agentConfig: ref({}),
      agents
    })
    assert.deepEqual(mentionConfig.value.agents, [agents.value[0]])
    agents.value = []
    assert.deepEqual(mentionConfig.value.agents, [])
  } finally {
    await server.close()
  }
})
