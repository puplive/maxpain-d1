/**
 * 路由表。
 * 所有业务页面都挂在 MainLayout 下，共用左侧菜单和顶栏。
 * 页面组件用动态 import，首屏只加载当前页。
 */
import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '../layouts/MainLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: MainLayout,
      children: [
        {
          path: '',
          name: 'dashboard',
          component: () => import('../views/DashboardView.vue'),
          meta: { title: '看板', subtitle: '多品种行情与 MP 概览' },
        },
        {
          path: 'signals',
          name: 'signals',
          component: () => import('../views/SignalsView.vue'),
          meta: { title: '今日机会', subtitle: '每日扫描结果与交易机会' },
        },
        {
          path: 'calendar',
          name: 'calendar',
          component: () => import('../views/ExpiryCalendarView.vue'),
          meta: { title: '期权日历', subtitle: '各品种期权到期日' },
        },
        {
          path: 'notes',
          name: 'notes',
          component: () => import('../views/NotesView.vue'),
          meta: { title: '研究笔记', subtitle: '知识点、案例与经验复盘' },
        },
        {
          path: 'strategies',
          name: 'strategies',
          component: () => import('../views/StrategiesView.vue'),
          meta: { title: '策略与参数', subtitle: '策略参数与执行配置' },
        },
        {
          path: 'backtest',
          name: 'backtest',
          component: () => import('../views/BacktestView.vue'),
          meta: { title: '回测', subtitle: '历史回测与参数验证' },
        },
      ],
    },
  ],
})

export default router
