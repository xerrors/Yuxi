import assert from 'node:assert/strict'
import test from 'node:test'
import { truncateMessageDebugToolValue } from '../../src/modules/session/model/messageDebugTokens.js'
import { buildMessageDebugEntries, serializeMessageDebugTurn } from '../../src/modules/session/model/messageDebug.js'

test('工具值按 token 首 200 尾 100 截断，300 token 边界不截断', () => {
  // cl100k_base 对重复的「 a」每段编码为一个 token；期望值不调用被测 tokenizer。
  const boundary = ' a'.repeat(300)
  assert.equal(truncateMessageDebugToolValue(boundary), boundary)
  assert.equal(
    truncateMessageDebugToolValue(' a'.repeat(201) + ' b'.repeat(100)),
    ' a'.repeat(200) + '\n… [已省略中间内容] …\n' + ' b'.repeat(100)
  )
  const small = { path: '/a', enabled: true }
  assert.deepEqual(truncateMessageDebugToolValue(small), small)
  assert.equal(truncateMessageDebugToolValue('<|endoftext|>'), '<|endoftext|>')
  const unicode = truncateMessageDebugToolValue('😀中文'.repeat(400))
  assert.ok(unicode.includes('已省略中间内容'))
  assert.ok(!unicode.includes('\uFFFD'))
})

test('精简导出分别截断工具参数、结果和未执行调用参数，不截断用户或模型正文', async () => {
  const long = ' a'.repeat(400)
  const records = [
    { type: 'human', content: long },
    { type: 'ai', content: long, tool_calls: [{ id: 'pending', name: 'pending_tool', args: { text: long } }] },
    { type: 'tool', tool_name: 'read_file', tool_input: { text: long }, content: long }
  ]
  const exported = JSON.parse(await serializeMessageDebugTurn({ items: buildMessageDebugEntries(records) }, 'compact'))
  assert.equal(exported.messages[0].content, long)
  assert.equal(exported.messages[1].content, long)
  assert.ok(exported.messages[1].tool_calls[0].arguments.includes('已省略中间内容'))
  assert.ok(exported.messages[2].arguments.includes('已省略中间内容'))
  assert.equal(exported.messages[2].result, ' a'.repeat(200) + '\n… [已省略中间内容] …\n' + ' a'.repeat(100))
})
