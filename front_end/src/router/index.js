import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginView.vue'),
    meta: { requiresAuth: false, title: '连接交易账户' },
  },
  {
    path: '/',
    name: 'Dashboard',
    component: () => import('@/views/DashboardView.vue'),
    meta: { requiresAuth: true, title: '交易监控台' },
  },
  {
    path: '/backtest',
    name: 'Backtest',
    component: () => import('@/views/BacktestView.vue'),
    meta: { requiresAuth: false, title: '回测与分析' },
  },
  {
    path: '/system',
    name: 'System',
    component: () => import('@/views/SystemView.vue'),
    meta: { requiresAuth: false, title: '系统监控' },
  },
  {
    path: '/kline',
    name: 'Kline',
    component: () => import('@/views/KlineView.vue'),
    meta: { requiresAuth: false, title: 'K线行情' },
  },
  {
    path: '/watch',
    name: 'Watch',
    component: () => import('@/views/WatchView.vue'),
    meta: { requiresAuth: false, title: '盯盘系统' },
  },
  // 未匹配的路由重定向到首页
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// ── 导航守卫 ──────────────────────────────────────────────────────────────
router.beforeEach((to) => {
  // 更新页面标题
  if (to.meta.title) document.title = `${to.meta.title} · 量化交易系统`

  const token = localStorage.getItem('quant_token')

  // 需要登录但未登录 → 跳到登录页
  if (to.meta.requiresAuth && !token) return { name: 'Login' }

  // 已登录但访问登录页 → 跳到仪表盘
  if (to.name === 'Login' && token) return { name: 'Dashboard' }
})

export default router
