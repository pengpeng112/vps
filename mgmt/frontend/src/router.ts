import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./views/Login.vue') },
    { path: '/', component: () => import('./views/Dashboard.vue') },
    { path: '/devices', component: () => import('./views/Devices.vue') },
    { path: '/devices/:id', component: () => import('./views/DeviceDetail.vue') },
    { path: '/nodes', component: () => import('./views/Nodes.vue') },
    { path: '/tasks', component: () => import('./views/Tasks.vue') },
    { path: '/alerts', component: () => import('./views/Alerts.vue') },
    { path: '/traffic', component: () => import('./views/Traffic.vue') },
    { path: '/audit', component: () => import('./views/Audit.vue') },
    { path: '/settings', component: () => import('./views/Settings.vue') },
  ],
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.path !== '/login' && !token) return '/login'
  if (to.path === '/login' && token) return '/'
})

export default router
