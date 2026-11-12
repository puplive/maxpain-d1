<script setup lang="ts">
/**
 * 主框架：左侧导航 + 顶栏 + 内容区。
 * 菜单 key 和路由 name 保持一致，activeKey 直接由当前路由推导，
 * 这样刷新页面或直接访问 URL 时菜单高亮也不会错。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton,
  NLayout,
  NLayoutContent,
  NLayoutHeader,
  NLayoutSider,
  NMenu,
  NText,
  type MenuOption,
} from 'naive-ui'

const route = useRoute()
const router = useRouter()
// 左侧导航收起状态；收起后宽度归零，由顶栏按钮展开
const collapsed = ref(false)

const menuOptions: MenuOption[] = [
  { label: '看板', key: 'dashboard' },
  { label: '今日机会', key: 'signals' },
  { label: '期权日历', key: 'calendar' },
  { label: '研究笔记', key: 'notes' },
  { label: '策略与参数', key: 'strategies' },
  { label: '回测', key: 'backtest' },
]

const activeKey = computed(() => String(route.name ?? 'dashboard'))

function onMenuSelect(key: string) {
  void router.push({ name: key })
}
</script>

<template>
  <n-layout has-sider class="app-shell">
    <n-layout-sider
      bordered
      :width="208"
      :collapsed-width="0"
      :collapsed="collapsed"
      :native-scrollbar="false"
    >
      <div class="brand">
        <span class="brand-mark">MP</span>
        <div class="brand-text">
          <strong>MaxPain</strong>
          <span>交易工作台</span>
        </div>
      </div>
      <n-menu
        class="app-menu"
        :value="activeKey"
        :options="menuOptions"
        @update:value="onMenuSelect"
      />
    </n-layout-sider>

    <n-layout>
      <n-layout-header bordered class="app-header">
        <div class="header-left">
          <n-button
            size="tiny"
            quaternary
            :title="collapsed ? '展开导航' : '收起导航'"
            @click="collapsed = !collapsed"
          >
            {{ collapsed ? '»' : '«' }}
          </n-button>
          <div class="header-title">
            <n-text class="title">{{ route.meta.title }}</n-text>
            <span class="hint">{{ route.meta.subtitle }}</span>
          </div>
        </div>
        <div class="header-right">
          <span class="status-dot" />
          <span class="hint">本地 · SQLite</span>
        </div>
      </n-layout-header>

      <n-layout-content
        class="app-content"
        content-style="padding: 15px; height: calc(100vh - 57px); overflow: auto;"
      >
        <router-view />
      </n-layout-content>
    </n-layout>
  </n-layout>
</template>

<style scoped>
.app-shell {
  height: 100vh;
}

.brand {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
}

.brand-mark {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  color: #0b0f14;
  background: linear-gradient(135deg, #7fb0ff, #4c8dff);
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}

.brand-text strong {
  font-size: 14px;
  letter-spacing: 0.3px;
  color: #e8eef5;
}

.brand-text span {
  font-size: 11px;
  color: #7d8998;
}

.app-menu {
  padding: 4px 8px;
}

.app-header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.header-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.title {
  font-size: 15px;
  font-weight: 600;
  color: #e8eef5;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #5cd85a;
  box-shadow: 0 0 0 3px rgba(92, 216, 90, 0.12);
}
</style>
