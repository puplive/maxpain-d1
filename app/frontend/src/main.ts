/**
 * 应用入口。
 * Pinia 管全局状态，Router 管页面切换。
 */
import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'

createApp(App).use(createPinia()).use(router).mount('#app')
