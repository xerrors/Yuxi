import { computed, ref } from 'vue'
import { typeApi } from '@/apis/knowledge_api'
import { DEFAULT_CHUNK_PRESET_ID } from '@/utils/chunkUtils'

const chunkPresetOptions = ref([])
const chunkPresetLoading = ref(false)
let chunkPresetOptionsLoaded = false
let chunkPresetOptionsRequest = null

const normalizeChunkPresetOptions = (options) => {
  if (!Array.isArray(options)) return []

  return options
    .filter((item) => item?.value && item?.label)
    .map((item) => ({
      value: String(item.value),
      label: String(item.label),
      description: item.description ? String(item.description) : ''
    }))
}

export const useChunkPresetOptions = () => {
  const loadChunkPresetOptions = async () => {
    if (chunkPresetOptionsLoaded) return chunkPresetOptions.value
    if (chunkPresetOptionsRequest) return chunkPresetOptionsRequest

    chunkPresetLoading.value = true
    chunkPresetOptionsRequest = typeApi
      .getChunkPresets()
      .then((data) => {
        chunkPresetOptions.value = normalizeChunkPresetOptions(data?.chunk_presets)
        chunkPresetOptionsLoaded = true
        return chunkPresetOptions.value
      })
      .catch((error) => {
        console.error('加载分块策略失败:', error)
        chunkPresetOptions.value = []
        return chunkPresetOptions.value
      })
      .finally(() => {
        chunkPresetLoading.value = false
        chunkPresetOptionsRequest = null
      })

    return chunkPresetOptionsRequest
  }

  const chunkPresetSelectOptions = computed(() =>
    chunkPresetOptions.value.map(({ value, label }) => ({ value, label }))
  )

  const chunkPresetLabelMap = computed(() =>
    Object.fromEntries(chunkPresetOptions.value.map((item) => [item.value, item.label]))
  )

  const chunkPresetDescriptionMap = computed(() =>
    Object.fromEntries(chunkPresetOptions.value.map((item) => [item.value, item.description]))
  )

  const getChunkPresetDescription = (presetId) => {
    const descriptions = chunkPresetDescriptionMap.value
    return descriptions[presetId] || descriptions[DEFAULT_CHUNK_PRESET_ID] || ''
  }

  return {
    chunkPresetOptions,
    chunkPresetSelectOptions,
    chunkPresetLabelMap,
    chunkPresetLoading,
    loadChunkPresetOptions,
    getChunkPresetDescription
  }
}
