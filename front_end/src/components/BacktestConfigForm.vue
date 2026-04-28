<template>
  <el-card class="bt-config-card" shadow="never">
    <template #header>
      <span class="card-title">策略配置</span>
    </template>

    <el-form :model="form" label-position="top" size="default">
      <el-row :gutter="16">
        <el-col :xs="24" :sm="12" :md="5">
          <el-form-item label="策略">
            <el-select
              v-model="form.strategy_name"
              @change="emit('strategy-change', $event)"
              style="width:100%"
            >
              <el-option
                v-for="s in strategyCatalog"
                :key="s.name"
                :value="s.name"
                :label="s.label"
              >
                <div class="strategy-option">
                  <span>{{ s.label }}</span>
                  <el-tag size="small" type="info">{{ s.desc }}</el-tag>
                </div>
              </el-option>
            </el-select>
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="4">
          <el-form-item label="合约代码">
            <el-input v-model="form.strategy_params.symbol" placeholder="IF9999" />
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="5">
          <el-form-item label="开始日期">
            <el-date-picker
              v-model="form.start_date"
              type="date"
              value-format="YYYY-MM-DD"
              style="width:100%"
            />
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="5">
          <el-form-item label="结束日期">
            <el-date-picker
              v-model="form.end_date"
              type="date"
              value-format="YYYY-MM-DD"
              style="width:100%"
            />
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="12" :md="5">
          <el-form-item label="初始资金">
            <el-input-number
              v-model="form.initial_capital"
              :min="10000"
              :step="100000"
              :controls="false"
              style="width:100%"
            />
          </el-form-item>
        </el-col>
      </el-row>

      <el-row :gutter="16">
        <el-col :xs="24" :sm="8" :md="4">
          <el-form-item label="手续费率">
            <el-input-number
              v-model="form.commission_rate"
              :precision="4"
              :step="0.0001"
              :min="0"
              :controls="false"
              style="width:100%"
            />
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="8" :md="4">
          <el-form-item label="滑点比例">
            <el-input-number
              v-model="form.slip_rate"
              :precision="4"
              :step="0.0001"
              :min="0"
              :controls="false"
              style="width:100%"
            />
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="8" :md="4">
          <el-form-item label="保证金比例">
            <el-input-number
              v-model="form.margin_rate"
              :precision="2"
              :step="0.01"
              :min="0.01"
              :max="1"
              :controls="false"
              style="width:100%"
            />
          </el-form-item>
        </el-col>

        <el-col
          v-for="(val, key) in editableStrategyParams"
          :key="key"
          :xs="24"
          :sm="8"
          :md="3"
        >
          <el-form-item :label="paramLabel(key)">
            <el-input-number
              v-if="typeof val === 'number'"
              v-model="form.strategy_params[key]"
              :precision="val % 1 !== 0 ? 2 : 0"
              :step="val % 1 !== 0 ? 0.1 : 1"
              :controls="false"
              style="width:100%"
            />
            <el-input v-else v-model="form.strategy_params[key]" />
          </el-form-item>
        </el-col>

        <el-col :xs="24" :sm="24" :md="5" class="run-col">
          <el-button
            type="primary"
            size="large"
            :loading="running"
            :disabled="running"
            @click="emit('run')"
            class="run-btn"
          >
            <el-icon v-if="!running"><VideoPlay /></el-icon>
            {{ running ? '回测中…' : '运行回测' }}
          </el-button>
        </el-col>
      </el-row>
    </el-form>
  </el-card>
</template>

<script setup>
import { VideoPlay } from '@element-plus/icons-vue'

import { paramLabel } from '@/config/backtest.js'

const form = defineModel('form', { type: Object, required: true })

defineProps({
  strategyCatalog: { type: Array, default: () => [] },
  editableStrategyParams: { type: Object, default: () => ({}) },
  running: { type: Boolean, default: false },
})

const emit = defineEmits(['strategy-change', 'run'])
</script>

<style scoped>
.bt-config-card {
  margin: 20px 24px 0;
  background: var(--bg-surface, #161b22) !important;
  border: 1px solid var(--border-color, #30363d) !important;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary, #c9d1d9);
}

.run-col {
  display: flex;
  align-items: flex-end;
  padding-bottom: 18px;
}

.run-btn {
  width: 100%;
  font-size: 14px;
  font-weight: 600;
}

.strategy-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
</style>
