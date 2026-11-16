import { apiPost } from './base'

export const userApi = {
  uploadImage: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiPost('/api/user/upload-image', formData)
  }
}
