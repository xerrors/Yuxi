<template>
  <section ref="reader" class="workspace-preview-pane" aria-label="文件预览" tabindex="-1">
    <AgentFilePreview
      :file="file"
      :file-path="filePath"
      :show-download="true"
      :show-close="true"
      :full-height="true"
      :editable="editable"
      :saving="saving"
      container-class="workspace-preview-container"
      content-class="workspace-preview-content"
      @download="$emit('download')"
      @close="$emit('close')"
      @save="$emit('save', $event)"
    />
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import AgentFilePreview from '@/modules/session/ui/workspace/AgentFilePreview.vue'

defineProps({
  file: { type: Object, required: true },
  filePath: { type: String, default: '' },
  editable: { type: Boolean, default: false },
  saving: { type: Boolean, default: false }
})

defineEmits(['close', 'download', 'save'])

const reader = ref(null)
onMounted(() => reader.value?.focus({ preventScroll: true }))
</script>

<style scoped lang="less">
.workspace-preview-pane {
  min-width: 0;
  min-height: 0;
  background: var(--gray-0);
  overflow: hidden;
}

:deep(.workspace-preview-container) {
  height: 100%;
  border-radius: 0;
}

:deep(.workspace-preview-container .file-path-title) {
  color: var(--gray-900);
  font-weight: 500;
}

:deep(.workspace-preview-content) {
  flex: 1 1 auto;
  max-height: none;
  min-height: 0;
}

:deep(.workspace-preview-content .html-preview),
:deep(.workspace-preview-content .pdf-preview),
:deep(.workspace-preview-content .office-preview) {
  display: block;
  height: 100%;
  min-height: 100%;
}
</style>
