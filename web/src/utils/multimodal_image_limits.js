/**
 * 聊天多图的纯策略：张数上限、请求体总量上限、拖拽分流。
 *
 * 刻意不依赖任何 I/O 与提示组件：策略要能被直接单测，而上传与提示留在
 * `multimodal_image_upload.js`。后端是权威（见
 * backend/package/yuxi/services/input_message_service.py 的 MAX_CHAT_IMAGES /
 * MAX_CHAT_IMAGE_TOTAL_BYTES），这里同值前置一份，好在发请求之前就给出提示。
 */

export const MAX_MULTIMODAL_IMAGES = 10

// 请求体里是内联 base64，体积按 base64 字符数计。
export const MAX_MULTIMODAL_TOTAL_BASE64_BYTES = 80 * 1024 * 1024

/** 拖拽分流：图片走多模态直读，其余文件仍走附件通道。 */
export const splitDroppedFiles = (files = []) => {
  const images = []
  const others = []
  for (const file of files) {
    if (file?.type?.startsWith('image/')) {
      images.push(file)
    } else {
      others.push(file)
    }
  }
  return { images, others }
}

/** 这批图片的 base64 总量。 */
export const sumBase64Bytes = (images = []) =>
  images.reduce((total, image) => total + (image?.imageContent?.length || 0), 0)

/** 还能再收几张；到上限后为 0，不回负数。 */
export const remainingImageSlots = (current = [], max = MAX_MULTIMODAL_IMAGES) =>
  Math.max(max - current.length, 0)

/** 是否在单次请求的总量预算内（等于预算是允许的）。 */
export const isWithinBase64Budget = (images = [], budget = MAX_MULTIMODAL_TOTAL_BASE64_BYTES) =>
  sumBase64Bytes(images) <= budget

/**
 * 选图即按顺序占位：上传中的项计入张数上限，两批并发选图不会超限；
 * 占位顺序即最终顺序，上传完成后只回填对应项，不按完成顺序追加。
 * accepted 与 placeholders 按下标一一对应。
 */
export const reserveImageSlots = (
  current = [],
  files = [],
  { max = MAX_MULTIMODAL_IMAGES, nextId = (index) => index } = {}
) => {
  const accepted = files.slice(0, remainingImageSlots(current, max))
  return {
    accepted,
    placeholders: accepted.map((file, index) => ({ localId: nextId(index), status: 'uploading' })),
    rejected: files.length - accepted.length
  }
}

/** 上传成功：按 localId 原位回填；等待期间占位已被移除则返回 null（结果丢弃）。 */
export const settleUploadedImage = (current, localId, imageData) => {
  const slot = current.find((image) => image.localId === localId)
  if (!slot) return null
  Object.assign(slot, imageData, { status: 'done' })
  return slot
}

/** 上传失败：按 localId 撤销占位；已不存在时是空操作。原地修改并返回同一数组。 */
export const removeUploadedImage = (current, localId) => {
  const index = current.findIndex((image) => image.localId === localId)
  if (index !== -1) current.splice(index, 1)
  return current
}
