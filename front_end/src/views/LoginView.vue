<template>
  <div class="login-page">

    <!-- 背景装饰网格 -->
    <div class="bg-grid" aria-hidden="true"></div>

    <div class="login-wrap">

      <!-- ── 卡片 ─────────────────────────────────────────────────── -->
      <div class="login-card">

        <!-- 标题 -->
        <div class="card-header">
          <span class="logo">⚡</span>
          <div>
            <h1 class="title">量化交易系统</h1>
            <p class="subtitle">连接 CTP 交易/行情通道</p>
          </div>
        </div>

        <!-- 错误横幅 -->
        <el-alert
          v-if="errorMsg"
          :title="errorMsg"
          type="error"
          show-icon
          :closable="true"
          @close="errorMsg = ''"
          class="error-banner"
        />

        <!-- 表单 -->
        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          class="login-form"
          @submit.prevent="handleLogin"
        >
          <!-- ── 账户信息 ── -->
          <div class="form-section-title">账户信息</div>

          <div class="form-row">
            <el-form-item label="投资者账号" prop="username">
              <el-input
                v-model="form.username"
                placeholder="账号"
                :prefix-icon="'User'"
                clearable
                :disabled="connecting"
              />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input
                v-model="form.password"
                type="password"
                placeholder="密码"
                :prefix-icon="'Lock'"
                show-password
                :disabled="connecting"
              />
            </el-form-item>
            <el-form-item label="经纪商ID" prop="broker_id" class="broker-field">
              <el-input
                v-model="form.broker_id"
                placeholder="如 2071"
                :disabled="connecting"
              />
            </el-form-item>
          </div>

          <!-- ── 服务器配置 ── -->
          <div class="form-section-title">服务器配置</div>

          <div class="form-row">
            <!-- 交易前置 -->
            <el-form-item label="交易前置（TD）">
              <el-select
                :model-value="form.td_custom ? '__custom__' : form.td_server"
                @change="onTdSelect"
                :disabled="connecting"
                style="width: 100%"
              >
                <el-option
                  v-for="s in tdServers"
                  :key="s.value"
                  :label="`${s.label}  ${s.value}`"
                  :value="s.value"
                />
                <el-option label="自定义地址…" value="__custom__" />
              </el-select>
              <el-input
                v-if="form.td_custom"
                v-model="form.td_server"
                placeholder="tcp://host:port"
                :disabled="connecting"
                style="margin-top: 6px"
              />
            </el-form-item>

            <!-- 行情前置 -->
            <el-form-item label="行情前置（MD）">
              <el-select
                :model-value="form.md_custom ? '__custom__' : form.md_server"
                @change="onMdSelect"
                :disabled="connecting"
                style="width: 100%"
              >
                <el-option
                  v-for="s in mdServers"
                  :key="s.value"
                  :label="`${s.label}  ${s.value}`"
                  :value="s.value"
                />
                <el-option label="自定义地址…" value="__custom__" />
              </el-select>
              <el-input
                v-if="form.md_custom"
                v-model="form.md_server"
                placeholder="tcp://host:port"
                :disabled="connecting"
                style="margin-top: 6px"
              />
            </el-form-item>
          </div>

          <!-- ── 高级配置（折叠） ── -->
          <div
            class="advanced-toggle"
            @click="showAdvanced = !showAdvanced"
          >
            <el-icon><ArrowRight :style="{ transform: showAdvanced ? 'rotate(90deg)' : '', transition: 'transform .2s' }" /></el-icon>
            高级配置
            <span class="adv-hint">（AppID / 认证码）</span>
          </div>

          <div v-if="showAdvanced" class="advanced-body">
            <div class="form-row">
              <el-form-item label="AppID">
                <el-input v-model="form.app_id" :disabled="connecting" />
              </el-form-item>
              <el-form-item label="认证码（AuthCode）">
                <el-input v-model="form.auth_code" :disabled="connecting" />
              </el-form-item>
            </div>
          </div>

          <!-- ── 连接按钮 ── -->
          <el-button
            type="primary"
            :loading="connecting"
            :loading-icon="'Loading'"
            class="connect-btn"
            @click="handleLogin"
            native-type="submit"
          >
            <template v-if="!connecting">
              <el-icon><Connection /></el-icon>
              连接交易账户
            </template>
            <template v-else>
              正在连接，请稍候…
            </template>
          </el-button>
        </el-form>

        <!-- ── 连接日志 ── -->
        <div v-if="connectLog.length" class="connect-log">
          <div class="log-header">
            <el-icon><Document /></el-icon>
            连接日志
          </div>
          <div ref="logContainer" class="log-body">
            <div
              v-for="(line, i) in connectLog"
              :key="i"
              :class="['log-line',
                line.includes('✔') ? 'log-ok' :
                line.includes('✘') || line.includes('错误') ? 'log-err' :
                line.includes('…') || line.includes('正在') ? 'log-info' : '']"
            >
              {{ line }}
            </div>
          </div>
        </div>

      </div>
      <!-- /login-card -->

      <p class="footer-text">量化交易系统 &copy; 2026 · 仅供内部使用</p>
    </div>
  </div>
</template>

<script setup>
 
import { ref, reactive, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth.js'
import { login, fetchAuthStatus, fetchServers } from '@/api/index.js'

const router    = useRouter()
const authStore = useAuthStore()

// ── 预设服务器（本地备用，会从后端覆盖） ──────────────────────────────────
const TD_SERVERS_DEFAULT = [
  { label: '电信1', value: 'tcp://114.94.128.1:42205'   },
  { label: '联通1', value: 'tcp://140.206.34.161:42205' },
  { label: '电信2', value: 'tcp://114.94.128.5:42205'   },
  { label: '联通2', value: 'tcp://140.206.34.165:42205' },
  { label: '电信3', value: 'tcp://114.94.128.6:42205'   },
  { label: '联通3', value: 'tcp://140.206.34.166:42205' },
]
const MD_SERVERS_DEFAULT = TD_SERVERS_DEFAULT.map(s => ({
  label: s.label,
  value: s.value.replace(':42205', ':42213'),
}))

const tdServers = ref([...TD_SERVERS_DEFAULT])
const mdServers = ref([...MD_SERVERS_DEFAULT])

// 加载服务器预设
fetchServers().then(data => {
  if (data.td_servers?.length) tdServers.value = data.td_servers
  if (data.md_servers?.length) mdServers.value = data.md_servers
}).catch(() => {/* 用本地默认值 */})

// ── 表单数据 ──────────────────────────────────────────────────────────────
const form = reactive({
  username:  '',
  password:  '',
  broker_id: '2071',
  td_server: 'tcp://114.94.128.1:42205',
  md_server: 'tcp://114.94.128.1:42213',
  app_id:      'client_TraderMaster_v1.0.0',
  auth_code:   '20260324LHJYMHBG',
  td_custom:   false,
  md_custom:   false,
})

const formRef         = ref(null)
const showAdvanced    = ref(false)
const connecting      = ref(false)
const errorMsg        = ref('')
const connectLog      = ref([])
const logContainer    = ref(null)

// ── 表单校验规则 ──────────────────────────────────────────────────────────
const rules = {
  username:  [{ required: true, message: '请输入投资者账号', trigger: 'blur' }],
  password:  [{ required: true, message: '请输入密码',       trigger: 'blur' }],
  broker_id: [{ required: true, message: '请输入经纪商ID',   trigger: 'blur' }],
}

// 服务器地址（支持自定义）
function onTdSelect(v) {
  if (v === '__custom__') { form.td_custom = true }
  else { form.td_custom = false; form.td_server = v }
}
function onMdSelect(v) {
  if (v === '__custom__') { form.md_custom = true }
  else { form.md_custom = false; form.md_server = v }
}

// ── 轮询连接日志 ──────────────────────────────────────────────────────────
let pollTimer = null

function startPolling() {
  pollTimer = setInterval(async () => {
    try {
      const status = await fetchAuthStatus()
      if (status.connect_log?.length) {
        connectLog.value = status.connect_log
        // 自动滚到底部
        setTimeout(() => {
          if (logContainer.value) {
            logContainer.value.scrollTop = logContainer.value.scrollHeight
          }
        }, 50)
      }
    } catch { /* 静默 */ }
  }, 2000)
}

function stopPolling() {
  clearInterval(pollTimer)
  pollTimer = null
}

onUnmounted(stopPolling)

// ── 登录处理 ──────────────────────────────────────────────────────────────
const LOGIN_TIMEOUT = 35_000

async function handleLogin() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  connecting.value  = true
  errorMsg.value    = ''
  connectLog.value  = ['[--:--:--] 正在初始化连接…']
  startPolling()

  try {
    const loginPromise = login({
      username:  form.username,
      password:  form.password,
      broker_id: form.broker_id,
      td_server: form.td_server,
      md_server: form.md_server,
      app_id:    form.app_id,
      auth_code: form.auth_code,
    })

    const timeoutPromise = new Promise((_, reject) => {
      setTimeout(() => reject(new Error('登录超时（35s），请检查网络和服务器地址')), LOGIN_TIMEOUT)
    })

    const res = await Promise.race([loginPromise, timeoutPromise])

    authStore.setAuth({
      token:     res.token,
      accountId: res.account_id,
      balance:   res.balance,
    })

    ElMessage.success(`登录成功，账户：${res.account_id}`)
    router.push({ name: 'Dashboard' })

  } catch (err) {
    errorMsg.value = err.message ?? '连接失败，请检查账户信息或网络'
    connectLog.value.push(`[错误] ${errorMsg.value}`)
  } finally {
    connecting.value = false
    stopPolling()
  }
}
</script>

<style scoped>
/* ── 全屏背景 ── */
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--q-bg);
  position: relative;
  overflow: hidden;
}

/* 背景网格 */
.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(88, 166, 255, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(88, 166, 255, 0.04) 1px, transparent 1px);
  background-size: 40px 40px;
  pointer-events: none;
}

/* ── 卡片容器 ── */
.login-wrap {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 680px;
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.login-card {
  width: 100%;
  background: var(--q-panel);
  border: 1px solid var(--q-border);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

/* ── 标题区 ── */
.card-header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 24px 28px 20px;
  border-bottom: 1px solid var(--q-border);
  background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
}

.logo {
  font-size: 36px;
  line-height: 1;
  filter: drop-shadow(0 0 10px rgba(88, 166, 255, 0.5));
}

.title {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 700;
  color: var(--q-blue);
  letter-spacing: 0.5px;
}
.subtitle {
  margin: 0;
  font-size: 12px;
  color: var(--q-muted);
}

/* ── 错误横幅 ── */
.error-banner {
  border-radius: 0;
  border-left: none;
  border-right: none;
}

/* ── 表单 ── */
.login-form {
  padding: 20px 28px 8px;
}

.form-section-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.8px;
  text-transform: uppercase;
  color: var(--q-muted);
  margin-bottom: 12px;
  margin-top: 4px;
}
.form-section-title:not(:first-child) {
  margin-top: 16px;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}
.broker-field { grid-column: 1; }

/* ── 高级配置折叠 ── */
.advanced-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--q-blue);
  cursor: pointer;
  padding: 4px 0 10px;
  user-select: none;
}
.advanced-toggle:hover { opacity: .8; }
.adv-hint { font-size: 11px; color: var(--q-muted); }

.advanced-body {
  background: rgba(0, 0, 0, 0.2);
  border-radius: 6px;
  padding: 12px 16px 4px;
  margin-bottom: 8px;
  border: 1px solid var(--q-border);
}

/* ── 连接按钮 ── */
.connect-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.5px;
  margin-top: 8px;
  margin-bottom: 20px;
}

/* ── 连接日志 ── */
.connect-log {
  border-top: 1px solid var(--q-border);
}

.log-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  font-size: 11px;
  font-weight: 600;
  color: var(--q-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  background: rgba(0, 0, 0, 0.2);
}

.log-body {
  max-height: 160px;
  overflow-y: auto;
  padding: 8px 0 4px;
  font-family: var(--q-font-mono);
  font-size: 11px;
}

.log-line {
  padding: 2px 16px;
  color: var(--q-muted);
  line-height: 1.6;
}
.log-ok   { color: var(--q-green); }
.log-err  { color: var(--q-red);   }
.log-info { color: var(--q-blue);  }

/* ── 底部文字 ── */
.footer-text {
  font-size: 11px;
  color: var(--q-muted);
  margin: 0;
}

/* ── Element Plus 覆盖 ── */
:deep(.el-form-item__label) {
  color: var(--q-muted) !important;
  font-size: 12px;
  padding-bottom: 4px;
}
:deep(.el-select .el-input__wrapper) {
  font-family: var(--q-font-mono);
  font-size: 12px;
}
</style>
