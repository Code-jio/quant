<script setup>
/**
 * OrderBook.vue — 实时订单与持仓簿
 *
 * Tab 1 委托单：所有委托单，实时状态更新，活跃单可撤
 * Tab 2 成交记录：成交明细，方向/盈亏着色
 * Tab 3 持仓明细：成本价、浮动盈亏、盈亏%、估算市值
 */
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { cancelOrder } from '@/api/index.js'
import { useOrderBookWs } from '@/composables/useOrderBookWs.js'

const {
  ordersArray, trades, positions,
  loading, ordersWsAlive, positionsWsAlive,
  lastOrderTime, lastPositionTime,
  reload,
} = useOrderBookWs()

// ── Tab 状态 ─────────────────────────────────────────────────────────────────
const activeTab = ref('orders')

// ── 委托单过滤器 ──────────────────────────────────────────────────────────────
const filterStatus = ref('all')   // all | active | done
const filterSymbol = ref('')

const ACTIVE_STATUS = new Set(['submitting', 'submitted', 'partfilled'])

const filteredOrders = computed(() => {
  let arr = ordersArray.value
  if (filterStatus.value === 'active') arr = arr.filter(o => ACTIVE_STATUS.has(o.status))
  if (filterStatus.value === 'done')   arr = arr.filter(o => !ACTIVE_STATUS.has(o.status))
  if (filterSymbol.value.trim()) {
    const kw = filterSymbol.value.trim().toUpperCase()
    arr = arr.filter(o => o.symbol?.toUpperCase().includes(kw))
  }
  return arr
})

const activeCount   = computed(() => ordersArray.value.filter(o => ACTIVE_STATUS.has(o.status)).length)
const filledCount   = computed(() => trades.length)
const posCount      = computed(() => positions.length)

// ── 订单状态映射 ──────────────────────────────────────────────────────────────
const ORDER_STATUS = {
  submitting: { label: '委托中', type: 'warning',  dot: '#e3b341' },
  submitted:  { label: '已报',   type: 'primary',  dot: '#58a6ff' },
  partfilled: { label: '部成',   type: 'warning',  dot: '#f0883e' },
  filled:     { label: '已成',   type: 'success',  dot: '#3fb950' },
  cancelled:  { label: '已撤',   type: 'info',     dot: '#6e7681' },
  rejected:   { label: '拒绝',   type: 'danger',   dot: '#f85149' },
}
const getOrderStatus = (s) => ORDER_STATUS[s] ?? { label: s, type: 'info', dot: '#6e7681' }

// ── 格式化工具 ────────────────────────────────────────────────────────────────
const fmt = {
  money:  (v, d = 2) => (Number(v) || 0).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }),
  signed: (v) => { const n = Number(v) || 0; return (n >= 0 ? '+¥' : '-¥') + fmt.money(Math.abs(n)) },
  pct:    (v) => { const n = Number(v) || 0; return (n >= 0 ? '+' : '') + n.toFixed(2) + '%' },
  price:  (v) => (Number(v) || 0) === 0 ? '市价' : fmt.money(v, 4),
}
const dirLabel = (d) => d === 'long' ? '多' : '空'
const dirType  = (d) => d === 'long' ? 'success' : 'danger'
const pnlCss   = (v) => Number(v) > 0 ? 'c-green' : Number(v) < 0 ? 'c-red' : 'c-muted'

function commentCn(c) {
  const MAP = { buy_open: '开多', sell_close: '平多', short_open: '开空', cover_close: '平空', market: '市价', limit: '限价' }
  return MAP[c] ?? c
}

const OFFSET_LABELS = { open: '开', close: '平', close_today: '平今', close_yesterday: '平昨' }
const OFFSET_TYPES  = { open: 'primary', close: 'warning', close_today: 'warning', close_yesterday: 'warning' }
function offsetLabel(v) { return OFFSET_LABELS[v] || '' }
function offsetType(v)  { return OFFSET_TYPES[v]  || 'info' }

// ── 撤单 ─────────────────────────────────────────────────────────────────────
const cancelLoading = ref({})

async function handleCancel(order) {
  try {
    await ElMessageBox.confirm(
      `确认撤销委托：${order.symbol} ${dirLabel(order.direction)} @${fmt.price(order.price)} ×${order.volume}？`,
      '撤单确认',
      { confirmButtonText: '确认撤销', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }

  cancelLoading.value[order.order_id] = true
  try {
    await cancelOrder(order.order_id)
    ElMessage.success('撤单请求已提交，等待交易所确认')
  } catch (err) {
    ElMessage.error(`撤单失败：${err.message}`)
  } finally {
    cancelLoading.value[order.order_id] = false
  }
}

// ── 成交方向行颜色 ────────────────────────────────────────────────────────────
function tradeRowClass({ row }) {
  return row.direction === 'long' ? 'row-long' : 'row-short'
}
</script>

<template>
  <div class="ob-wrap">

    <!-- ── 状态栏 ──────────────────────────────────────────────────────────── -->
    <div class="ob-statusbar">
      <div class="sb-left">
        <!-- 委托单 WS -->
        <span class="sb-item">
          <span class="ws-dot" :class="ordersWsAlive ? 'dot-on' : 'dot-off'"></span>
          <span :class="ordersWsAlive ? 'c-green' : 'c-muted'" style="font-size:11px">
            {{ ordersWsAlive ? '订单实时' : '订单断线' }}
          </span>
        </span>
        <!-- 持仓 WS -->
        <span class="sb-item">
          <span class="ws-dot" :class="positionsWsAlive ? 'dot-on' : 'dot-off'"></span>
          <span :class="positionsWsAlive ? 'c-green' : 'c-muted'" style="font-size:11px">
            {{ positionsWsAlive ? '持仓实时' : '持仓断线' }}
          </span>
        </span>
        <span class="sb-sep"></span>
        <span v-if="lastOrderTime"    class="sb-item c-muted" style="font-size:11px">订单更新 {{ lastOrderTime }}</span>
        <span v-if="lastPositionTime" class="sb-item c-muted" style="font-size:11px">持仓更新 {{ lastPositionTime }}</span>
      </div>
      <div class="sb-right">
        <el-button size="small" plain :loading="loading" @click="reload">
          <el-icon><RefreshRight /></el-icon>刷新
        </el-button>
      </div>
    </div>

    <!-- ── 标签页 ──────────────────────────────────────────────────────────── -->
    <el-tabs v-model="activeTab" class="ob-tabs">

      <!-- ══════════════════════════════════════════ -->
      <!--  Tab 1：委托单                            -->
      <!-- ══════════════════════════════════════════ -->
      <el-tab-pane name="orders">
        <template #label>
          <span class="tab-label">
            委托单
            <el-badge v-if="activeCount > 0" :value="activeCount" type="warning" class="tab-badge" />
          </span>
        </template>

        <!-- 过滤工具栏 -->
        <div class="filter-bar">
          <el-radio-group v-model="filterStatus" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="active">活跃</el-radio-button>
            <el-radio-button value="done">历史</el-radio-button>
          </el-radio-group>
          <el-input
            v-model="filterSymbol"
            placeholder="合约过滤…"
            size="small"
            clearable
            style="width:140px"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <span class="c-muted" style="font-size:11px">{{ filteredOrders.length }} 条</span>
        </div>

        <!-- 委托单表格 -->
        <el-table
          :data="filteredOrders"
          size="small"
          class="ob-table"
          :empty-text="'暂无委托单'"
          max-height="420"
          :row-class-name="({ row }) => ACTIVE_STATUS.has(row.status) ? 'row-active' : ''"
        >
          <el-table-column label="时间" width="72" fixed>
            <template #default="{ row }">
              <span class="mono c-muted" style="font-size:11px">{{ row.create_time ?? '--' }}</span>
            </template>
          </el-table-column>

          <el-table-column label="合约" width="88" fixed>
            <template #default="{ row }">
              <span class="mono fw-600">{{ row.symbol }}</span>
            </template>
          </el-table-column>

          <el-table-column label="方向/类型" width="90" align="center">
            <template #default="{ row }">
              <div style="display:flex;flex-direction:column;align-items:center;gap:2px">
                <div style="display:flex;align-items:center;gap:3px">
                  <el-tag :type="dirType(row.direction)" size="small" effect="dark">
                    {{ dirLabel(row.direction) }}
                  </el-tag>
                  <el-tag
                    v-if="row.offset && row.offset !== 'open'"
                    :type="offsetType(row.offset)"
                    size="small"
                    effect="plain"
                  >
                    {{ offsetLabel(row.offset) }}
                  </el-tag>
                  <el-tag
                    v-else-if="row.offset === 'open'"
                    type="primary"
                    size="small"
                    effect="plain"
                  >
                    开
                  </el-tag>
                </div>
                <span class="c-muted" style="font-size:10px">{{ commentCn(row.order_type) }}</span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="委托价" width="90" align="right">
            <template #default="{ row }">
              <span class="mono">{{ fmt.price(row.price) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="总量/已成" width="95" align="right">
            <template #default="{ row }">
              <span class="mono">{{ row.volume }}</span>
              <span class="c-muted">/</span>
              <span class="mono" :class="row.traded_volume > 0 ? 'c-green' : 'c-muted'">
                {{ row.traded_volume ?? 0 }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <div style="display:flex;align-items:center;gap:4px;justify-content:center">
                <span
                  class="status-dot-sm"
                  :style="{ background: getOrderStatus(row.status).dot }"
                ></span>
                <el-tag
                  :type="getOrderStatus(row.status).type"
                  size="small" effect="plain"
                >
                  {{ getOrderStatus(row.status).label }}
                </el-tag>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="错误" min-width="80">
            <template #default="{ row }">
              <span v-if="row.error_msg" class="c-red" style="font-size:11px">{{ row.error_msg }}</span>
              <span v-else class="c-muted">—</span>
            </template>
          </el-table-column>

          <el-table-column label="撤单" width="72" align="center" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="ACTIVE_STATUS.has(row.status)"
                size="small"
                type="danger"
                link
                :loading="!!cancelLoading[row.order_id]"
                @click="handleCancel(row)"
              >
                撤单
              </el-button>
              <span v-else class="c-muted">—</span>
            </template>
          </el-table-column>

          <template #empty>
            <div class="ob-empty">
              <el-icon size="36" class="c-muted"><DocumentCopy /></el-icon>
              <p class="c-muted">暂无委托单记录</p>
            </div>
          </template>
        </el-table>
      </el-tab-pane>

      <!-- ══════════════════════════════════════════ -->
      <!--  Tab 2：成交记录                          -->
      <!-- ══════════════════════════════════════════ -->
      <el-tab-pane name="trades">
        <template #label>
          <span class="tab-label">
            成交记录
            <el-badge v-if="filledCount > 0" :value="filledCount" type="success" class="tab-badge" />
          </span>
        </template>

        <el-table
          :data="trades"
          size="small"
          class="ob-table"
          :empty-text="'暂无成交记录'"
          max-height="420"
          :row-class-name="tradeRowClass"
        >
          <el-table-column label="成交时间" width="72" fixed>
            <template #default="{ row }">
              <span class="mono c-muted" style="font-size:11px">{{ row.trade_time }}</span>
            </template>
          </el-table-column>

          <el-table-column label="合约" width="88" fixed>
            <template #default="{ row }">
              <span class="mono fw-600">{{ row.symbol }}</span>
            </template>
          </el-table-column>

          <el-table-column label="方向" width="60" align="center">
            <template #default="{ row }">
              <el-tag :type="dirType(row.direction)" size="small" effect="dark">
                {{ dirLabel(row.direction) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="成交价" width="90" align="right">
            <template #default="{ row }">
              <span class="mono fw-600">{{ fmt.money(row.price, 4) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="数量" width="60" align="right">
            <template #default="{ row }">
              <span class="mono">{{ row.volume }}</span>
            </template>
          </el-table-column>

          <el-table-column label="手续费" width="90" align="right">
            <template #default="{ row }">
              <span class="mono c-muted">{{ row.commission ? '-¥' + fmt.money(row.commission, 4) : '—' }}</span>
            </template>
          </el-table-column>

          <el-table-column label="本笔盈亏" align="right">
            <template #default="{ row }">
              <span
                v-if="row.pnl !== 0"
                class="mono fw-600"
                :class="pnlCss(row.pnl)"
              >
                {{ fmt.signed(row.pnl) }}
              </span>
              <span v-else class="c-muted">—</span>
            </template>
          </el-table-column>

          <el-table-column label="关联委托" width="90">
            <template #default="{ row }">
              <span class="mono c-muted" style="font-size:10px">{{ row.order_id?.slice(-6) ?? '—' }}</span>
            </template>
          </el-table-column>

          <template #empty>
            <div class="ob-empty">
              <el-icon size="36" class="c-muted"><Tickets /></el-icon>
              <p class="c-muted">暂无成交记录</p>
            </div>
          </template>
        </el-table>
      </el-tab-pane>

      <!-- ══════════════════════════════════════════ -->
      <!--  Tab 3：持仓明细                          -->
      <!-- ══════════════════════════════════════════ -->
      <el-tab-pane name="positions">
        <template #label>
          <span class="tab-label">
            持仓明细
            <el-badge v-if="posCount > 0" :value="posCount" type="primary" class="tab-badge" />
          </span>
        </template>

        <el-table
          :data="positions"
          size="small"
          class="ob-table"
          :empty-text="'当前空仓'"
          :row-class-name="({ row }) => row.direction === 'long' ? 'row-long' : 'row-short'"
        >
          <el-table-column label="合约" width="100" fixed>
            <template #default="{ row }">
              <span class="mono fw-600">{{ row.symbol }}</span>
            </template>
          </el-table-column>

          <el-table-column label="方向" width="60" align="center">
            <template #default="{ row }">
              <el-tag :type="dirType(row.direction)" size="small" effect="dark">
                {{ dirLabel(row.direction) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="持仓/冻结" width="90" align="right">
            <template #default="{ row }">
              <span class="mono">{{ row.volume }}</span>
              <span v-if="row.frozen > 0" class="mono c-yellow"> / {{ row.frozen }}</span>
            </template>
          </el-table-column>

          <el-table-column label="成本价" width="100" align="right">
            <template #default="{ row }">
              <span class="mono">{{ fmt.money(row.cost_price, 4) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="现价" width="100" align="right">
            <template #default="{ row }">
              <span class="mono" :class="row.cur_price > 0 ? 'c-text' : 'c-muted'">
                {{ row.cur_price > 0 ? fmt.money(row.cur_price, 4) : '—' }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="浮动盈亏" width="120" align="right">
            <template #default="{ row }">
              <span class="mono fw-600" :class="pnlCss(row.pnl)">
                {{ fmt.signed(row.pnl) }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="盈亏率" width="85" align="right">
            <template #default="{ row }">
              <span class="mono" :class="pnlCss(row.pnl_pct)">
                {{ fmt.pct(row.pnl_pct) }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="估算市值" align="right">
            <template #default="{ row }">
              <span class="mono c-muted">¥{{ fmt.money(row.market_value, 0) }}</span>
            </template>
          </el-table-column>

          <template #empty>
            <div class="ob-empty">
              <el-icon size="36" class="c-muted"><Box /></el-icon>
              <p class="c-muted">当前空仓</p>
            </div>
          </template>
        </el-table>

        <!-- 持仓汇总行 -->
        <div class="pos-summary" v-if="positions.length">
          <span class="c-muted">{{ positions.length }} 个标的</span>
          <span class="sb-sep"></span>
          <span>
            合计浮盈：
            <span
              class="mono fw-600"
              :class="pnlCss(positions.reduce((s, p) => s + p.pnl, 0))"
            >
              {{ fmt.signed(positions.reduce((s, p) => s + p.pnl, 0)) }}
            </span>
          </span>
          <span>
            总市值：
            <span class="mono c-text">
              ¥{{ fmt.money(positions.reduce((s, p) => s + p.market_value, 0), 0) }}
            </span>
          </span>
        </div>

      </el-tab-pane>
    </el-tabs>

  </div>
</template>

<style scoped>
/* ── 容器 ── */
.ob-wrap {
  background: var(--q-panel);
  border: 1px solid var(--q-border);
  border-radius: 10px;
  overflow: hidden;
}

/* ── 状态栏 ── */
.ob-statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 18px;
  border-bottom: 1px solid var(--q-border);
  background: rgba(0,0,0,.2);
  flex-wrap: wrap;
  gap: 6px;
}
.sb-left  { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.sb-right { display: flex; align-items: center; gap: 8px; }
.sb-item  { display: flex; align-items: center; gap: 5px; }
.sb-sep   { width: 1px; height: 14px; background: var(--q-border); }

.ws-dot    { width: 7px; height: 7px; border-radius: 50%; }
.dot-on    { background: var(--q-green); box-shadow: 0 0 5px var(--q-green); animation: pulse 2s infinite; }
.dot-off   { background: var(--q-muted); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.45} }

/* ── Tabs ── */
.ob-tabs { padding: 0 }
:deep(.el-tabs__header) {
  background: rgba(0,0,0,.15);
  margin: 0;
  padding: 0 14px;
  border-bottom: 1px solid var(--q-border);
}
:deep(.el-tabs__content) { padding: 0; }
:deep(.el-tab-pane)      { padding: 0; }

.tab-label  { display: flex; align-items: center; gap: 6px; }
.tab-badge  { margin-left: 2px; }

/* ── 过滤工具栏 ── */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 14px;
  border-bottom: 1px solid var(--q-border);
  background: rgba(0,0,0,.1);
}

/* ── 表格通用 ── */
.ob-table { width: 100%; }

/* 委托单行色 */
:deep(.row-active td) { background: rgba(88,166,255,.04) !important; }

/* 成交行色 */
:deep(.row-long td:first-child)  { border-left: 3px solid var(--q-green) !important; }
:deep(.row-short td:first-child) { border-left: 3px solid var(--q-red)   !important; }

/* ── 状态点 ── */
.status-dot-sm {
  width: 6px; height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  display: inline-block;
}

/* ── 持仓汇总行 ── */
.pos-summary {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 8px 18px;
  border-top: 1px solid var(--q-border);
  background: rgba(0,0,0,.12);
  font-size: 12px;
  flex-wrap: wrap;
}

/* ── 空状态 ── */
.ob-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 0;
  gap: 8px;
}

/* ── 工具类 ── */
.mono    { font-family: var(--q-font-mono); font-variant-numeric: tabular-nums; }
.fw-600  { font-weight: 600; }
.c-green { color: var(--q-green);  }
.c-red   { color: var(--q-red);    }
.c-muted { color: var(--q-muted);  }
.c-text  { color: var(--q-text);   }
.c-yellow{ color: var(--q-yellow); }
</style>
