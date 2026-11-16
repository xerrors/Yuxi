import { message } from 'ant-design-vue'
import { multimodalApi } from '@/apis/agent_api'

const MAX_IMAGE_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024

export const uploadMultimodalImage = async (file) => {
  if (!file) return null

  if (file.size > MAX_IMAGE_UPLOAD_SIZE_BYTES) {
    message.error('图片文件过大，请选择小于10MB的图片')
    return null
  }

  if (!file.type?.startsWith('image/')) {
    message.error('请选择有效的图片文件')
    return null
  }

  try {
    message.loading({ content: '正在处理图片...', key: 'image-upload' })

    const result = await multimodalApi.uploadImage(file)
    if (!result.success) {
      message.error({
        content: `图片处理失败: ${result.error}`,
        key: 'image-upload'
      })
      return null
    }

    message.success({
      content: '图片处理成功',
      key: 'image-upload',
      duration: 2
    })

    return {
      success: true,
      imageContent: result.image_content,
      thumbnailContent: result.thumbnail_content,
      width: result.width,
      height: result.height,
      format: result.format,
      mimeType: result.mime_type || file.type,
      sizeBytes: result.size_bytes,
      originalName: file.name || result.original_filename || 'pasted-image'
    }
  } catch (error) {
    console.error('图片上传失败:', error)
    message.error({
      content: `图片上传失败: ${error.message || '未知错误'}`,
      key: 'image-upload'
    })
    return null
  }
}
