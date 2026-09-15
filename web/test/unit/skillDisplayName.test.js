import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { parse } from 'vue/compiler-sfc'
import { computed, ref } from 'vue'

const source = readFileSync(
  new URL('../../src/components/extensions/SkillDisplayNameButton.vue', import.meta.url),
  'utf8'
)
const script = parse(source).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')

function panel(skill, update = async () => {}, refresh = async () => {}) {
  const events = []
  const setup = new Function(
    'computed',
    'ref',
    'defineProps',
    'defineEmits',
    'skillApi',
    'useAgentStore',
    'message',
    `${script}; return { startEditing, save, editing, name, error, canRename }`
  )
  return {
    events,
    ...setup(
      computed,
      ref,
      () => ({ skill }),
      () => (event) => events.push(event),
      {
        updateSkillDisplayName: async (...args) => {
          events.push(args)
          await update()
        }
      },
      () => ({
        refreshAvailableSkills: async () => {
          await refresh()
          events.push('refreshed')
        }
      }),
      {
        success() {},
        warning(text) {
          events.push(text)
        }
      }
    )
  }
}

for (const scope of ['personal', 'shared', 'builtin']) {
  test(`${scope} display name saves slug and refreshes mentions before publishing`, async () => {
    const p = panel({ slug: 'pptx', name: 'Original', sourceScope: scope, can_manage: true })
    p.startEditing()
    assert.equal(p.name.value, 'Original')
    p.name.value = ' 中文演示 '
    await p.save()
    assert.deepEqual(p.events, [
      ['pptx', '中文演示', { personal: scope === 'personal' }],
      'refreshed',
      'saved'
    ])
    assert.equal(p.editing.value, false)
  })
}

test('failed save preserves draft without refreshing or emitting saved', async () => {
  const p = panel({ slug: 'pptx', name: 'Original' }, async () => {
    throw new Error('服务器拒绝保存')
  })
  p.startEditing()
  p.name.value = 'Draft'
  await p.save()
  assert.equal(p.editing.value, true)
  assert.equal(p.name.value, 'Draft')
  assert.equal(p.error.value, '服务器拒绝保存')
  assert.equal(p.events.length, 1)
})

test('read-only skills do not expose or execute rename', async () => {
  for (const skill of [{ can_manage: false }, { source_type: 'builtin', can_manage: false }]) {
    const p = panel({ slug: 'pptx', name: 'Original', ...skill })
    assert.equal(p.canRename.value, false)
    p.name.value = 'New'
    await p.save()
    assert.deepEqual(p.events, [])
  }
})

test('invalid length stays in editor without API writes', async () => {
  for (const value of ['', '   ', 'x'.repeat(129)]) {
    const p = panel({ slug: 'pptx', name: 'Original' })
    p.startEditing()
    p.name.value = value
    await p.save()
    assert.equal(p.editing.value, true)
    assert.ok(p.error.value)
    assert.deepEqual(p.events, [])
  }
})

test('successful write with failed mention refresh reports saved, not failed write', async () => {
  const p = panel(
    { slug: 'pptx', name: 'Original' },
    async () => {},
    async () => {
      throw new Error('offline')
    }
  )
  p.startEditing()
  p.name.value = '新名称'
  await p.save()
  assert.equal(p.editing.value, false)
  assert.equal(p.error.value, '')
  assert.ok(p.events.some((event) => typeof event === 'string' && event.includes('显示名称已保存')))
  assert.equal(p.events.at(-1), 'saved')
})

test('skill-only refresh preserves cached candidates when retrieval fails', async () => {
  const store = readFileSync(new URL('../../src/stores/agent.js', import.meta.url), 'utf8')
  const body = store.match(/async function refreshAvailableSkills\(\) \{([\s\S]*?)\n {4}\}/)[1]
  const cached = ref([{ slug: 'pptx', name: '旧名称' }])
  const run = new Function('skillApi', 'availableSkills', `return (async () => {${body}})()`)
  await assert.rejects(
    run(
      {
        listAccessibleSkills: async () => {
          throw new Error('offline')
        }
      },
      cached
    )
  )
  assert.equal(cached.value[0].name, '旧名称')
  await run(
    { listAccessibleSkills: async () => ({ data: [{ slug: 'pptx', name: '新名称' }] }) },
    cached
  )
  assert.deepEqual(cached.value, [{ slug: 'pptx', name: '新名称' }])
})

test('preview reload selects the same source when personal and shared slugs collide', async () => {
  const list = readFileSync(
    new URL('../../src/components/extensions/SkillCardList.vue', import.meta.url),
    'utf8'
  )
  const body = list.match(/const refreshRenamedPreview = async \(\) => \{([\s\S]*?)\n\}/)[1]
  const old = { slug: 'pptx', name: 'Old', sourceScope: 'shared' }
  const candidates = [
    { slug: 'pptx', name: 'Personal', sourceScope: 'personal' },
    { slug: 'pptx', name: '共享新名', sourceScope: 'shared' }
  ]
  let opened
  const run = new Function(
    'previewSkill',
    'fetchSkills',
    'installedSkillCards',
    'openSkillPreview',
    'skillPreviewVisible',
    `return (async () => {${body}})()`
  )
  await run(
    ref(old),
    async () => {},
    ref(candidates),
    async (skill) => {
      opened = skill
    },
    ref(true)
  )
  assert.equal(opened.name, '共享新名')
})

test('rename detail refresh keeps unrelated unsaved settings', () => {
  const detail = readFileSync(
    new URL('../../src/components/extensions/SkillDetailView.vue', import.meta.url),
    'utf8'
  )
  const body = detail.match(/const refreshDisplayName = async \(\) => \{([\s\S]*?)\n\}/)[1]
  assert.doesNotMatch(body, /syncShareConfig|syncDependencyForm|fetchSkillDetail/)
})
