<template>
  <div class="settings-panel">
    <div class="settings-content">
      <el-alert
        v-if="error_message"
        type="error"
        :title="error_message"
        :closable="false"
        show-icon
        class="settings-alert"
      />

      <section class="settings-section">
        <h3 class="section-title">运行模式</h3>
        <el-form class="settings-form">
          <el-form-item label="运行模式">
            <el-select v-model="mode" placeholder="请选择" class="field-input field-input--wide">
              <el-option label="辅助模式（assisted）" value="assisted" />
            </el-select>
          </el-form-item>

          <el-form-item label="自动化阈值">
            <el-slider
              v-model="auto_threshold"
              :min="0"
              :max="1"
              :step="0.05"
              :show-tooltip="false"
              class="threshold-slider"
            />
          </el-form-item>
        </el-form>
      </section>

      <el-divider class="section-divider" />

      <section class="settings-section">
        <h3 class="section-title">商家配置</h3>
        <el-form class="settings-form">
          <el-form-item label="金额阈值">
            <el-input-number
              v-model="order_amount_threshold"
              :min="0"
              :controls="false"
              class="field-input"
            />
          </el-form-item>

          <el-form-item label="老客单数阈值">
            <el-input-number
              v-model="loyal_customer_order_threshold"
              :min="0"
              :controls="false"
              class="field-input"
            />
          </el-form-item>

          <el-form-item label="老客消费阈值">
            <el-input-number
              v-model="loyal_customer_spend_threshold"
              :min="0"
              :controls="false"
              class="field-input"
            />
          </el-form-item>

          <el-form-item label="补偿金额上限">
            <el-input-number
              v-model="max_compensation"
              :min="0"
              :controls="false"
              class="field-input"
            />
          </el-form-item>

          <el-form-item label="补偿比例上限">
            <el-input-number
              v-model="max_compensation_ratio_percent"
              :min="0"
              :max="100"
              :controls="false"
              class="field-input"
            />
          </el-form-item>
        </el-form>
      </section>
    </div>

    <div class="settings-footer">
      <el-button class="footer-btn" :loading="loading" @click="load_config">刷新配置</el-button>
      <el-button class="footer-btn" type="success" :loading="loading" @click="save_config">保存配置</el-button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { fetchMerchantConfig, updateMerchantConfig } from '../api'

const mode = ref('assisted')
const auto_threshold = ref(0.8)
const order_amount_threshold = ref(500)
const loyal_customer_order_threshold = ref(10)
const loyal_customer_spend_threshold = ref(500)
const max_compensation = ref(0)
const max_compensation_ratio_percent = ref(0)
const loading = ref(false)
const error_message = ref('')

// ---------- 比例换算：API 存 0~1，界面展示 0~100% ----------
function ratio_to_percent(ratio) {
  return Math.round(Number(ratio || 0) * 100)
}

function percent_to_ratio(percent) {
  return Number(percent || 0) / 100
}

// ---------- 配置加载：从后端读取商家配置 ----------
function apply_response(response) {
  mode.value = response.mode
  auto_threshold.value = Number(response.auto_threshold)
  order_amount_threshold.value = Number(response.order_amount_threshold)
  loyal_customer_order_threshold.value = Number(response.loyal_customer_order_threshold)
  loyal_customer_spend_threshold.value = Number(response.loyal_customer_spend_threshold)
  max_compensation.value = Number(response.max_compensation)
  max_compensation_ratio_percent.value = ratio_to_percent(response.max_compensation_ratio)
}

async function load_config() {
  loading.value = true
  error_message.value = ''
  try {
    const response = await fetchMerchantConfig()
    apply_response(response)
  } catch (error) {
    error_message.value = error.message || '读取商家配置失败'
  } finally {
    loading.value = false
  }
}

// ---------- 配置保存：写回商家配置 ----------
async function save_config() {
  loading.value = true
  error_message.value = ''
  try {
    const payload = {
      mode: mode.value,
      auto_threshold: Number(auto_threshold.value),
      order_amount_threshold: Number(order_amount_threshold.value),
      loyal_customer_order_threshold: Number(loyal_customer_order_threshold.value),
      loyal_customer_spend_threshold: Number(loyal_customer_spend_threshold.value),
      max_compensation: Number(max_compensation.value),
      max_compensation_ratio: percent_to_ratio(max_compensation_ratio_percent.value)
    }
    const response = await updateMerchantConfig(payload)
    apply_response(response)
  } catch (error) {
    error_message.value = error.message || '更新商家配置失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load_config()
})
</script>

<style scoped>
.settings-panel {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  margin: -20px;
}

.settings-content {
  flex: 1;
  padding: 4px 20px 16px;
}

.settings-alert {
  margin-bottom: 16px;
}

.settings-section {
  margin-bottom: 4px;
}

.section-title {
  margin: 0 0 14px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.section-divider {
  margin: 18px 0;
}

.settings-form :deep(.el-form-item) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.settings-form :deep(.el-form-item__label) {
  flex-shrink: 0;
  padding-right: 12px;
  color: #606266;
  line-height: 32px;
}

.settings-form :deep(.el-form-item__content) {
  flex: 0 0 auto;
  margin-left: 0 !important;
}

.field-input {
  width: 128px;
}

.field-input--wide {
  width: 168px;
}

.settings-form :deep(.field-input.el-input-number) {
  width: 128px;
}

.settings-form :deep(.field-input .el-input__inner),
.settings-form :deep(.field-input input) {
  text-align: center;
}

.threshold-slider {
  width: 168px;
}

.settings-footer {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 14px 20px 16px;
  border-top: 1px solid #ebeef5;
  background: #fff;
}

.footer-btn {
  width: 100%;
  margin: 0;
}
</style>
