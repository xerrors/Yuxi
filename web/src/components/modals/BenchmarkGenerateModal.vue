<template>
  <a-modal
    v-model:open="visible"
    title="自动生成评估基准"
    width="600px"
    :confirmLoading="generating"
    @ok="handleGenerate"
    @cancel="handleCancel"
  >
    <a-form ref="formRef" :model="formState" :rules="rules" layout="vertical">
      <a-form-item label="基准名称" name="name">
        <a-input v-model:value="formState.name" placeholder="请输入评估基准名称" />
      </a-form-item>

      <a-form-item label="描述" name="description">
        <a-textarea
          v-model:value="formState.description"
          placeholder="请输入评估基准描述（可选）"
          :rows="3"
        />
      </a-form-item>

      <a-form-item label="生成参数" name="params" :extra="extraText">
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item
              label="问题数量"
              name="count"
              :labelCol="{ span: 24 }"
              :wrapperCol="{ span: 24 }"
            >
              <a-input-number
                v-model:value="formState.count"
                :min="1"
                :max="100"
                style="width: 100%"
                placeholder="生成问题数量"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item
              label="相似chunks数量"
              name="neighbors_count"
              :labelCol="{ span: 24 }"
              :wrapperCol="{ span: 24 }"
            >
              <a-input-number
                v-model:value="formState.neighbors_count"
                :min="0"
                :max="10"
                style="width: 100%"
                placeholder="每次参考的chunks总数"
              />
            </a-form-item>
          </a-col>
        </a-row>
      </a-form-item>

      <a-form-item
        label="LLM模型配置"
        name="llm_model_spec"
        :rules="[{ required: true, message: '请选择LLM模型' }]"
      >
        <ModelSelectorComponent
          :model_spec="formState.llm_model_spec"
          placeholder="选择用于生成问题的LLM模型"
          @select-model="handleSelectLLMModel"
        />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup>
import { ref, reactive, computed, watch, h } from 'vue'
import { message } from 'ant-design-vue'
import { evaluationApi } from '@/apis/knowledge_api'
import { useConfigStore } from '@/stores/config'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'

const configStore = useConfigStore()

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  databaseId: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['update:visible', 'success'])

// 默认基准名称
const defaultBenchmarkName = () => {
  const today = new Date().toISOString().slice(0, 10)
  const suffix = Array.from(
    { length: 4 },
    () => '0123456789abcdefghijklmnopqrstuvwxyz'[Math.floor(Math.random() * 36)]
  ).join('')
  return `Test-${today}-${suffix}`
}

// 响应式数据
const formRef = ref()
const generating = ref(false)

const formState = reactive({
  name: defaultBenchmarkName(),
  description: '',
  count: 10,
  neighbors_count: 1,
  llm_model_spec: configStore.config?.default_model || ''
})

// 表单验证规则
const rules = {
  name: [
    { required: true, message: '请输入基准名称', trigger: 'blur' },
    { min: 2, max: 100, message: '基准名称长度应在2-100个字符之间', trigger: 'blur' }
  ],
  count: [{ required: true, message: '请输入生成问题数量', trigger: 'blur' }]
}

// 双向绑定visible
const visible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val)
})

// 说明文本
const extraText = computed(() =>
  h('span', {}, [
    '需要了解评估基准生成原理？查看',
    h(
      'a',
      {
        href: 'https://xerrors.github.io/Yuxi/intro/evaluation.html',
        target: '_blank',
        rel: 'noopener noreferrer'
      },
      '使用说明'
    )
  ])
)

// 生成基准
const handleGenerate = async () => {
  try {
    // 表单验证
    await formRef.value.validate()

    generating.value = true

    const params = {
      name: formState.name,
      description: formState.description,
      count: formState.count,
      neighbors_count: formState.neighbors_count,
      llm_model_spec: formState.llm_model_spec
    }

    const response = await evaluationApi.generateBenchmark(props.databaseId, params)

    if (response.message === 'success') {
      message.success('生成任务已提交，请稍后查看结果')
      handleCancel()
      emit('success')
    } else {
      message.error(response.message || '生成失败')
    }
  } catch (error) {
    console.error('生成失败:', error)
    message.error('生成失败')
  } finally {
    generating.value = false
  }
}

// 取消操作
const handleCancel = () => {
  visible.value = false
  resetForm()
}

// 重置表单
const resetForm = () => {
  formRef.value?.resetFields()
  Object.assign(formState, {
    name: defaultBenchmarkName(),
    description: '',
    count: 10,
    neighbors_count: 1,
    llm_model_spec: configStore.config?.default_model || ''
  })
  generating.value = false
}

// 选择LLM模型
const handleSelectLLMModel = (modelSpec) => {
  formState.llm_model_spec = modelSpec
}

// 监听visible变化
watch(visible, (val) => {
  if (val) {
    resetForm()
  }
})
</script>
