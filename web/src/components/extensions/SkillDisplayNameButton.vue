<template>
  <a-button
    v-if="canRename"
    :disabled="disabled"
    :title="disabled ? '请先保存或取消文件编辑' : undefined"
    @click="startEditing"
    >修改显示名称</a-button
  >
  <a-modal
    v-model:open="editing"
    title="修改显示名称"
    ok-text="保存"
    cancel-text="取消"
    :confirm-loading="saving"
    :closable="!saving"
    :mask-closable="!saving"
    :cancel-button-props="{ disabled: saving }"
    @ok="save"
  >
    <p>只修改显示名称，技能标识 {{ skill.slug }} 保持不变。</p>
    <a-input v-model:value="name" aria-label="显示名称" :maxlength="128" :disabled="saving" />
    <p v-if="error" role="alert">{{ error }}</p>
  </a-modal>
</template>

<script setup>
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { skillApi } from '@/apis/skill_api'
import { useAgentStore } from '@/stores/agent'

const props = defineProps({
  skill: { type: Object, required: true },
  disabled: { type: Boolean, default: false }
})
const emit = defineEmits(['saved'])
const agentStore = useAgentStore()
const editing = ref(false)
const saving = ref(false)
const name = ref('')
const error = ref('')
const canRename = computed(() => props.skill.can_manage !== false)

/** 用当前服务端名称初始化编辑。 */
const startEditing = () => {
  if (props.disabled) return
  name.value = props.skill.name || ''
  error.value = ''
  editing.value = true
}

/** 保存源名称，再刷新聊天候选及调用方视图。 */
const save = async () => {
  if (saving.value || !canRename.value || props.disabled) return
  if (!name.value.trim() || [...name.value].length > 128) {
    error.value = '请输入 1–128 个字符的显示名称'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await skillApi.updateSkillDisplayName(props.skill.slug, name.value.trim(), {
      personal: (props.skill.sourceScope || props.skill.source_scope) === 'personal'
    })
    try {
      await agentStore.refreshAvailableSkills()
      message.success('显示名称已保存')
    } catch {
      message.warning('显示名称已保存，聊天候选刷新失败，请刷新页面后重试')
    }
    emit('saved')
    editing.value = false
  } catch (cause) {
    error.value = cause.message || '保存显示名称失败'
  } finally {
    saving.value = false
  }
}
</script>
