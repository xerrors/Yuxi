<template>
  <section class="media-evidence-reader" aria-label="媒体来源" :aria-busy="loading">
    <a-radio-group
      v-if="entries.length > 1"
      v-model:value="selected"
      size="small"
      aria-label="选择媒体或音轨"
      class="media-assets"
    >
      <a-radio-button v-for="(asset, index) in entries" :key="asset.read_url" :value="index">{{
        assetLabel(asset)
      }}</a-radio-button>
    </a-radio-group>
    <p class="media-interval">{{ intervalLabel(active) }}</p>
    <div class="media-display">
      <a-spin v-if="loading" tip="正在读取媒体…" />
      <div v-else-if="error" role="alert" class="media-error">
        <p>{{ error }}</p>
        <p v-if="stale">原引用仍保留，不会换成更新后的媒体。</p>
        <a-button v-else @click="reload += 1">重新读取</a-button>
      </div>
      <img v-else-if="url && mime.startsWith('image/')" :src="url" :alt="title || '引用原图'" />
      <audio
        v-else-if="url && mime.startsWith('audio/')"
        :src="url"
        controls
        preload="metadata"
        aria-label="播放引用音频片段"
      />
      <video
        v-else-if="url && mime === 'video/mp4'"
        :src="url"
        controls
        preload="metadata"
        playsinline
        aria-label="播放引用视频片段"
      />
      <p v-else>此来源没有可读取的媒体资产。</p>
    </div>
    <p class="media-note">
      仅展示所选原图或时间区间。视频与音轨分别播放；播放不表示已核对完整文件或每一帧。
    </p>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { getApiToken } from '@/apis/auth_context'
import { knowledgeMediaApi } from '@/apis/knowledge_media_api'

const props = defineProps({
  readUrl: { type: String, default: '' },
  assets: { type: Array, default: () => [] },
  title: { type: String, default: '' },
  snapshotStatus: { type: String, default: '' }
})
const selected = ref(0)
const reload = ref(0)
const entries = computed(() =>
  props.assets.length ? props.assets : props.readUrl ? [{ read_url: props.readUrl }] : []
)
const active = computed(() => entries.value[selected.value] || entries.value[0])
const loading = ref(false)
const error = ref('')
const stale = ref(false)
const url = ref('')
const mime = ref('')
let request = 0
let controller

/** 将原件毫秒时间码显示为分秒。 */
function timestamp(value) {
  const seconds = Math.floor(value / 1000)
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}
/** 区分画面与音轨，不把模型观察当作原文。 */
function assetLabel(asset) {
  return { image: '原图', frame: '视频画面', audio: '音轨', video: '视频' }[asset.kind] || '媒体'
}
/** 只显示资产实际绑定的区间。 */
function intervalLabel(asset) {
  return Number.isFinite(asset?.start_ms) && Number.isFinite(asset?.end_ms)
    ? `原件时间 ${timestamp(asset.start_ms)}–${timestamp(asset.end_ms)}`
    : '引用的图像资产'
}
/** 切换或卸载时取消读取并释放浏览器字节。 */
function clearMedia() {
  controller?.abort()
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
  mime.value = ''
}
watch(
  () => [props.readUrl, props.assets],
  () => {
    selected.value = 0
  }
)
watch(
  () => [active.value?.read_url, props.snapshotStatus, reload.value, getApiToken()],
  async () => {
    const id = ++request
    clearMedia()
    loading.value = false
    error.value = ''
    stale.value = props.snapshotStatus === 'stale_source'
    if (stale.value) {
      error.value = '资料已更新，无法打开这份旧媒体引用。'
      return
    }
    if (!active.value?.read_url) return
    controller = new AbortController()
    loading.value = true
    try {
      const blob = await knowledgeMediaApi.readBlob(active.value.read_url, controller.signal)
      if (id !== request) return
      mime.value = blob.type.split(';')[0]
      url.value = URL.createObjectURL(blob)
    } catch (cause) {
      if (id !== request || cause.name === 'AbortError') return
      stale.value = cause.status === 409 || cause.response?.data?.detail?.code === 'stale_source'
      error.value = stale.value
        ? '资料已更新，无法打开这份旧媒体引用。'
        : cause.message || '媒体读取失败，请重试。'
    } finally {
      if (id === request) loading.value = false
    }
  },
  { immediate: true }
)
onUnmounted(() => {
  request += 1
  clearMedia()
})
</script>

<style scoped lang="less">
.media-evidence-reader {
  min-width: 0;
  color: var(--color-text);
}
.media-assets {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.media-interval,
.media-note {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.media-display {
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border-surface);
  background: var(--gray-10);
  border-radius: 8px;
  padding: 12px;
}
.media-display img,
.media-display video {
  display: block;
  max-width: 100%;
  max-height: 55vh;
  object-fit: contain;
}
.media-display audio {
  width: min(100%, 560px);
}
.media-error {
  width: 100%;
}
</style>
