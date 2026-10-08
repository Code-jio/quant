import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import { ArrowDown, ArrowLeft, ArrowRight, Bell, Box, Check, Clock, Close, CloseBold, Connection, DataAnalysis, DataLine, Delete, Document, DocumentCopy, Download, Edit, EditPen, FullScreen, Histogram, Loading, Menu, Minus, Monitor, Operation, Plus, Pointer, QuestionFilled, Refresh, RefreshRight, Search, Sell, SemiSelect, Setting, Star, StarFilled, SwitchButton, Tickets, Timer, TrendCharts, User, VideoPause, VideoPlay, WarningFilled } from '@element-plus/icons-vue'
const ElementPlusIconsVue = {ArrowDown, ArrowLeft, ArrowRight, Bell, Box, Check, Clock, Close, CloseBold, Connection, DataAnalysis, DataLine, Delete, Document, DocumentCopy, Download, Edit, EditPen, FullScreen, Histogram, Loading, Menu, Minus, Monitor, Operation, Plus, Pointer, QuestionFilled, Refresh, RefreshRight, Search, Sell, SemiSelect, Setting, Star, StarFilled, SwitchButton, Tickets, Timer, TrendCharts, User, VideoPause, VideoPlay, WarningFilled}

import App from './App.vue'
import router from './router/index.js'
import './style.css'

const app   = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)
app.use(ElementPlus)
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

document.documentElement.classList.add('dark')

app.mount('#app')
