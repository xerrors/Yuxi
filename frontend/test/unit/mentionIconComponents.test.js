import assert from 'node:assert/strict'
import test from 'node:test'
import { BookOpenText, Computer, Plug, Search, WandSparkles } from '@lucide/vue'
import { getMentionIconComponent } from '../../src/modules/session/model/mention_icon_utils.js'

test('连接器提及使用与目录一致的用途图标，未知连接器保留插头', () => {
  assert.equal(getMentionIconComponent('mcp', 'abridge', 'aBridge'), Computer)
  assert.equal(getMentionIconComponent('mcp', 'weknora', 'WeKnora 知识检索'), Search)
  assert.equal(getMentionIconComponent('mcp', 'knowledge', '知识库'), BookOpenText)
  assert.equal(getMentionIconComponent('mcp', 'custom', '知识库'), BookOpenText)
  assert.equal(getMentionIconComponent('mcp', 'custom', '自定义连接器'), Plug)
  assert.equal(getMentionIconComponent('skill', 'custom', '知识库'), WandSparkles)
})
