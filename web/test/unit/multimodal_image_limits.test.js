import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_MULTIMODAL_IMAGES,
  MAX_MULTIMODAL_TOTAL_BASE64_BYTES,
  isWithinBase64Budget,
  remainingImageSlots,
  removeUploadedImage,
  reserveImageSlots,
  settleUploadedImage,
  splitDroppedFiles,
  sumBase64Bytes
} from '../../src/utils/multimodal_image_limits.js'

const image = (name = 'a.png', type = 'image/png') => ({ name, type })
const doc = (name = 'a.pdf', type = 'application/pdf') => ({ name, type })
const withBytes = (length) => ({ imageContent: 'x'.repeat(length) })

test('拖拽分流：图片进图片通道、其余进附件通道', () => {
  const { images, others } = splitDroppedFiles([image(), doc(), image('b.jpg', 'image/jpeg')])

  assert.deepEqual(
    images.map((file) => file.name),
    ['a.png', 'b.jpg']
  )
  assert.deepEqual(
    others.map((file) => file.name),
    ['a.pdf']
  )
})

test('拖拽分流：纯图片不产生附件、纯文档不产生图片', () => {
  assert.deepEqual(splitDroppedFiles([image()]).others, [])
  assert.deepEqual(splitDroppedFiles([doc()]).images, [])
  assert.deepEqual(splitDroppedFiles([]), { images: [], others: [] })
})

test('拖拽分流：缺 type 的文件按非图片处理，不误判成图片', () => {
  const { images, others } = splitDroppedFiles([{ name: 'unknown' }])

  assert.equal(images.length, 0)
  assert.equal(others.length, 1)
})

test('base64 总量按 imageContent 长度累加', () => {
  assert.equal(sumBase64Bytes([]), 0)
  assert.equal(sumBase64Bytes([withBytes(10), withBytes(5)]), 15)
  // 缺字段的项记为 0，不抛错
  assert.equal(sumBase64Bytes([{}, withBytes(3)]), 3)
})

test('剩余名额按张数递减并在到顶时归零', () => {
  assert.equal(remainingImageSlots([], 10), 10)
  assert.equal(remainingImageSlots([withBytes(1), withBytes(1)], 10), 8)
  assert.equal(remainingImageSlots(Array.from({ length: 10 }, () => ({})), 10), 0)
  // 已超上限时不回负数，否则 slice(0, 负数) 会切掉末尾而不是拒绝
  assert.equal(remainingImageSlots(Array.from({ length: 13 }, () => ({})), 10), 0)
})

test('总量判定是闭区间边界：正好等于预算算通过，超一字节即拒绝', () => {
  assert.equal(isWithinBase64Budget([withBytes(10)], 10), true)
  assert.equal(isWithinBase64Budget([withBytes(11)], 10), false)
  assert.equal(isWithinBase64Budget([withBytes(6), withBytes(4)], 10), true)
  assert.equal(isWithinBase64Budget([withBytes(6), withBytes(5)], 10), false)
  assert.equal(isWithinBase64Budget([], 10), true)
})

test('默认预算与后端约定一致（后端是权威，见 input_message_service）', () => {
  assert.equal(MAX_MULTIMODAL_IMAGES, 10)
  assert.equal(MAX_MULTIMODAL_TOTAL_BASE64_BYTES, 80 * 1024 * 1024)
  assert.equal(remainingImageSlots([]), MAX_MULTIMODAL_IMAGES)
  assert.equal(isWithinBase64Budget([withBytes(MAX_MULTIMODAL_TOTAL_BASE64_BYTES)]), true)
  assert.equal(isWithinBase64Budget([withBytes(MAX_MULTIMODAL_TOTAL_BASE64_BYTES + 1)]), false)
})

test('选图占位：accepted 与 placeholders 按下标配对，占位顺序即最终顺序', () => {
  const files = [image('a.png'), image('b.jpg', 'image/jpeg'), image('c.png')]
  const { accepted, placeholders, rejected } = reserveImageSlots([], files, {
    nextId: (index) => `id-${index}`
  })

  assert.equal(accepted.length, 3)
  assert.equal(placeholders.length, 3)
  assert.equal(rejected, 0)
  // 占位按下标与文件一一对应：上传完成后回填的是自己那一位，顺序不随完成先后改变
  assert.deepEqual(placeholders.map((slot) => slot.localId), ['id-0', 'id-1', 'id-2'])
  assert.ok(placeholders.every((slot) => slot.status === 'uploading'))
})

test('选图占位：上传中的占位计入名额，两批并发选图不会超上限', () => {
  // 第一批选 6 张全部占位中（尚未上传完成）
  const current = Array.from({ length: 6 }, (_, index) => ({ localId: `p-${index}`, status: 'uploading' }))
  // 第二批并发到达 7 张：只剩 4 个名额，接受 4 拒绝 3——总占位数不超过 10
  const { accepted, placeholders, rejected } = reserveImageSlots(current, Array.from({ length: 7 }, () => image()))

  assert.equal(accepted.length, 4)
  assert.equal(placeholders.length, 4)
  assert.equal(rejected, 3)
  assert.equal(current.length + placeholders.length, MAX_MULTIMODAL_IMAGES)
})

test('上传回填：按 localId 原位更新，位置不变', () => {
  const current = [
    { localId: 'a', status: 'done', imageContent: 'AAA' },
    { localId: 'b', status: 'uploading' },
    { localId: 'c', status: 'done', imageContent: 'CCC' }
  ]

  const settled = settleUploadedImage(current, 'b', { success: true, imageContent: 'BBB' })

  assert.equal(settled.status, 'done')
  assert.equal(settled.imageContent, 'BBB')
  // 回填发生在原位：乱序完成的多次回填不会改变列表顺序
  assert.deepEqual(current.map((item) => item.localId), ['a', 'b', 'c'])
  assert.deepEqual(current.map((item) => item.imageContent), ['AAA', 'BBB', 'CCC'])
})

test('上传回填：等待期间占位已被移除时返回 null，结果丢弃', () => {
  const current = [{ localId: 'a', status: 'uploading' }]

  assert.equal(settleUploadedImage(current, 'removed-id', { success: true, imageContent: 'X' }), null)
  // 被移除的占位不因迟到的上传结果复活
  assert.deepEqual(current, [{ localId: 'a', status: 'uploading' }])
})

test('撤销占位：按 localId 移除且只移除那一项；不存在的 localId 是空操作', () => {
  const current = [
    { localId: 'a', status: 'done' },
    { localId: 'b', status: 'uploading' },
    { localId: 'c', status: 'done' }
  ]

  removeUploadedImage(current, 'b')
  assert.deepEqual(current.map((item) => item.localId), ['a', 'c'])

  const same = removeUploadedImage(current, 'not-exist')
  assert.equal(same, current)
  assert.deepEqual(current.map((item) => item.localId), ['a', 'c'])
})
