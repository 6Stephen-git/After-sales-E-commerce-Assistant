<template>
  <el-dialog v-model="visible" title="结束处理" width="480px" @closed="reset_form">
    <el-form label-width="108px">
      <el-form-item label="最终结果" required>
        <el-radio-group v-model="form.final_outcome">
          <el-radio value="胜">胜</el-radio>
          <el-radio value="败">败</el-radio>
          <el-radio value="和解">和解</el-radio>
          <el-radio value="升级">升级</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="补充说明">
        <el-input v-model="form.outcome_note" type="textarea" :rows="2" placeholder="可选" />
      </el-form-item>
      <el-form-item label="策略采纳">
        <el-checkbox v-model="form.ai_strategy_adopted">已采纳 AI 建议</el-checkbox>
      </el-form-item>
      <el-form-item label="判例入库">
        <el-checkbox v-model="form.save_to_db">写入判例库并复盘</el-checkbox>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">确认结束处理</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { submitDisputeReview } from '../../api'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'submitted'])

const visible = ref(false)
const submitting = ref(false)
const form = reactive({
  final_outcome: '和解',
  outcome_note: '',
  ai_strategy_adopted: false,
  save_to_db: true
})

watch(
  () => props.modelValue,
  (value) => {
    visible.value = value
  },
  { immediate: true }
)

watch(visible, (value) => {
  emit('update:modelValue', value)
})

function reset_form() {
  form.final_outcome = '和解'
  form.outcome_note = ''
  form.ai_strategy_adopted = false
  form.save_to_db = true
}

async function submit() {
  submitting.value = true
  try {
    const result = await submitDisputeReview({
      final_outcome: form.final_outcome,
      outcome_note: form.outcome_note,
      ai_strategy_adopted: form.ai_strategy_adopted,
      save_to_db: form.save_to_db
    })
    const saved = Boolean(result?.saved)
    ElMessage.success(saved ? '复盘已提交' : '处理已结束')
    visible.value = false
    emit('submitted', result)
  } catch (error) {
    ElMessage.error(error.message || '提交失败')
  } finally {
    submitting.value = false
  }
}
</script>
