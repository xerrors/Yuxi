import assert from 'node:assert/strict'
import test from 'node:test'

import { createAgentResources } from '../../src/utils/agentCreateResources.js'

const progress = () => ({ createdMcpSlugs: [], createdMcpConfigs: {}, agent: null, skillUploaded: false, uncertainStep: '' })

test('Agent 请求已提交但响应丢失时禁止重试创建', async () => {
  const state = progress()
  let created = 0
  const api = {
    listMcps: async () => ({ data: [] }),
    createMcp: async () => {},
    createAgent: async () => { created += 1; throw new TypeError('Network request failed') },
    uploadSkill: async () => {}
  }
  const input = { payload: { slug: 'same-agent' }, servers: [], skillFile: null, progress: state, api }
  await assert.rejects(createAgentResources(input), /Network request failed/)
  assert.equal(state.uncertainStep, '智能体')
  await assert.rejects(createAgentResources(input), /结果未确认/)
  assert.equal(created, 1)
})

test('MCP 响应丢失时禁止重复创建', async () => {
  const state = progress()
  let created = 0
  const api = {
    listMcps: async () => ({ data: [] }),
    createMcp: async () => { created += 1; throw new TypeError('Network request failed') },
    createAgent: async () => ({ id: 'agent' }),
    uploadSkill: async () => {}
  }
  const input = { payload: {}, servers: [{ slug: 'remote' }], skillFile: null, progress: state, api }
  await assert.rejects(createAgentResources(input), /Network request failed/)
  assert.equal(state.uncertainStep, 'MCP「remote」')
  await assert.rejects(createAgentResources(input), /结果未确认/)
  assert.equal(created, 1)
})

test('明确拒绝的 ZIP 上传可只重试剩余步骤', async () => {
  const state = progress()
  let mcpCreates = 0
  let agentCreates = 0
  let uploads = 0
  const api = {
    listMcps: async () => ({ data: [] }),
    createMcp: async () => { mcpCreates += 1 },
    createAgent: async () => { agentCreates += 1; return { id: 'agent' } },
    uploadSkill: async () => {
      uploads += 1
      if (uploads === 1) throw Object.assign(new Error('Invalid ZIP'), { status: 422 })
    }
  }
  const input = { payload: {}, servers: [{ slug: 'remote' }], skillFile: { name: 'skill.zip' }, progress: state, api }
  await assert.rejects(createAgentResources(input), /Invalid ZIP/)
  assert.equal(state.uncertainStep, '')
  await createAgentResources(input)
  assert.deepEqual([mcpCreates, agentCreates, uploads], [1, 1, 2])
})

test('ZIP 明确拒绝后取消可选上传，直接完成已创建的智能体', async () => {
  const state = progress()
  let agentCreates = 0
  let uploads = 0
  const api = {
    listMcps: async () => ({ data: [] }),
    createMcp: async () => {},
    createAgent: async () => { agentCreates += 1; return { id: 'agent' } },
    uploadSkill: async () => {
      uploads += 1
      throw Object.assign(new Error('Invalid ZIP'), { status: 422 })
    }
  }
  const input = { payload: {}, servers: [], skillFile: { name: 'bad.zip' }, progress: state, api }
  await assert.rejects(createAgentResources(input), /Invalid ZIP/)
  input.skillFile = null
  const created = await createAgentResources(input)
  assert.equal(created.id, 'agent')
  assert.deepEqual([agentCreates, uploads], [1, 1])
})

test('后续 MCP 明确冲突时可以修正未创建条目，不能改已创建条目', async () => {
  const state = progress()
  const created = []
  const api = {
    listMcps: async () => ({ data: [] }),
    createMcp: async (server) => {
      if (server.slug === 'conflict') throw Object.assign(new Error('Exists'), { status: 400 })
      created.push(server.slug)
    },
    createAgent: async () => ({ id: 'agent' }),
    uploadSkill: async () => {}
  }
  const first = { slug: 'first', transport: 'streamable_http', url: 'https://example.com/first' }
  const input = { payload: {}, servers: [first, { slug: 'conflict' }], skillFile: null, progress: state, api }
  await assert.rejects(createAgentResources(input), /Exists/)
  assert.deepEqual(state.createdMcpSlugs, ['first'])
  assert.equal(state.uncertainStep, '')

  input.servers = [{ ...first, url: 'https://example.com/changed' }, { slug: 'fixed' }]
  await assert.rejects(createAgentResources(input), /不能从清单移除或修改/)
  assert.deepEqual(created, ['first'])

  input.servers = [first, { slug: 'fixed' }]
  await createAgentResources(input)
  assert.deepEqual(created, ['first', 'fixed'])
  assert.equal(state.agent.id, 'agent')
})
