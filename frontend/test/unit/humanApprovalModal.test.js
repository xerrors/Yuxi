import assert from 'node:assert/strict'
import test from 'node:test'

import { createRenderer, h, nextTick, ref } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'
import { compileScript, parse } from 'vue/compiler-sfc'
import { readFileSync } from 'node:fs'

const makeNode = (type) => {
  const node = { type, children: [], props: {}, style: {}, parent: null, text: '' }
  node.focus = () => {
    node.focused = true
  }
  return node
}

const renderer = createRenderer({
  createElement: makeNode,
  createText(text) {
    return { ...makeNode('text'), text }
  },
  createComment(text) {
    return { ...makeNode('comment'), text }
  },
  insert(child, parent, anchor = null) {
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
  },
  setText(node, text) {
    node.text = text
  },
  setElementText(node, text) {
    node.text = text
    node.children = []
  },
  parentNode: (node) => node.parent,
  nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  patchProp(node, key, _oldValue, value) {
    node.props[key] = value
  }
})

const find = (node, predicate) => {
  if (predicate(node)) return node
  for (const child of node.children || []) {
    const match = find(child, predicate)
    if (match) return match
  }
  return undefined
}

const textContent = (node) =>
  [node.text, ...(node.children || []).map((child) => textContent(child))].join('')

test('问题弹窗按顺序收集全部答案', async () => {
  globalThis.window = {
    getComputedStyle: () => ({
      lineHeight: '20',
      paddingTop: '0',
      paddingBottom: '0',
      borderTopWidth: '0',
      borderBottomWidth: '0'
    })
  }

  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    plugins: [
      {
        name: 'human-approval-modal-test',
        resolveId(id) {
          if (id === 'virtual:human-approval-modal-test') return `\0${id}`
        },
        load(id) {
          if (id !== '\0virtual:human-approval-modal-test') return
          const source = readFileSync(
            new URL('../../src/modules/session/ui/HumanApprovalModal.vue', import.meta.url),
            'utf8'
          )
          const { descriptor } = parse(source)
          return compileScript(descriptor, {
            id: 'human-approval-modal-test',
            inlineTemplate: true
          }).content
        }
      }
    ]
  })

  let app
  try {
    const { default: HumanApprovalModal } = await server.ssrLoadModule(
      'virtual:human-approval-modal-test'
    )
    const submissions = []
    const wireQuestions = [
      {
        question_id: 'destination',
        question: '你想去哪个城市？',
        options: [],
        multi_select: false,
        allow_other: true
      },
      {
        question_id: 'season',
        question: '你最喜欢哪个季节？',
        options: [
          { label: '春天', value: '春天' },
          { label: '夏天', value: '夏天' }
        ],
        multi_select: false,
        allow_other: true
      }
    ]
    const { pendingInterruptFromWaitpoint, extractPendingInterrupt } = await server.ssrLoadModule(
      '/src/modules/session/model/useApproval.js'
    )
    const pending = pendingInterruptFromWaitpoint(
      {
        id: 'waitpoint-1',
        run_id: 'run-1',
        kind: 'answer',
        questions: wireQuestions
      },
      'thread-1'
    )
    const streamed = extractPendingInterrupt(
      {
        status: 'ask_user_question_required',
        questions: wireQuestions,
        run_id: 'run-1'
      },
      'thread-1'
    )
    assert.equal(pending.questions, wireQuestions)
    assert.equal(streamed.questions, wireQuestions)
    const questions = pending.questions
    const host = makeNode('root')
    app = renderer.createApp(() =>
      h(HumanApprovalModal, {
        visible: true,
        questions,
        onSubmit: (answer) => submissions.push(answer)
      })
    )
    app.mount(host)
    await nextTick()

    const answer = find(host, (node) => node.props.placeholder === '请输入你的回答…')
    assert.ok(answer)
    answer.props.onInput({ target: { value: '  杭州  ' } })
    await nextTick()

    const next = find(
      host,
      (node) => node.type === 'button' && node.props.class === 'btn btn-approve'
    )
    assert.equal(textContent(next), '下一步')
    assert.equal(next.props.disabled, false)
    assert.equal(next.parent?.parent?.props.class, 'question-footer')
    next.props.onClick()
    await nextTick()

    assert.ok(find(host, (node) => node.props.placeholder === '或自行填写回答'))
    const spring = find(host, (node) => node.type === 'input' && node.props.value === '春天')
    spring.props.onChange()
    await nextTick()
    const submit = find(
      host,
      (node) => node.type === 'button' && node.props.class === 'btn btn-approve'
    )
    assert.equal(textContent(submit), '提交')
    assert.equal(submit.parent?.parent?.props.class, 'question-footer')
    submit.props.onClick()
    assert.deepEqual(submissions, [{ destination: '杭州', season: '春天' }])

    app.unmount()
    submissions.length = 0
    const customHost = makeNode('root')
    app = renderer.createApp(() =>
      h(HumanApprovalModal, {
        visible: true,
        questions,
        onSubmit: (answerValue) => submissions.push(answerValue)
      })
    )
    app.mount(customHost)
    await nextTick()
    find(customHost, (node) => node.props.placeholder === '请输入你的回答…').props.onInput({
      target: { value: '杭州' }
    })
    await nextTick()
    find(
      customHost,
      (node) => node.type === 'button' && node.props.class === 'btn btn-approve'
    ).props.onClick()
    await nextTick()
    const customAnswer = find(customHost, (node) => node.props.placeholder === '或自行填写回答')
    customAnswer.props.onInput({ target: { value: '  秋天  ' } })
    await nextTick()
    find(
      customHost,
      (node) => node.type === 'button' && node.props.class === 'btn btn-approve'
    ).props.onClick()

    assert.deepEqual(submissions, [
      { destination: '杭州', season: { type: 'other', text: '秋天', selected: [] } }
    ])

    app.unmount()
    submissions.length = 0
    const multipleHost = makeNode('root')
    app = renderer.createApp(() =>
      h(HumanApprovalModal, {
        visible: true,
        questions: [
          {
            question_id: 'cities',
            question: '你想去哪些城市？',
            options: [
              { label: '杭州', value: '杭州' },
              { label: '上海', value: '上海' }
            ],
            multi_select: true,
            allow_other: true
          }
        ],
        onSubmit: (answerValue) => submissions.push(answerValue)
      })
    )
    app.mount(multipleHost)
    await nextTick()
    await nextTick()

    const questionTitle = find(multipleHost, (node) => node.props.id === 'question-dialog-title')
    assert.equal(questionTitle.focused, true)

    const multipleCustomAnswer = find(
      multipleHost,
      (node) => node.props.placeholder === '或自行填写回答'
    )
    multipleCustomAnswer.props.onInput({ target: { value: '苏州' } })
    await nextTick()
    find(
      multipleHost,
      (node) => node.type === 'input' && node.props.value === '杭州'
    ).props.onChange()
    await nextTick()

    const multipleSubmit = find(
      multipleHost,
      (node) => node.type === 'button' && node.props.class === 'btn btn-approve'
    )
    assert.equal(multipleSubmit.props.disabled, false)
    multipleSubmit.props.onClick()
    assert.deepEqual(submissions, [{ cities: ['杭州'] }])

    app.unmount()
    submissions.length = 0
    const approvalHost = makeNode('root')
    const processing = ref(false)
    app = renderer.createApp(() =>
      h(HumanApprovalModal, {
        visible: true,
        kind: 'tool_approval',
        processing: processing.value,
        actionRequests: [
          { name: 'write_file', args: { file_path: 'result.txt', content: 'result' } }
        ],
        onSubmit: (decision) => {
          submissions.push(decision)
          processing.value = true
        }
      })
    )
    app.mount(approvalHost)
    await nextTick()
    const approve = () =>
      find(approvalHost, (node) => node.type === 'button' && node.props.class === 'btn btn-approve')
    approve().props.onClick()
    await nextTick()
    assert.equal(approve().props.disabled, true)
    processing.value = false
    await nextTick()
    assert.equal(approve().props.disabled, false, '提交失败后同一卡片可重试，不需要关掉再打开')
    approve().props.onClick()
    await nextTick()
    assert.deepEqual(submissions, [
      { decisions: [{ type: 'approve' }] },
      { decisions: [{ type: 'approve' }] }
    ])
  } finally {
    app?.unmount()
    await server.close()
    delete globalThis.window
  }
})
