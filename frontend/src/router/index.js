import { createRouter, createWebHistory } from 'vue-router'
import DisputeView from '../views/DisputeView.vue'

// ---------- 路由定义：主页面为纠纷辅助模式 ----------
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
    redirect: '/dispute'
  }
]

// ---------- 路由实例：使用 HTML5 History 模式 ----------
const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
