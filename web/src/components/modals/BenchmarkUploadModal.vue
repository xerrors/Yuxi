<template>
  <a-modal
    v-model:open="visible"
    title="上传评估基准"
    width="600px"
    :mask-closable="!uploading"
    :closable="!uploading"
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

      <a-form-item label="基准文件" name="file">
        <a-upload-dragger
          v-model:fileList="fileList"
          name="file"
          :multiple="false"
          accept=".jsonl"
          :before-upload="beforeUpload"
          @remove="handleRemove"
        >
          <UploadCloud class="upload-icon" />
          <p class="ant-upload-text">点击或拖拽 JSONL 文件到此区域上传</p>
          <p class="ant-upload-hint">每行一个 JSON 对象，仅支持 .jsonl，最大 100MB</p>
        </a-upload-dragger>
      </a-form-item>
    </a-form>
    <template #footer>
      <div class="benchmark-modal-footer">
        <div class="benchmark-help-text">
          需要了解评估基准格式？查看
          <a
            class="benchmark-help-link"
            href="https://xerrors.github.io/Yuxi/intro/evaluation.html"
            target="_blank"
            rel="noopener noreferrer"
          >
            使用说明
          </a>
        </div>
        <div class="footer-actions">
          <a-button :disabled="uploading" @click="handleCancel">取消</a-button>
          <a-button type="primary" :loading="uploading" :disabled="uploading" @click="handleUpload">
            上传
          </a-button>
        </div>
      </div>
    </template>
  </a-modal>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { message } from 'ant-design-vue'
import { UploadCloud } from '@lucide/vue'
import { evaluationApi } from '@/apis/knowledge_api'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  kbId: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['update:visible', 'success'])

// 响应式数据
const formRef = ref()
const fileList = ref([])
const uploading = ref(false)

const formState = reactive({
  name: '',
  description: '',
  file: null
})

// 表单验证规则
const rules = {
  name: [
    { required: true, message: '请输入基准名称', trigger: 'blur' },
    { min: 2, max: 100, message: '基准名称长度应在2-100个字符之间', trigger: 'blur' }
  ],
  file: [{ required: true, message: '请选择基准文件', trigger: 'change' }]
}

// 双向绑定visible
const visible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val)
})

// 文件上传前验证
const beforeUpload = async (file) => {
  // 检查文件类型
  if (!file.name.endsWith('.jsonl')) {
    message.error('仅支持 JSONL 格式文件')
    return false
  }

  // 检查文件大小（限制为100MB）
  const isLt100M = file.size / 1024 / 1024 < 100
  if (!isLt100M) {
    message.error('文件大小不能超过 100MB')
    return false
  }

  try {
    // 读取文件内容验证格式
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = (e) => resolve(e.target.result)
      reader.onerror = () => reject(new Error('文件读取失败'))
      reader.readAsText(file)
    })

    const lines = content.trim().split('\n')

    // 验证至少有一行
    if (lines.length === 0) {
      message.error('文件不能为空')
      return false
    }

    // 验证JSON格式
    for (let i = 0; i < Math.min(5, lines.length); i++) {
      const line = lines[i].trim()
      if (line) {
        JSON.parse(line)
      }
    }

    // 验证通过，设置文件
    formState.file = file
    return true
  } catch (error) {
    if (error instanceof SyntaxError) {
      message.error('文件格式错误，请检查JSONL格式')
    } else {
      message.error('文件验证失败: ' + error.message)
    }
    return false
  }
}

// 移除文件
const handleRemove = () => {
  formState.file = null
}

// 上传文件
const handleUpload = async () => {
  try {
    // 表单验证
    await formRef.value.validate()

    if (!formState.file) {
      message.error('请选择基准文件')
      return
    }

    uploading.value = true

    const response = await evaluationApi.uploadDataset(props.kbId, formState.file, {
      name: formState.name,
      description: formState.description
    })

    if (response.message === 'success') {
      message.success('上传成功')
      handleCancel()
      emit('success')
    } else {
      message.error(response.message || '上传失败')
    }
  } catch (error) {
    console.error('上传失败:', error)
    message.error('上传失败')
  } finally {
    uploading.value = false
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
  fileList.value = []
  formState.file = null
  uploading.value = false
}

// 监听visible变化
watch(visible, (val) => {
  if (!val) {
    resetForm()
  }
})
</script>

<style lang="less" scoped>
:deep(.ant-upload-dragger) {
  padding: 24px 16px;
  border-color: var(--gray-150);
  background: var(--gray-0);
  transition: all 0.2s ease;

  &:hover {
    border-color: var(--main-color);
    background: var(--main-10);
  }

  .ant-upload-text {
    margin: 8px 0 4px;
    font-size: 15px;
    font-weight: 500;
    color: var(--gray-800);
  }

  .ant-upload-hint {
    color: var(--gray-500);
  }
}

.upload-icon {
  width: 44px;
  height: 44px;
  color: var(--main-color);
}

.benchmark-modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.benchmark-help-text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--gray-600);
}

.benchmark-help-link {
  margin-left: 2px;
}

.footer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 640px) {
  .benchmark-modal-footer {
    align-items: flex-start;
    flex-direction: column;
  }

  .footer-actions {
    align-self: flex-end;
  }
}
</style>
