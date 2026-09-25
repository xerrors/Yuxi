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
