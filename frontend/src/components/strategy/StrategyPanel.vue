<template>
  <div class="strategy-panel">
    <template v-if="report">
      <!-- 事实还原（Agent1）：独立卡片，与文档「参考信息区」并存；此处保证首屏可见 -->
      <FactCard :facts="report.facts" class="panel-block" />
      <!-- 核心结论区 + 关键依据区：策略卡片 -->
      <StrategyCard
        :strategy="report.strategy"
        :facts="report.facts"
        :matched-rules="report.matched_rules"
        :buyer_profile="report.buyer_profile"
        :similar_cases="report.similar_cases"
        class="panel-block"
      />
      <!-- 情绪提醒（若触发）：紧接关键依据区之后 -->
      <EmotionAlert :emotion_alert="report.emotion_alert" class="panel-block" />
      <!-- 话术选项 -->
      <ScriptCard :scripts="report.scripts" class="panel-block" @use_script="emit_use_script" />
    </template>

    <el-skeleton v-else-if="loading" animated :rows="8" />

    <el-empty v-else description='点击左侧「请求 AI 帮助」后查看分析结果' />
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
  /* 折叠展开时减少滚动条出现/消失导致的横向抖动 */
  scrollbar-gutter: stable;
  contain: layout;
}

.panel-block {
  margin-bottom: 12px;
}
</style>
