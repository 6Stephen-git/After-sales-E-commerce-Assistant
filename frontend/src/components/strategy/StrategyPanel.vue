<template>
  <div class="strategy-panel">
    <el-skeleton v-if="loading" animated :rows="8" />

    <template v-else-if="report">
      <EmotionAlert :emotion_alert="report.emotion_alert" class="panel-block" />
      <FactCard :facts="report.facts" class="panel-block" />
      <StrategyCard :strategy="report.strategy" class="panel-block" />
      <ScriptCard :scripts="report.scripts" class="panel-block" @use_script="emit_use_script" />
    </template>

    <el-empty v-else description="点击左侧“请求 AI 帮助”后查看分析结果" />
  </div>
</template>

<script setup>
import EmotionAlert from './EmotionAlert.vue'
import FactCard from './FactCard.vue'
import StrategyCard from './StrategyCard.vue'
import ScriptCard from './ScriptCard.vue'

// ---------- 组件输入：报告数据与加载状态 ----------
defineProps({
  report: {
    type: Object,
    default: null
  },
  loading: {
    type: Boolean,
    default: false
  }
})

// ---------- 组件输出：上抛话术使用事件 ----------
const emit = defineEmits(['use_script'])

// ---------- 事件转发：把子组件事件继续抛出 ----------
function emit_use_script(script_text) {
  emit('use_script', script_text)
}
</script>

<style scoped>
.strategy-panel {
  height: 100%;
  overflow-y: auto;
  padding-right: 8px;
}

.panel-block {
  margin-bottom: 12px;
}
</style>
