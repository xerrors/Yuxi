<template>
  <div
    class="attachment-options-panel"
    role="menu"
    :aria-label="activeResourceType ? `${activeResourceLabel}选择` : '添加内容'"
    @click.stop
  >
    <template v-if="!activeResourceType">
      <template v-if="fileUploadEnabled">
        <button
          type="button"
          role="menuitem"
          class="config-dropdown-item"
          :class="{ disabled }"
          :disabled="disabled"
          title="支持任意文件格式 ≤ 5 MB"
          @click="handleAttachmentClick"
        >
          <FileText :size="15" class="config-dropdown-item-icon" />
          <span class="config-dropdown-item-label">添加附件</span>
        </button>

        <button
          type="button"
          role="menuitem"
          class="config-dropdown-item"
          :class="{ disabled }"
          :disabled="disabled"
          title="支持 jpg/jpeg/png/gif，≤ 5 MB"
          @click="handleImageUpload"
        >
          <Image :size="15" class="config-dropdown-item-icon" />
          <span class="config-dropdown-item-label">上传图片</span>
        </button>
      </template>

      <div v-if="fileUploadEnabled && hasMentionResources" class="config-dropdown-divider"></div>

      <button
        v-for="group in visibleResourceGroups"
        :key="group.key"
        type="button"
        role="menuitem"
        class="config-dropdown-item"
        :disabled="disabled"
        :class="{ disabled }"
        @click="activeResourceType = group.key"
      >
        <component :is="group.icon" :size="15" class="config-dropdown-item-icon" />
        <span class="config-dropdown-item-label">{{ group.label }}</span>
        <ChevronRight :size="14" class="attachment-options-chevron" />
      </button>
    </template>

    <template v-else>
      <button
        type="button"
        class="attachment-options-back"
        aria-label="返回添加内容菜单"
        @click="activeResourceType = ''"
      >
        <ArrowLeft :size="15" />
        <span>{{ activeResourceLabel }}</span>
      </button>

      <div class="config-dropdown-divider"></div>

      <div class="attachment-resource-list">
        <button
          v-for="item in activeResourceItems"
          :key="`${item.type}-${item.value}`"
          type="button"
          role="menuitem"
          class="config-dropdown-item attachment-resource-item"
          :title="item.description || item.label"
          @click="selectMention(item)"
        >
          <component
            :is="getMentionIconComponent(item.type, item.value)"
            :size="15"
            class="config-dropdown-item-icon"
          />
          <span class="attachment-resource-content">
            <span class="config-dropdown-item-label">{{ item.label }}</span>
            <span class="attachment-resource-description">
              {{ item.description || '暂无描述' }}
            </span>
          </span>
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ArrowLeft, ChevronRight, Database, FileText, Image, WandSparkles } from '@lucide/vue'
import { getMentionIconComponent } from '@/utils/mention_icon_utils'
import { buildMentionResourceItems } from '@/utils/mention_resource_items'

const RESOURCE_GROUPS = [
  { key: 'knowledgeBases', label: '知识库', icon: Database },
  { key: 'skills', label: '技能', icon: WandSparkles }
]

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  },
  fileUploadEnabled: {
    type: Boolean,
    default: false
  },
  mention: {
    type: Object,
    default: () => null
  }
})

const emit = defineEmits(['upload', 'upload-image-files', 'select-mention'])
const activeResourceType = ref('')
const resourceItems = computed(() => buildMentionResourceItems(props.mention || {}))
const visibleResourceGroups = computed(() =>
  RESOURCE_GROUPS.filter((group) => resourceItems.value[group.key].length)
)
const hasMentionResources = computed(() => visibleResourceGroups.value.length > 0)
const activeResourceItems = computed(() => resourceItems.value[activeResourceType.value] || [])
const activeResourceLabel = computed(
  () => RESOURCE_GROUPS.find((group) => group.key === activeResourceType.value)?.label ?? ''
)

const handleAttachmentClick = () => {
  if (props.disabled) return
  emit('upload')
}

// 选择图片：菜单只负责选文件，上传与张数/体积限流统一由父级处理，
// 这样菜单、粘贴、拖拽三条入口走同一条路径，限额只在一处判定。
const handleImageUpload = () => {
  if (props.disabled) return

  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/*'
  input.multiple = true
  input.style.display = 'none'

  input.onchange = (event) => {
    const files = Array.from(event.target.files || [])
    document.body.removeChild(input)
    if (files.length) {
      emit('upload-image-files', files)
    }
  }

  document.body.appendChild(input)
  input.click()
}

const selectMention = (item) => {
  if (props.disabled) return
  emit('select-mention', item)
  activeResourceType.value = ''
}
</script>

<style lang="less" scoped>
.attachment-options-panel {
  width: 100%;
}

.attachment-options-chevron {
  flex-shrink: 0;
  color: var(--gray-400);
}

.attachment-options-back {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: 6px;
  color: var(--gray-800);
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  transition: background-color 0.15s ease;

  &:hover,
  &:focus-visible {
    background: var(--gray-50);
  }
}

.attachment-resource-list {
  max-height: min(320px, calc(100vh - 180px));
  overflow-y: auto;
}

.attachment-options-panel .attachment-resource-item {
  gap: 10px;
}

.attachment-resource-content {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.attachment-resource-description {
  overflow: hidden;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.config-dropdown-item:focus-visible) {
  outline: 2px solid var(--main-300);
  outline-offset: -2px;
}
</style>
