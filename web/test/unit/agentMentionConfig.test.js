import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { createServer } from 'vite'

test('资源 mention 按智能体选择生成，不把预加载 Skills 当成可提及资源', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  try {
    const { useAgentMentionConfig } = await server.ssrLoadModule(
      '/src/composables/useAgentMentionConfig.js'
    )
    const getMentionMcps = (agentConfig) => {
      const { mentionConfig } = useAgentMentionConfig({
        currentAgentState: ref({}),
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
        agentConfig: ref(agentConfig)
      })
      return mentionConfig.value.mcps
    }

    assert.deepEqual(getMentionMcps({}), [])
    assert.deepEqual(getMentionMcps({ mcps: null }), [])
    assert.deepEqual(getMentionMcps({ mcps: ['mcp-b'] }), [
      { slug: 'mcp-b', name: 'MCP B', description: '' }
    ])

    const getMentionSkills = (agentConfig) => {
      const options = [
        { key: 'skill-a', name: 'Skill A' },
        { key: 'skill-b', name: 'Skill B' }
      ]
      const { mentionConfig } = useAgentMentionConfig({
        currentAgentState: ref({}),
        currentThreadAttachments: ref([]),
        configurableItems: ref({
          skills: { kind: 'skills', options },
          preload_skills: { kind: 'skills', options }
        }),
        agentConfig: ref(agentConfig)
      })
      return mentionConfig.value.skills
    }

    assert.deepEqual(getMentionSkills({ skills: [], preload_skills: ['skill-a'] }), [])
    assert.deepEqual(getMentionSkills({ skills: ['skill-b'], preload_skills: ['skill-a'] }), [
      { slug: 'skill-b', name: 'Skill B', description: '' }
    ])
    assert.deepEqual(getMentionSkills({ skills: null, preload_skills: [] }), [
      { slug: 'skill-a', name: 'Skill A', description: '' },
      { slug: 'skill-b', name: 'Skill B', description: '' }
    ])
    assert.deepEqual(getMentionSkills({ preload_skills: [] }), [
      { slug: 'skill-a', name: 'Skill A', description: '' },
      { slug: 'skill-b', name: 'Skill B', description: '' }
    ])

    const getMentionSubagents = (agentConfig) => {
      const { mentionConfig } = useAgentMentionConfig({
        currentAgentState: ref({}),
        currentThreadAttachments: ref([]),
        configurableItems: ref({
          subagents: {
            kind: 'subagents',
            options: [
              { key: 'sub-a', name: 'Sub A' },
              { key: 'sub-b', name: 'Sub B' }
            ]
          }
        }),
        agentConfig: ref(agentConfig)
      })
      return mentionConfig.value.subagents
    }
    const allSubagents = [
      { id: 'sub-a', slug: 'sub-a', name: 'Sub A', description: '' },
      { id: 'sub-b', slug: 'sub-b', name: 'Sub B', description: '' }
    ]
    assert.deepEqual(getMentionSubagents({ subagents: null }), allSubagents)
    assert.deepEqual(getMentionSubagents({ subagents: [] }), allSubagents)
    assert.deepEqual(getMentionSubagents({ subagents: ['sub-b'] }), [allSubagents[1]])
  } finally {
    await server.close()
  }
})
