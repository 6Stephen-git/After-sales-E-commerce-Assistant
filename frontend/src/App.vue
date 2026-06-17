<template>
  <el-config-provider>
    <div class="app-container">
      <el-header class="app-header">
        <span class="app-title">商家应诉助手</span>
        <el-button
          class="settings-btn"
          :class="{ 'is-active': settings_visible }"
          :icon="Setting"
          circle
          aria-label="设置"
          @click="open_settings"
        />
      </el-header>

      <el-main class="app-main app-main--dispute">
        <router-view />
      </el-main>

      <!-- 设置浮层：叠在主页之上，右上角可关闭 -->
      <el-drawer
        v-model="settings_visible"
        title="系统设置"
        direction="rtl"
        size="360px"
        :append-to-body="true"
        destroy-on-close
        class="settings-drawer"
      >
        <SettingsView />
      </el-drawer>
    </div>
  </el-config-provider>
</template>

<script setup>
import { ref } from 'vue'
import { Setting } from '@element-plus/icons-vue'
import SettingsView from './views/SettingsView.vue'

// ---------- 设置浮层：控制抽屉显隐 ----------
const settings_visible = ref(false)

// ---------- 打开设置浮层 ----------
function open_settings() {
  settings_visible.value = true
}
</script>

<style scoped>
.app-container {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  background: #f8fafd;
  color: #2d4054;
  border-bottom: 1px solid #e2eaf2;
  box-shadow: none;
}

.app-title {
  font-size: 22px;
  font-weight: 600;
  white-space: nowrap;
  letter-spacing: 0.14em;
  color: #2d4054;
}

.settings-btn {
  color: #5c6b7a;
  background-color: #ffffff;
  border-color: #d5dee8;
}

.settings-btn:hover {
  color: var(--el-color-primary);
  background-color: #ffffff;
  border-color: var(--el-color-primary-light-5);
}

.settings-btn.is-active {
  color: var(--el-color-primary-dark-2);
  background-color: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-5);
}

.app-main {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  padding: 0;
  background: transparent;
}

.app-main :deep(> *) {
  height: 100%;
  min-height: 0;
}

.settings-drawer :deep(.el-drawer__body) {
  display: flex;
  flex-direction: column;
  padding: 20px 0 0;
  overflow: hidden;
}
</style>
