<template>
  <FallbackAvatar
    v-if="item.type === 'agent'"
    :src="item.icon"
    :name="item.label"
    :seed="item.value"
    kind="agent"
    :size="24"
    shape="rounded"
    decorative
  />
  <span v-else class="resource-avatar" :style="{ '--resource-color': color }" aria-hidden="true">
    <component :is="icon" :size="15" />
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { useThemeStore } from '@/shared/model/theme'
import FallbackAvatar from '@/shared/ui/FallbackAvatar.vue'
import { getExtensionIconColor } from '@/shared/lib/extensionIconColor'
import { getSkillIcon } from '@/shared/lib/skill_icon_utils'
import { getKbTypeIcon } from '@/modules/knowledge'
import { getMentionIconComponent } from '@/modules/session/model/mention_icon_utils'

const props = defineProps({ item: { type: Object, required: true } })
const themeStore = useThemeStore()
// 图标色卡分浅深两套，缺省参数会恒取浅色卡，深色下对比不足。
const color = computed(() => getExtensionIconColor(props.item.resourceId || props.item.value, themeStore.isDark))
const icon = computed(() => {
  if (props.item.type === 'knowledge') return getKbTypeIcon(props.item.kbType || 'milvus')
  if (props.item.type === 'skill') return getSkillIcon(props.item.value)
  return getMentionIconComponent(props.item.type, props.item.value, props.item.label)
})
</script>

<style lang="less" scoped>
.resource-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 24px;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  color: var(--resource-color);
  background: color-mix(in srgb, var(--resource-color) 7%, var(--gray-0));
}
</style>
