import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_MULTIMODAL_IMAGES,
  MAX_MULTIMODAL_TOTAL_BASE64_BYTES,
  isWithinBase64Budget,
  remainingImageSlots,
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
