<template>
  <div class="strategy-panel">
    <div class="panel-scroll" :class="{ 'is-empty': !report && !loading }">
      <template v-if="report">
        <FactCard :facts="report.facts" class="panel-block" />
        <StrategyCard
          :strategy="report.strategy"
          :facts="report.facts"
          :matched-rules="report.matched_rules"
          :buyer_profile="report.buyer_profile"
          :similar_cases="report.similar_cases"
          class="panel-block"
        />
        <EmotionAlert :emotion_alert="seller_emotion_alert" class="panel-block" />
        <ScriptCard :scripts="report.scripts" class="panel-block" @use_script="emit_use_script" />
      </template>

      <el-skeleton v-else-if="loading" animated :rows="8" />

      <el-empty v-else class="panel-empty" description='点击左侧「分析对话」后查看分析结果' />
    </div>
  </div>
</template>

<script setup>
import EmotionAlert from './EmotionAlert.vue'
import FactCard from './FactCard.vue'
import StrategyCard from './StrategyCard.vue'
import ScriptCard from './ScriptCard.vue'

defineProps({
  report: {
    type: Object,
    default: null
  },
  loading: {
    type: Boolean,
    default: false
  },
  seller_emotion_alert: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['use_script'])

function emit_use_script(script_text) {
  emit('use_script', script_text)
}
</script>

<style scoped>
.strategy-panel {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.panel-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 8px;
  scrollbar-gutter: stable;
}

.panel-scroll.is-empty {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}

.panel-empty {
  transform: translateY(-36px);
}

.panel-block {
  margin-bottom: 16px;
}
</style>
