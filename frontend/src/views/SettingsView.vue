<template>
  <el-card class="settings-card">
    <template #header>
      <span>系统设置</span>
    </template>

    <el-alert
      v-if="error_message"
      type="error"
      :title="error_message"
      :closable="false"
      show-icon
      class="settings-alert"
    />

    <el-form label-width="120px">
      <el-form-item label="商家编号">
        <el-input v-model="merchant_id" disabled />
      </el-form-item>

      <el-form-item label="运行模式">
        <el-select v-model="mode" placeholder="请选择模式">
          <el-option label="辅助模式（assisted）" value="assisted" />
          <el-option label="智能模式（intelligent）" value="intelligent" />
        </el-select>
      </el-form-item>

      <el-form-item label="自动化阈值">
        <el-slider v-model="auto_threshold" :min="0" :max="1" :step="0.05" show-input />
      </el-form-item>
    </el-form>

    <div class="action-row">
      <el-button :loading="loading" @click="load_config">刷新配置</el-button>
      <el-button type="primary" :loading="loading" @click="save_config">保存配置</el-button>
    </div>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { fetchMerchantConfig, updateMerchantConfig } from '../api'

// ---------- 页面状态：商家配置编辑与接口加载状态 ----------
const merchant_id = ref('MERCHANT_DEMO_001')
const mode = ref('assisted')
const auto_threshold = ref(0.8)
const loading = ref(false)
const error_message = ref('')

// ---------- 配置读取：加载后端存储的商家模式 ----------
async function load_config() {
  loading.value = true
  error_message.value = ''
  try {
    const response = await fetchMerchantConfig(merchant_id.value)
    mode.value = response.mode
    auto_threshold.value = Number(response.auto_threshold)
  } catch (error) {
    error_message.value = error.message || '读取商家配置失败'
  } finally {
    loading.value = false
  }
}

// ---------- 配置保存：更新商家模式与阈值 ----------
async function save_config() {
  loading.value = true
  error_message.value = ''
  try {
    const payload = {
      mode: mode.value,
      auto_threshold: Number(auto_threshold.value)
    }
    const response = await updateMerchantConfig(merchant_id.value, payload)
    mode.value = response.mode
    auto_threshold.value = Number(response.auto_threshold)
  } catch (error) {
    error_message.value = error.message || '更新商家配置失败'
  } finally {
    loading.value = false
  }
}

// ---------- 生命周期：页面加载后自动拉取一次配置 ----------
onMounted(() => {
  load_config()
})
</script>

<style scoped>
.settings-card {
  max-width: 760px;
}

.settings-alert {
  margin-bottom: 12px;
}

.action-row {
  display: flex;
  gap: 10px;
}
</style>
