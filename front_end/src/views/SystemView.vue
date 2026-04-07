<template>
  <div class="system-page">

    <!-- ── 顶部导航栏 ──────────────────────────────────────────────── -->
    <header class="sys-topbar">
      <div class="topbar-left">
        <el-button text @click="router.push('/')">
          <el-icon><ArrowLeft /></el-icon> 返回监控台
        </el-button>
        <span class="sys-title">系统监控</span>
        <el-tag type="info" size="small" effect="plain">基础设施</el-tag>
      </div>
      <div class="topbar-right">
        <span class="c-muted text-sm">实时更新 · 每秒一次</span>
      </div>
    </header>

    <!-- ── 资源监控 ────────────────────────────────────────────────── -->
    <section class="sys-section">
      <div class="section-header">
        <h2 class="section-title">
          <el-icon><Monitor /></el-icon>
          资源 &amp; 网关状态
          <span class="section-hint">CPU · 内存 · 网络速率 · TD/MD 连接 · 延迟</span>
        </h2>
      </div>
      <SystemMonitor />
    </section>

    <!-- ── 日志查看器 ──────────────────────────────────────────────── -->
    <section class="sys-section">
      <div class="section-header">
        <h2 class="section-title">
          <el-icon><Document /></el-icon>
          日志查看器
          <span class="section-hint">关键错误 · 警告过滤 · 关键词搜索 · 实时推送</span>
        </h2>
      </div>
      <LogViewer />
    </section>

  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { ArrowLeft, Monitor, Document } from '@element-plus/icons-vue'
import SystemMonitor from '@/components/SystemMonitor.vue'
import LogViewer     from '@/components/LogViewer.vue'

const router = useRouter()
</script>

<style scoped>
.system-page {
  min-height: 100vh;
  background: var(--bg-base, #0d1117);
  padding: 0 0 48px;
  color: var(--text-primary, #c9d1d9);
}

/* ── 顶部导航栏 ────────────────────────────────────────────────── */
.sys-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  background: var(--bg-surface, #161b22);
  border-bottom: 1px solid var(--border-color, #30363d);
  position: sticky;
  top: 0;
  z-index: 100;
}

.topbar-left { display: flex; align-items: center; gap: 14px; }
.topbar-right { display: flex; align-items: center; gap: 10px; }

.sys-title {
  font-size: 17px;
  font-weight: 600;
  color: var(--text-primary, #c9d1d9);
}

/* ── 区块 ─────────────────────────────────────────────────────── */
.sys-section { padding: 20px 24px 0; }

.section-header { margin-bottom: 14px; }

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #c9d1d9);
  margin: 0;
}

.section-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-muted, #6e7681);
  margin-left: 4px;
}

.c-muted  { color: var(--text-muted, #6e7681); }
.text-sm  { font-size: 12px; }

@media (max-width: 600px) {
  .sys-topbar, .sys-section { padding-left: 12px; padding-right: 12px; }
}
</style>
