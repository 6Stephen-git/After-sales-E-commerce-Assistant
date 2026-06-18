<template>
  <el-dialog
    v-model="visible"
    title="情绪提醒"
    width="420px"
    :close-on-click-modal="false"
    @closed="on_closed"
  >
    <p class="alert-message">{{ message_text }}</p>
    <template v-if="mode === 'notice'" #footer>
      <el-button type="primary" @click="handle_dismiss">知道了</el-button>
    </template>
    <template v-else #footer>
      <el-button @click="handle_cancel">修改内容</el-button>
      <el-button type="warning" plain @click="handle_confirm_send">{{ confirm_label }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'

// ---------- 情绪弹窗：notice=发送后当场；block=本地秒拦；intercept=预警态发送前拦截 ----------
const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  mode: {
    type: String,
    default: 'block'
  },
  emotion_alert: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:modelValue', 'cancel', 'confirm_send', 'dismiss'])

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const message_text = computed(() => {
  if (props.mode === 'notice') {
    if (props.emotion_alert?.alert_triggered) {
      return '您这条情绪明显过激，后续请先冷静再回复买家。'
    }
    return '您这条措辞偏硬，有情绪上头的迹象，请注意后续回复。'
  }
  if (props.mode === 'intercept') {
    if (props.emotion_alert?.alert_triggered) {
      return '您近期语气仍偏激烈，这条也有激化风险，尚未发出，请修改后再发。'
    }
    return '您近期语气偏硬，这条也有激化风险，尚未发出，请修改后再发。'
  }
  return '您的措辞含过激内容，本条尚未发出，请修改后再发。'
})

const confirm_label = computed(() => (props.mode === 'intercept' ? '确认发送' : '仍要发送'))

const skip_closed_emit = ref(false)

function handle_cancel() {
  skip_closed_emit.value = true
  visible.value = false
  emit('cancel')
}

function handle_confirm_send() {
  skip_closed_emit.value = true
  visible.value = false
  emit('confirm_send')
}

function handle_dismiss() {
  skip_closed_emit.value = true
  visible.value = false
  emit('dismiss')
}

function on_closed() {
  if (!skip_closed_emit.value) {
    if (props.mode === 'notice') {
      emit('dismiss')
    } else {
      emit('cancel')
    }
  }
  skip_closed_emit.value = false
}
</script>

<style scoped>
.alert-message {
  margin: 0;
  line-height: 1.6;
  color: #606266;
}
</style>
