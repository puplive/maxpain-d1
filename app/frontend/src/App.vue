<script setup lang="ts">
/**
 * 全局根组件。
 * - NConfigProvider 统一暗色主题、中文文案和主题色
 * - Message/Dialog Provider 是 useMessage()/useDialog() 能用的前提
 */
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  dateZhCN,
  zhCN,
  type GlobalThemeOverrides,
} from 'naive-ui'

// 与 style.css 里的 CSS 变量保持同一套配色
const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#4c8dff',
    primaryColorHover: '#6aa1ff',
    primaryColorPressed: '#3a78e0',
    primaryColorSuppl: '#4c8dff',
    bodyColor: '#0b0f14',
    cardColor: '#121a22',
    modalColor: '#121a22',
    popoverColor: '#121a22',
    tableColor: '#121a22',
    inputColor: '#0f161d',
    borderColor: '#1f2b37',
    dividerColor: '#1a2530',
    textColorBase: '#dbe4ee',
    textColor1: '#e8eef5',
    textColor2: '#b7c2cf',
    textColor3: '#7d8998',
    borderRadius: '6px',
  },
  Layout: {
    color: '#0b0f14',
    siderColor: '#0e141b',
    headerColor: '#0e141b',
    headerBorderColor: '#1a2530',
    siderBorderColor: '#1a2530',
  },
  Menu: {
    itemColorActive: 'rgba(76, 141, 255, 0.14)',
    itemColorActiveHover: 'rgba(76, 141, 255, 0.2)',
    itemTextColorActive: '#7fb0ff',
    itemTextColorActiveHover: '#9cc2ff',
    itemIconColorActive: '#7fb0ff',
    borderRadius: '6px',
    itemHeight: '38px',
  },
  Card: {
    borderColor: '#1f2b37',
    titleFontWeight: '600',
  },
  DataTable: {
    thColor: '#0f161d',
    tdColor: '#121a22',
    tdColorHover: '#16202b',
    borderColor: '#1a2530',
    thTextColor: '#8b97a6',
    tdTextColor: '#c5cfdb',
  },
}
</script>

<template>
  <n-config-provider
    :theme="darkTheme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-message-provider>
      <n-dialog-provider>
        <router-view />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
