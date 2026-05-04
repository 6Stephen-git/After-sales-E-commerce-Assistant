<template>
  <el-config-provider>
    <div class="app-container">
      <el-header class="app-header">
        <span class="app-title">商家应诉助手</span>
      </el-header>
      <el-main class="app-main">
        <!-- 辅助模式：左右分栏布局（聊天窗口 + AI 策略面板），详见 Frontend_spec.md -->
        <router-view v-if="false" />
        <el-empty description="前端开发中，敬请期待" />
      </el-main>
    </div>
  </el-config-provider>
</template>

<script setup>
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
  background-color: #409eff;
  color: #fff;
}
.app-title {
  font-size: 18px;
  font-weight: bold;
}
.app-main {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
