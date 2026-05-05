<template>
  <el-card>
    <template #header>
      <span>话术选项</span>
    </template>

    <el-alert
      v-if="scripts?.recommended_version"
      type="success"
      :closable="false"
      show-icon
      :title="`推荐版本：${recommended_label}`"
      class="recommended-alert"
    />

    <el-collapse accordion>
      <el-collapse-item
        v-for="item in script_options"
        :key="item.key"
        :name="item.key"
        :title="item.label"
      >
        <p class="script-content">{{ item.content || '暂无话术内容' }}</p>
        <el-button type="primary" link :disabled="!item.content" @click="emit_use_script(item.content)">
          使用该话术
        </el-button>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { getScriptVersionLabel } from '../../utils/enums'

// ---------- 组件输入：多版本话术 ----------
const props = defineProps({
  scripts: {
    type: Object,
    default: null
  }
})

// ---------- 组件输出：通知上层填充选中话术 ----------
const emit = defineEmits(['use_script'])

// ---------- 派生状态：推荐版本中文标签 ----------
const recommended_label = computed(() => {
  return getScriptVersionLabel(props.scripts?.recommended_version)
})

// ---------- 派生状态：三版本话术统一结构 ----------
const script_options = computed(() => {
  return [
    {
      key: 'defense_version',
      label: getScriptVersionLabel('defense_version'),
      content: props.scripts?.defense_version || ''
    },
    {
      key: 'negotiate_version',
      label: getScriptVersionLabel('negotiate_version'),
      content: props.scripts?.negotiate_version || ''
    },
    {
      key: 'compensate_version',
      label: getScriptVersionLabel('compensate_version'),
      content: props.scripts?.compensate_version || ''
    }
  ]
})

// ---------- 事件分发：把点击话术传递给上层 ----------
function emit_use_script(script_text) {
  emit('use_script', script_text)
}
</script>

<style scoped>
.recommended-alert {
  margin-bottom: 12px;
}

.script-content {
  margin: 0 0 8px;
  color: #606266;
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
