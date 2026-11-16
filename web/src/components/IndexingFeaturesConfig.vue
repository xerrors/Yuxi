<template>
  <div class="indexing-features-config">
    <div v-if="supportsSparseVectors" class="feature-toggle-row">
      <div class="feature-toggle-copy">
        <span class="feature-label">BGE-M3 稀疏向量</span>
        <span class="feature-hint">与稠密向量共同参与检索</span>
      </div>
      <a-select
        v-model:value="embeddingFeatures.bge_m3_sparse_enabled"
        :options="featureToggleOptions"
        aria-label="BGE-M3 稀疏向量"
        class="feature-toggle-select"
      />
    </div>

    <div class="feature-toggle-row">
      <div class="feature-toggle-copy">
        <span class="feature-label">Parent-Child</span>
        <span class="feature-hint">使用子块召回，并返回完整父块</span>
      </div>
      <a-select
        v-model:value="parentChild.enabled"
        :options="featureToggleOptions"
        aria-label="Parent-Child"
        class="feature-toggle-select"
      />
    </div>

    <div v-if="parentChild.enabled" class="parent-child-fields">
      <a-form-item>
        <template #label>
          <span class="parent-child-label">
            父块 Token 数
            <a-tooltip title="父块作为最终返回的完整上下文，默认最大 Token 数为 1000">
              <QuestionCircleOutlined class="parent-child-help-icon" />
            </a-tooltip>
          </span>
        </template>
        <a-input-number
          :value="displayDefaultValue(parentChild.parent_token_num, 1000)"
          :min="256"
          :max="4096"
          placeholder="默认 1000"
          class="full-width"
          @update:value="
            updateDefaultValue(parentChild, 'parent_token_num', $event, 1000)
          "
        />
      </a-form-item>
      <a-form-item>
        <template #label>
          <span class="parent-child-label">
            子块 Token 数
            <a-tooltip title="子块用于检索召回，默认最大 Token 数为 200">
              <QuestionCircleOutlined class="parent-child-help-icon" />
            </a-tooltip>
          </span>
        </template>
        <a-input-number
          :value="displayDefaultValue(parentChild.child_token_num, 200)"
          :min="64"
          :max="1024"
          placeholder="默认 200"
          class="full-width"
          @update:value="updateDefaultValue(parentChild, 'child_token_num', $event, 200)"
        />
      </a-form-item>
      <a-form-item>
        <template #label>
          <span class="parent-child-label">
            子块重叠比例 (%)
            <a-tooltip title="相邻子块按 Token 数计算的重叠比例，默认值为 15%">
              <QuestionCircleOutlined class="parent-child-help-icon" />
            </a-tooltip>
          </span>
        </template>
        <a-input-number
          :value="displayDefaultValue(parentChild.child_overlap_percent, 15)"
          :min="0"
          :max="99"
          placeholder="默认 15"
          class="full-width"
          @update:value="
            updateDefaultValue(parentChild, 'child_overlap_percent', $event, 15)
          "
        />
      </a-form-item>
      <a-form-item>
        <template #label>
          <span class="parent-child-label">
            分隔符
            <a-tooltip title="父块和子块切分使用的分隔符，默认使用换行符 \n">
              <QuestionCircleOutlined class="parent-child-help-icon" />
            </a-tooltip>
          </span>
        </template>
        <a-input
          :value="displayDefaultValue(parentChild.separator, '\\n')"
          placeholder="默认 \n"
          class="full-width"
          @update:value="updateDefaultValue(parentChild, 'separator', $event, '\\n')"
        />
      </a-form-item>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { QuestionCircleOutlined } from '@ant-design/icons-vue'
import { isBgeM3EmbeddingModelSpec } from '@/utils/databaseCreateForm'

const props = defineProps({
  params: {
    type: Object,
    required: true
  },
  embeddingModelSpec: {
    type: String,
    default: ''
  }
})

const embeddingFeatures = computed(() => props.params.embedding_features)
const parentChild = computed(() => props.params.parent_child)
const featureToggleOptions = [
  { label: '启用', value: true },
  { label: '关闭', value: false }
]
const displayDefaultValue = (value, defaultValue) =>
  value === defaultValue ? undefined : value
const updateDefaultValue = (target, key, value, defaultValue) => {
  target[key] = value === undefined || value === null || value === '' ? defaultValue : value
}
const supportsSparseVectors = computed(() =>
  isBgeM3EmbeddingModelSpec(props.embeddingModelSpec)
)

watch(
  supportsSparseVectors,
  (supported) => {
    if (!supported && embeddingFeatures.value) {
      embeddingFeatures.value.bge_m3_sparse_enabled = false
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.indexing-features-config {
  display: grid;
  gap: 14px;
  width: 100%;
  min-width: 0;
}

.feature-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.feature-toggle-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.feature-toggle-select {
  flex: 0 0 120px;
  width: 120px;
}

.feature-label {
  color: var(--gray-800);
  font-size: 14px;
  font-weight: 500;
}

.feature-hint {
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.5;
}

.parent-child-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.parent-child-fields :deep(.ant-form-item) {
  margin-bottom: 4px;
}

.parent-child-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.parent-child-help-icon {
  color: var(--gray-500);
  cursor: help;
  font-size: 14px;
}

.full-width {
  width: 100%;
}

@media (max-width: 640px) {
  .parent-child-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
