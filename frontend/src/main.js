import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'

const app = createApp(App)

// 注册所有图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(ElementPlus)

// 全局属性
import { UserFilled, ChatDotRound } from '@element-plus/icons-vue'
app.config.globalProperties.$icons = {
  UserFilled,
  ChatDotRound
}

app.mount('#app')
