<template>
  <img
    class="file-type-icon"
    :src="iconUrl"
    :style="{ width: `${size}px`, height: `${size}px` }"
    alt=""
    draggable="false"
  />
</template>

<script setup>
import { computed } from 'vue'
import { resolveFileIconUrl } from '@/utils/file_icon'

const props = defineProps({
  // 文件名或路径（目录可 `/` 结尾）
  name: { type: String, default: '' },
  isDir: { type: Boolean, default: false },
  // 文件夹图标变体：default | agent | enterprise | favorite | knowledge | personal | trash
  folderVariant: { type: String, default: 'default' },
  size: { type: Number, default: 16 }
})

const iconUrl = computed(() =>
  resolveFileIconUrl(props.name, { isDir: props.isDir, folderVariant: props.folderVariant })
)
</script>

<style scoped>
.file-type-icon {
  display: inline-block;
  object-fit: contain;
  flex-shrink: 0;
  vertical-align: middle;
}
</style>
