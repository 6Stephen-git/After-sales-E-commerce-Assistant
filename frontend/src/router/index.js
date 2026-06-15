import { createRouter, createWebHistory } from 'vue-router'
import DisputeView from '../views/DisputeView.vue'
import SettingsView from '../views/SettingsView.vue'

// ---------- 路由定义：辅助模式 + 设置页面 ----------
const routes = [
  {
    path: '/',
    redirect: '/dispute'
  },
  {
    path: '/dispute',
    name: 'dispute',
    component: DisputeView
  },
  {
    path: '/settings',
    name: 'settings',
    component: SettingsView
  }
]

// ---------- 路由实例：使用 HTML5 History 模式 ----------
const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
