<template>
  <div class="skill-settings-section">
    <div class="section-title">Skill 配置</div>

    <section class="source-panel">
      <template v-if="sourceOption">
        <div class="source-header">
          <div class="source-copy">
            <h4>远程来源白名单</h4>
            <p>只有列表中的域名可以远程加载和安装 Skill。</p>
          </div>
          <a-button v-if="!isEditing" type="text" size="small" @click="startEditing">
            <Pencil :size="14" />
            编辑
          </a-button>
        </div>

        <div v-for="field in sourceFields" :key="field.key" class="source-content">
          <template v-if="isEditing">
            <a-select
              v-model:value="draftValue[field.key]"
              class="domain-select"
              mode="tags"
              :aria-label="field.label"
              :token-separators="[',', ' ', '\n']"
              placeholder="输入域名后按回车，例如 github.com"
              :options="[]"
            />
            <div class="edit-footer">
              <span>精确匹配域名；清空并保存会关闭远程安装。</span>
              <div class="edit-actions">
                <a-button size="small" :disabled="isSaving" @click="cancelEditing"> 取消 </a-button>
                <a-button type="primary" size="small" :loading="isSaving" @click="saveOption">
                  保存
                </a-button>
              </div>
            </div>
          </template>
          <div
            v-else-if="getFieldValue(field).length"
            class="host-list"
            aria-label="允许的来源域名"
          >
            <a-tag v-for="host in getFieldValue(field)" :key="host" class="host-tag">
              {{ host }}
            </a-tag>
          </div>
          <div v-else class="empty-value">未配置域名，远程安装已关闭</div>
        </div>
      </template>

      <div v-else class="panel-state">
        {{ isLoading ? '正在加载配置…' : '远程 Skill 来源配置尚未初始化' }}
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { Pencil } from '@lucide/vue'
import { configOptionsApi } from '@/apis/system_api'

const SOURCE_OPTION_KEY = 'remote_skill_source_policy'

const sourceOption = ref(null)
const isEditing = ref(false)
const isLoading = ref(true)
const isSaving = ref(false)
const draftValue = ref({})

const sourceFields = computed(() => sourceOption.value?.params?.fields || [])

/** 返回已保存值；尚未保存时展示后端定义的默认值。 */
const getFieldValue = (field) => {
  const value = sourceOption.value?.value || {}
  if (Object.prototype.hasOwnProperty.call(value, field.key)) return value[field.key]
  return field.default || []
}

/** 加载远程 Skill 来源配置。 */
const loadOption = async () => {
  try {
    const data = await configOptionsApi.getOptions()
    sourceOption.value = (data.options || []).find((option) => option.key === SOURCE_OPTION_KEY)
  } catch (error) {
    message.error(error.message || '加载 Skill 配置失败')
  } finally {
    isLoading.value = false
  }
}

/** 进入编辑状态并复制当前有效域名，避免直接修改接口数据。 */
const startEditing = () => {
  draftValue.value = Object.fromEntries(
    sourceFields.value.map((field) => [field.key, [...getFieldValue(field)]])
  )
  isEditing.value = true
}

/** 放弃本次编辑。 */
const cancelEditing = () => {
  isEditing.value = false
  draftValue.value = {}
}

/** 保存来源域名，并用服务端规范化后的值刷新页面。 */
const saveOption = async () => {
  isSaving.value = true
  try {
    const data = await configOptionsApi.updateOption(SOURCE_OPTION_KEY, draftValue.value)
    sourceOption.value = data.option
    cancelEditing()
    message.success('Skill 来源配置已保存')
  } catch (error) {
    message.error(error.message || '保存 Skill 配置失败')
  } finally {
    isSaving.value = false
  }
}

onMounted(loadOption)
</script>

<style lang="less" scoped>
.skill-settings-section {
  margin-top: 24px;

  &.first-section {
    margin-top: 0;
  }
}

.section-title {
  margin: 0 0 10px;
  color: var(--gray-900);
  font-size: 15px;
  font-weight: 600;
}

.source-panel {
  padding: 14px 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.source-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;

  h4,
  p {
    margin: 0;
  }

  h4 {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 500;
  }

  p {
    margin-top: 4px;
    color: var(--color-text-secondary);
    font-size: 12px;
    line-height: 1.5;
  }

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--gray-600);

    &:hover,
    &:focus-visible {
      color: var(--main-700);
      background: var(--main-10);
    }
  }
}

.source-content {
  margin-top: 14px;
}

.domain-select {
  width: 100%;
}

.edit-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 10px;

  > span {
    color: var(--color-text-secondary);
    font-size: 12px;
    line-height: 1.5;
  }
}

.edit-actions {
  display: flex;
  flex: none;
  gap: 8px;
}

.host-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.host-tag {
  margin: 0;
  padding: 3px 9px;
  border-color: var(--gray-150);
  border-radius: 6px;
  background: var(--gray-50);
  color: var(--gray-700);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 18px;
}

.empty-value,
.panel-state {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.panel-state {
  padding: 4px 0;
}

@media (max-width: 680px) {
  .edit-footer {
    align-items: flex-start;
    flex-direction: column;
  }

  .edit-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
