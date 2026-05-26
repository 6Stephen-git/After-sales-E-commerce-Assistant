<template>
  <el-card>
    <template #header>
      <span>推荐话术</span>
    </template>

    <div v-if="scripts?.response_mode" class="mode-row">
      <el-tag type="info">{{ response_mode_label }}</el-tag>
    </div>

    <el-alert
      v-if="scripts?.usage_tip"
      type="success"
      :closable="false"
      show-icon
      :title="scripts.usage_tip"
      class="usage-tip-alert"
    />

    <p class="script-content">{{ scripts?.script || '暂无话术内容' }}</p>
    <el-button type="primary" link :disabled="!scripts?.script" @click="emit_use_script(scripts?.script)">
      使用该话术
    </el-button>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { getResponseModeLabel } from '../../utils/enums'

// ---------- 组件输入：单一推荐话术 ----------
const props = defineProps({
  scripts: {
    type: Object,
    default: null
  }
})

// ---------- 组件输出：通知上层填充选中话术 ----------
const emit = defineEmits(['use_script'])

// ---------- 派生状态：应对思想中文标签 ----------
const response_mode_label = computed(() => {
  return getResponseModeLabel(props.scripts?.response_mode)
})

// ---------- 事件分发：把点击话术传递给上层 ----------
function emit_use_script(script_text) {
  emit('use_script', script_text)
}
</script>

<style scoped>
.mode-row {
  margin-bottom: 12px;
}

.usage-tip-alert {
  margin-bottom: 12px;
}

.script-content {
  margin: 0 0 8px;
  color: #606266;
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
