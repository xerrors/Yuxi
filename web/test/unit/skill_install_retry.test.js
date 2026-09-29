import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'

const source = readFileSync(
  new URL('../../src/components/extensions/SkillInstallFlowModal.vue', import.meta.url),
  'utf8'
)
const retrySource = source.slice(
  source.indexOf('const retryFailedItems ='),
  source.indexOf('const finishFlow =')
)
const installSource = source.slice(
  source.indexOf('const installDrafts = async'),
  source.indexOf('const applyInstallResults =')
)

test('安装失败重试复用草稿，只选择失败项，保留用户移除的条目不被安装', () => {
  const context = {
    drafts: {
      value: [{ draft_id: 'draft-a', items: [{ slug: 'failed' }, { slug: 'removed' }] }]
    },
    failedInstallItems: {
      value: [{ draft_id: 'draft-a', slug: 'failed', source_type: 'upload', status: 'failed', retryable: true }]
    },
    reviewItems: { value: [] },
    flowError: { value: '' },
    phase: { value: 'result' },
    openReview: () => assert.fail('不能重新选择全部草稿条目'),
    prepareRequests: () => assert.fail('已有上传草稿不应重新获取来源')
  }

  runInNewContext(`${retrySource}\nretryFailedItems()`, context)

  assert.equal(context.phase.value, 'reviewing')
  assert.deepEqual(Array.from(context.reviewItems.value, (item) => item.slug), ['failed'])
  assert.equal(context.reviewItems.value[0].success, true)
  assert.deepEqual(context.drafts.value[0].items.map((item) => item.slug), ['failed', 'removed'])
})

test('重试安装失败项时保留不能从当前草稿重试的解析失败项', () => {
  const context = {
    drafts: {
      value: [{ draft_id: 'draft-a', items: [{ slug: 'failed' }] }]
    },
    failedInstallItems: {
      value: [
        { draft_id: 'draft-a', slug: 'invalid', status: 'failed', retryable: false, error: '解析失败' },
        { draft_id: 'draft-a', slug: 'failed', status: 'failed', retryable: true }
      ]
    },
    reviewItems: { value: [] },
    flowError: { value: '' },
    phase: { value: 'result' },
    openReview: () => assert.fail('不能重新选择全部草稿条目'),
    prepareRequests: () => assert.fail('已有上传草稿不应重新获取来源')
  }

  runInNewContext(`${retrySource}\nretryFailedItems()`, context)

  assert.deepEqual(
    Array.from(context.reviewItems.value, (item) => [item.slug, item.success]),
    [
      ['invalid', false],
      ['failed', true]
    ]
  )
})

test('安装结果继续显示预览阶段已经发现的解析失败项', async () => {
  const context = {
    installTarget: { value: 'personal' },
    shareConfigFormRef: { value: null },
    shareConfig: { value: {} },
    readyItems: { value: [{ draft_id: 'draft-a', slug: 'valid', success: true }] },
    reviewItems: {
      value: [
        { draft_id: 'draft-a', slug: 'valid', success: true, retryable: true },
        { draft_id: 'draft-a', slug: 'invalid', success: false, retryable: false, error: '解析失败' }
      ]
    },
    drafts: { value: [{ draft_id: 'draft-a', items: [{ slug: 'valid' }] }] },
    installItems: { value: [] },
    flowError: { value: '' },
    phase: { value: 'reviewing' },
    skillApi: {
      confirmPersonalSkillInstallDraft: async () => ({
        data: [{ success: true, slug: 'valid', requested_slug: 'valid' }]
      })
    },
    applyInstallResults: () => {},
    forgetDraft: () => {},
    discardDrafts: async () => {}
  }
  context.applyInstallResults = (results, draftId) => {
    for (const result of results) {
      const item = context.installItems.value.find(
        (candidate) => candidate.slug === (result.requested_slug || result.slug) && candidate.draft_id === draftId
      )
      Object.assign(item, { status: result.success ? 'success' : 'failed', error: result.error || '' })
    }
  }
  context.forgetDraft = (draftId) => {
    context.drafts.value = context.drafts.value.filter((draft) => draft.draft_id !== draftId)
  }

  await runInNewContext(`${installSource}\ninstallDrafts()`, context)

  assert.deepEqual(
    Array.from(context.installItems.value, (item) => [item.slug, item.status]),
    [
      ['valid', 'success'],
      ['invalid', 'failed']
    ]
  )
})

test('草稿仅剩用户移除条目时重新获取远程失败项，不重新选择已移除条目', async () => {
  const requests = []
  const discarded = []
  const context = {
    drafts: { value: [{ draft_id: 'draft-a', items: [{ slug: 'removed' }] }] },
    failedInstallItems: {
      value: [{ draft_id: 'draft-a', slug: 'failed', source_type: 'remote', source: 'owner/repo', retryable: false }]
    },
    reviewItems: { value: [] },
    flowError: { value: '' },
    phase: { value: 'result' },
    discardDrafts: async () => {
      discarded.push(...context.drafts.value.map((draft) => draft.draft_id))
      context.drafts.value = []
    },
    prepareRequests: async (items) => {
      assert.deepEqual(discarded, ['draft-a'])
      requests.push(...items)
      context.reviewItems.value = items.flatMap((item) => item.skills.map((slug) => ({ slug, success: true })))
      context.phase.value = 'reviewing'
    }
  }

  await runInNewContext(`${retrySource}\nretryFailedItems()`, context)

  assert.deepEqual(JSON.parse(JSON.stringify(requests)), [{ source: 'owner/repo', skills: ['failed'] }])
  assert.equal(context.phase.value, 'reviewing')
  assert.deepEqual(Array.from(context.reviewItems.value, (item) => item.slug), ['failed'])
  assert.equal(context.reviewItems.value[0].success, true)
})
