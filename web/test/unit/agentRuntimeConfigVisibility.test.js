import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { computed, ref } from 'vue'
import { compileScript, parse } from 'vue/compiler-sfc'
import * as configUtils from '../../src/utils/agentConfigUtils.js'

const source = readFileSync(
  new URL('../../src/components/AgentRuntimeConfigForm.vue', import.meta.url),
  'utf8'
)
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'resource-visibility-test' }).content
const executable = compiled
  .replace(/^import(?:\s*\{[\s\S]*?\}|\s+[A-Za-z]\w*)\s+from\s+'[^']+'\n/gm, '')
  .replace('export default', 'return')
  .replace(
    /const __returned__ = \{[\s\S]*?\}\nObject\.defineProperty/,
    'const __returned__ = { filteredConfigurableItems, isCurrentSegmentEmpty }\nObject.defineProperty'
  )

/** 使用真实组件 setup 验证筛选结果，保留响应式资源与配置。 */
function createForm(selection) {
  const item = { name: '知识库', kind: 'knowledges', type: 'list', supports_all: true, options: [] }
  const state = {
    selectedAgent: ref({ can_manage: true }),
    selectedAgentId: ref('test-agent'),
    agentConfig: ref({ knowledges: selection }),
    configurableItems: ref({ knowledges: item })
  }
  const deps = {
    ...configUtils,
    ref,
    computed,
    useAgentStore: () => ({}),
    useRouter: () => ({}),
    storeToRefs: () => state,
    getOptionValue: configUtils.getAgentConfigOptionValue
  }
  const component = new Function(...Object.keys(deps), executable)(...Object.values(deps))
  const form = component.setup({ segment: 'tools', showSegmented: false }, { expose() {} })
  return { state, form }
}

for (const selection of ['all', []]) {
  test(`空资源分组隐藏且保留配置 ${JSON.stringify(selection)}，候选项新增后显示`, () => {
    const { state, form } = createForm(selection)
    assert.deepEqual(form.filteredConfigurableItems.value, {})
    assert.equal(form.isCurrentSegmentEmpty.value, true)
    assert.deepEqual(state.agentConfig.value.knowledges, selection)
    state.configurableItems.value.knowledges.options.push({ value: 'kb-a', label: '知识库 A' })
    assert.ok(form.filteredConfigurableItems.value.knowledges)
    assert.equal(form.isCurrentSegmentEmpty.value, false)
    assert.deepEqual(state.agentConfig.value.knowledges, selection)
  })
}

test('没有候选项时仍保留历史固定选择的提示与清空入口', () => {
  const { state, form } = createForm(['hidden-kb'])
  assert.ok(form.filteredConfigurableItems.value.knowledges)
  assert.deepEqual(state.agentConfig.value.knowledges, ['hidden-kb'])
})
