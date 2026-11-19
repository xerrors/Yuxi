<template>
  <div class="tool-result-blocks">
    <template v-for="(part, index) in parts" :key="index">
      <div
        v-if="part.loading"
        class="tool-result-image-skeleton"
        role="status"
        aria-label="图片加载中"
        aria-busy="true"
      >
        <ImageIcon :size="28" aria-hidden="true" />
      </div>
      <p v-else-if="part.image && (!part.src || part.failed)" role="alert">图片无法加载</p>
      <button
        v-else-if="part.image"
        type="button"
        class="tool-result-image"
        :aria-label="`放大查看工具结果图片 ${index + 1}`"
        @click="preview = part.src"
      >
        <img
          :src="part.src"
          alt="工具结果图片"
          referrerpolicy="no-referrer"
          @error="part.failed = true"
        />
      </button>
      <pre v-else>{{ part.text }}</pre>
    </template>
    <ChatImageViewer :src="preview" alt="工具结果图片" @close="preview = ''" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Image as ImageIcon } from '@lucide/vue'
import { apiGet } from '@/apis/base'
import { resolveMarkdownImageUrl } from '@/shared/lib/file_preview'
import ChatImageViewer from '@/shared/ui/ChatImageViewer.vue'
import { getToolResultParts } from '@/modules/session/model/toolCallProjection'

const props = defineProps({ content: { default: null }, imageUrls: Boolean })
const preview = ref('')
const parts = ref([])
// 流式投影会重建内容对象；按展示值监听，避免相同结果反复下载并撤销图片。
const resultParts = computed(() =>
  JSON.stringify(getToolResultParts(props.content, { imageUrls: props.imageUrls }))
)

watch(
  resultParts,
  async (value, _previous, onCleanup) => {
    let disposed = false
    const objectUrls = []
    onCleanup(() => {
      disposed = true
      objectUrls.forEach((url) => URL.revokeObjectURL(url))
    })
    preview.value = ''
    parts.value = JSON.parse(value)
    await Promise.all(
      parts.value.map(async (part) => {
        if (!part.image || !part.src) return
        const imageUrl = resolveMarkdownImageUrl(part.src, null, window.location?.origin)
        if (!imageUrl) return
        part.loading = true
        part.src = ''
        try {
          const response = await apiGet(imageUrl, {}, true, 'blob')
          const blob = await response.blob()
          if (disposed) return
          if (!blob.type.startsWith('image/')) throw new Error('文件无法作为图片预览')
          part.src = URL.createObjectURL(blob)
          objectUrls.push(part.src)
        } catch {
          if (!disposed) part.failed = true
        } finally {
          if (!disposed) part.loading = false
        }
      })
    )
  },
  { immediate: true }
)
</script>

<style lang="less" scoped>
.tool-result-blocks {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;

  pre {
    box-sizing: border-box;
    max-width: 100%;
    margin: 0;
    padding: 10px;
    border-radius: 4px;
    font-size: 12px;
    line-height: 1.4;
    color: var(--tool-result-text-color, var(--gray-700));
    background: var(--gray-25);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 300px;
    overflow: auto;
  }
}

.tool-result-image-skeleton {
  position: relative;
  display: grid;
  place-items: center;
  width: min(400px, 100%);
  aspect-ratio: 4 / 3;
  overflow: hidden;
  border-radius: 4px;
  background: var(--gray-100);
  color: var(--gray-300);

  &::after {
    position: absolute;
    inset: 0;
    content: '';
    background: linear-gradient(90deg, transparent, var(--gray-200), transparent);
    animation: image-skeleton-shimmer 1.5s ease-in-out infinite;
  }
}

@keyframes image-skeleton-shimmer {
  from { transform: translateX(-100%); }
  to { transform: translateX(100%); }
}

@media (prefers-reduced-motion: reduce) {
  .tool-result-image-skeleton::after {
    animation: none;
    background: none;
  }
}

.tool-result-image {
  display: block;
  max-width: min(400px, 100%);
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  cursor: zoom-in;

  img {
    display: block;
    width: auto;
    height: auto;
    max-width: 100%;
    max-height: 300px;
    object-fit: contain;
    border-radius: inherit;
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 2px;
  }
}
</style>
