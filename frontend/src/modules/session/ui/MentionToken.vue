<template>
  <span class="mention-ref-token" :class="`mention-ref-${type}`">
    <span class="mention-ref-icon" aria-hidden="true">
      <FileTypeIcon
        v-if="type === 'file'"
        :name="value"
        :is-dir="value.endsWith('/')"
        :size="MENTION_ICON_SIZE"
      />
      <FallbackAvatar
        v-else-if="type === 'agent'"
        :src="agent?.icon"
        :name="label"
        :seed="value"
        kind="agent"
        :size="18"
        shape="rounded"
        decorative
      />
      <component
        v-else
        :is="getMentionIconComponent(type, value, label)"
        :stroke-width="MENTION_ICON_STROKE_WIDTH"
        :size="MENTION_ICON_SIZE"
      />
    </span>
    <span class="mention-ref-label">{{ label }}</span>
  </span>
</template>

<script setup>
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import {
  getMentionIconComponent,
  MENTION_ICON_SIZE,
  MENTION_ICON_STROKE_WIDTH
} from '@/modules/session/model/mention_icon_utils'

defineProps({
  type: { type: String, required: true },
  value: { type: String, required: true },
  label: { type: String, required: true },
  agent: { type: Object, default: null }
})
</script>

<style lang="less" scoped>
.mention-ref-token {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  max-width: min(100%, 360px);
  color: var(--main-700);
  line-height: inherit;
  vertical-align: baseline;
  white-space: nowrap;
}

.mention-ref-editable {
  user-select: all;
}

.mention-ref-icon {
  display: inline-flex;
  align-items: center;
  // 头像居中，文字继续按基线对齐，保持与后续正文一致。
  align-self: center;
  flex-shrink: 0;
  font-size: 13px;
  line-height: 1;
  margin-left: 4px;

  :deep(svg) {
    display: block;
  }
}

.mention-ref-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: inherit;
  font-weight: 500;
}
</style>
