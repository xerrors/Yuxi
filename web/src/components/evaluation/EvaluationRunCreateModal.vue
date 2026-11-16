<template>
  <a-modal
    v-model:open="visible"
    title="新建评估"
    width="560px"
    :mask-closable="!submitting"
    :closable="!submitting"
    @cancel="closeModal"
  >
    <a-form ref="formRef" :model="form" :rules="rules" layout="vertical">
      <a-form-item label="评估名称" name="name">
        <a-input
          v-model:value="form.name"
          placeholder="请输入评估名称"
          :maxlength="100"
          show-count
        />
      </a-form-item>

      <a-form-item label="评估基准" name="datasetId">
        <a-select
          v-model:value="form.datasetId"
          placeholder="请选择评估基准"
          :options="datasetOptions"
          @change="handleDatasetChange"
        />
      </a-form-item>

      <div class="evaluation-model-grid">
        <a-form-item
          :label="selectedDataset?.has_gold_answers ? '答案生成模型（可选）' : '答案生成模型'"
        >
          <ModelSelectorComponent
            :model_spec="form.answerModel"
            placeholder="选择答案生成模型"
            display-name="short"
            clearable
            :disabled="!selectedDataset?.has_gold_answers"
            @select-model="(value) => (form.answerModel = value)"
          />
        </a-form-item>

        <a-form-item
          :label="selectedDataset?.has_gold_answers ? '答案评判模型（可选）' : '答案评判模型'"
        >
          <ModelSelectorComponent
            :model_spec="form.judgeModel"
            placeholder="选择答案评判模型"
            display-name="short"
            clearable
            :disabled="!selectedDataset?.has_gold_answers"
            @select-model="(value) => (form.judgeModel = value)"
          />
        </a-form-item>
      </div>

      <div class="evaluation-run-hint" role="status">
        <Info :size="15" aria-hidden="true" />
        <span>{{ evaluationHint }}</span>
      </div>
    </a-form>

    <template #footer>
      <a-button :disabled="submitting" @click="closeModal">取消</a-button>
      <a-button
        type="primary"
        :loading="submitting"
        :disabled="datasets.length === 0"
        @click="submitEvaluation"
      >
        开始评估
      </a-button>
    </template>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Info } from '@lucide/vue'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import { evaluationApi } from '@/apis/knowledge_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, required: true },
  datasets: { type: Array, default: () => [] },
  initialDatasetId: { type: String, default: '' }
})

const emit = defineEmits(['update:open', 'success'])

const formRef = ref(null)
const submitting = ref(false)
const form = reactive({
  name: '',
  datasetId: '',
  answerModel: '',
  judgeModel: ''
})

const rules = {
  name: [{ required: true, message: '请输入评估名称', trigger: 'blur' }],
  datasetId: [{ required: true, message: '请选择评估基准', trigger: 'change' }]
}

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const datasetOptions = computed(() =>
  props.datasets.map((dataset) => ({
    value: dataset.dataset_id,
    label: `${dataset.name}（${dataset.item_count || 0} 题）`
  }))
)

const selectedDataset = computed(() =>
  props.datasets.find((dataset) => dataset.dataset_id === form.datasetId)
)

const buildDefaultName = () => {
  const now = new Date()
  const date = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((value, index) => (index === 0 ? value : String(value).padStart(2, '0')))
    .join('')
  const suffix = globalThis.crypto?.randomUUID?.().replaceAll('-', '').slice(0, 6) || Date.now().toString(16).slice(-6)
  return `eval-${date}-${suffix}`
}

const evaluationHint = computed(() => {
  if (!selectedDataset.value) return '选择一个已完成的评估基准后开始。'
  if (!selectedDataset.value.has_gold_answers) return '当前基准仅执行检索评估，无需选择模型。'
  if (form.answerModel && form.judgeModel) return '将同时执行检索评估和答案评估。'
  if (!form.answerModel && !form.judgeModel) return '不选择模型时仅执行检索评估。'
  return '答案生成模型和答案评判模型需要同时选择。'
})

const resetForm = () => {
  form.name = buildDefaultName()
  form.datasetId =
    props.datasets.find((dataset) => dataset.dataset_id === props.initialDatasetId)?.dataset_id ||
    props.datasets[0]?.dataset_id ||
    ''
  form.answerModel = ''
  form.judgeModel = ''
  formRef.value?.clearValidate()
}

const handleDatasetChange = () => {
  form.answerModel = ''
  form.judgeModel = ''
}

const closeModal = () => {
  if (submitting.value) return
  visible.value = false
}

const submitEvaluation = async () => {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  const hasAnswerModel = !!form.answerModel
  const hasJudgeModel = !!form.judgeModel
  if (hasAnswerModel !== hasJudgeModel) {
    message.warning('答案生成模型和答案评判模型需要同时选择')
    return
  }

  submitting.value = true
  try {
    const response = await evaluationApi.runEvaluation(props.kbId, {
      dataset_id: form.datasetId,
      name: form.name.trim(),
      model_config: {
        answer_llm: selectedDataset.value?.has_gold_answers ? form.answerModel : '',
        judge_llm: selectedDataset.value?.has_gold_answers ? form.judgeModel : ''
      }
    })
    if (response?.message !== 'success') {
      throw new Error(response?.message || '启动评估失败')
    }

    message.success('评估任务已开始')
    visible.value = false
    emit('success', response.data)
  } catch (error) {
    console.error('启动评估失败:', error)
    message.error(error.message || '启动评估失败')
  } finally {
    submitting.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) resetForm()
  }
)
</script>

<style lang="less" scoped>
.evaluation-model-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.evaluation-run-hint {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--color-info-50);
  color: var(--color-info-700);
  font-size: 12px;
  line-height: 1.5;

  svg {
    flex: 0 0 auto;
    margin-top: 1px;
  }
}

@media (max-width: 640px) {
  .evaluation-model-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
