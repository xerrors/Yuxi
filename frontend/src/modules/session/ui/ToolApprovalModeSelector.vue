<template>
  <ActionDropdown :upward="upward" :width="280" v-model:open="open">
    <template #trigger>
      <ActionTrigger
        :label="currentOption.label"
        :open="open"
        :title="currentOption.hint"
        :class="{ 'is-trusted': modelValue === 'always_trust' }"
        collapse-label
      >
        <template #icon><component :is="currentOption.icon" :size="16" /></template>
      </ActionTrigger>
    </template>
    <div role="menu" aria-label="工具审批模式">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        role="menuitemradio"
        :aria-checked="modelValue === option.value"
        class="config-dropdown-item"
        :class="{ selected: modelValue === option.value }"
        @click="selectMode(option.value)"
      >
        <component
          :is="option.icon"
          :size="15"
          class="config-dropdown-item-icon"
          :class="{ trusted: option.value === 'always_trust' }"
        />
        <span class="config-dropdown-item-text">
          <span class="config-dropdown-item-label">{{ option.label }}</span>
          <span class="config-dropdown-item-hint">{{ option.hint }}</span>
        </span>
        <Check v-if="modelValue === option.value" :size="14" class="config-dropdown-item-check" />
      </button>
    </div>
  </ActionDropdown>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Check, Hand, ShieldAlert } from '@lucide/vue'
import ActionDropdown from '@/shared/ui/ActionDropdown.vue'
import ActionTrigger from '@/shared/ui/ActionTrigger.vue'
import { DEFAULT_TOOL_APPROVAL_MODE } from '@/modules/session/model/toolApproval'

const props = defineProps({
  upward: { type: Boolean, default: false },
  modelValue: { type: String, default: DEFAULT_TOOL_APPROVAL_MODE }
})

const emit = defineEmits(['update:modelValue'])
const options = [
  {
    value: 'default',
    label: '请求审批',
    // 说明审批门范围，避免“当前 Project 内写入免审批”被误读为缺陷。
    hint: '命令执行与 Project 外文件写入需你批准；当前 Project 内写入自动放行。',
    icon: Hand
  },
  {
    value: 'always_trust',
    label: '完全信任',
    hint: '不再暂停审批，工具结果直接返回。仅建议在可信会话中使用。',
    icon: ShieldAlert
  }
]

const open = ref(false)
const currentOption = computed(
  () => options.find((option) => option.value === props.modelValue) ||
    options.find((option) => option.value === DEFAULT_TOOL_APPROVAL_MODE)
)

const selectMode = (mode) => {
  emit('update:modelValue', mode)
  open.value = false
}
</script>

<style scoped lang="less">
.is-trusted {
  color: var(--color-warning-700);
}
</style>

<style lang="less">
.config-dropdown-overlay .config-dropdown-item-icon.trusted {
  color: var(--color-warning-700);
}

.config-dropdown-overlay .config-dropdown-item-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.config-dropdown-overlay .config-dropdown-item-text .config-dropdown-item-label {
  flex: none;
}

.config-dropdown-overlay .config-dropdown-item-hint {
  color: var(--color-text-secondary);
  font-size: 11px;
  line-height: 1.4;
  white-space: normal;
  text-align: left;
}
</style>
