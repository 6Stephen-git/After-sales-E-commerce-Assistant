<template>
  <el-config-provider>
    <div class="app-container">
      <el-header class="app-header">
        <span class="app-title">商家应诉助手</span>
        <el-menu :default-active="active_menu" mode="horizontal" class="app-menu" @select="handle_menu_select">
          <el-menu-item index="/dispute">辅助模式</el-menu-item>
          <el-menu-item index="/intelligent">智能模式</el-menu-item>
          <el-menu-item index="/settings">设置页</el-menu-item>
        </el-menu>
      </el-header>
      <el-main class="app-main">
        <router-view />
      </el-main>
    </div>
  </el-config-provider>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

// ---------- 路由实例：用于菜单高亮与页面切换 ----------
const route = useRoute()
const router = useRouter()

// ---------- 菜单状态：根据当前路径高亮顶部导航 ----------
const active_menu = computed(() => {
  if (route.path === '/settings') return '/settings'
  if (route.path === '/intelligent') return '/intelligent'
  return '/dispute'
})

// ---------- 菜单跳转：点击导航切换视图 ----------
function handle_menu_select(path) {
  router.push(path)
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
  background-color: #409eff;
  color: #fff;
}
.app-title {
  font-size: 18px;
  font-weight: bold;
  white-space: nowrap;
}
.app-menu {
  flex: 1;
  min-width: 280px;
  border-bottom: none;
  background: transparent;
}
.app-menu :deep(.el-menu-item) {
  color: #eaf3ff;
}
.app-menu :deep(.el-menu-item.is-active) {
  color: #ffffff;
  border-bottom-color: #ffffff;
}
.app-main {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: #f5f7fa;
}

.app-main :deep(> *) {
  height: 100%;
  min-height: 0;
}
</style>
