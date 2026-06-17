<template>
  <el-card>
    <template #header>
      <span>推荐话术</span>
    </template>

    <div v-if="scripts?.response_mode" class="mode-row">
      <el-tag type="info">{{ response_mode_label }}</el-tag>
    </div>

    <el-alert
      v-if="usage_tip_text"
      type="success"
      :closable="false"
      show-icon
      :title="usage_tip_text"
      class="usage-tip-alert"
    />

    <div v-if="script_sentences.length > 0" class="script-list">
      <div
        v-for="(sentence, idx) in script_sentences"
        :key="'script-sentence-' + idx"
        class="script-sentence"
        role="button"
        tabindex="0"
        title="点击填入输入框"
        @click="emit_use_script(sentence)"
        @keydown.enter="emit_use_script(sentence)"
      >
        {{ sentence }}
      </div>
    </div>
    <p v-else class="script-empty">暂无话术内容</p>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { getResponseModeLabel } from '../../utils/enums'
import { split_script_sentences } from '../../utils/text'

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

// ---------- 派生状态：话术使用提示（去除内部英文字段名） ----------
const usage_tip_text = computed(() => {
  const raw = String(props.scripts?.usage_tip || '').trim()
  if (!raw) return ''
  return raw
    .replace(/\bdialogue_context\b/gi, '对话语境')
    .replace(/\bevidence_first\b/gi, '举证阶段')
})

// ---------- 派生状态：拆句后的话术列表 ----------
const script_sentences = computed(() => {
  return split_script_sentences(props.scripts?.script)
})

// ---------- 事件分发：把点击句子传递给上层填入输入框 ----------
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

.script-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.script-empty {
  margin: 0;
  color: #909399;
  font-size: 14px;
}
</style>
