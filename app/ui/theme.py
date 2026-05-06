# -*- coding: utf-8 -*-
"""VPN 管理系统全局样式表。"""
from nicegui import ui


_THEME_STYLE = """

<style>
  :root {
    --vpn-bg: #0f172a;
    --vpn-bg-soft: #111c31;
    --vpn-panel: #182338;
    --vpn-panel-2: #1d2a41;
    --vpn-panel-3: #0f172a;
    --vpn-sidebar: #0a1220;
    --vpn-sidebar-2: #111b2d;
    --vpn-text: #e5edf7;
    --vpn-text-heading: #d9e2ec;
    --vpn-text-display: #cfd9e4;
    --vpn-text-muted: #94a3b8;
    --vpn-text-soft: #64748b;
    --vpn-border: rgba(148, 163, 184, 0.14);
    --vpn-border-strong: rgba(148, 163, 184, 0.24);
    --vpn-accent: #4f8fc7;
    --vpn-accent-soft: #76a8d4;
    --vpn-accent-2: #f59e0b;
    --vpn-danger: #fb7185;
    --vpn-success: #22c55e;
    --vpn-shadow: 0 18px 40px rgba(2, 6, 23, 0.35);
    --vpn-shadow-soft: 0 8px 24px rgba(2, 6, 23, 0.22);
    --vpn-radius-xl: 22px;
    --vpn-radius-lg: 18px;
    --vpn-radius-md: 14px;
    --vpn-radius-sm: 10px;
  }

  body {
    color: var(--vpn-text);
    background:
      radial-gradient(circle at top left, rgba(45, 212, 191, 0.08), transparent 24%),
      radial-gradient(circle at 90% 10%, rgba(245, 158, 11, 0.08), transparent 18%),
      linear-gradient(180deg, #0c1322 0%, #0f172a 100%);
    font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif;
  }

  .q-layout,
  .q-page-container {
    background: transparent !important;
    height: 100vh;
  }

  .q-page {
    background: transparent !important;
    display: flex !important;
    flex-direction: column !important;
    height: 100%;
  }

  .nicegui-content {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    width: 100%;
    max-width: none !important;
    height: 100%;
  }

  .vpn-shell,
  .page-shell,
  .page-panel,
  .compact-panel,
  .overview-grid,
  .dashboard-grid,
  .dashboard-mini-grid {
    width: 100%;
    max-width: none !important;
  }

  .page-shell > *,
  .record-list > *,
  .overview-grid > *,
  .dashboard-grid > *,
  .dashboard-mini-grid > * {
    width: 100%;
    min-width: 0;
  }

  .page-panel > *,
  .compact-panel > * {
    width: 100%;
    min-width: 0;
  }

  .q-header {
    background: rgba(15, 23, 42, 0.76) !important;
    color: var(--vpn-text) !important;
    backdrop-filter: blur(14px);
    border-bottom: 1px solid rgba(148, 163, 184, 0.08);
    box-shadow: 0 6px 18px rgba(2, 6, 23, 0.18);
  }

  .q-drawer {
    background:
      linear-gradient(180deg, var(--vpn-sidebar) 0%, var(--vpn-sidebar-2) 100%) !important;
    color: #fff !important;
    border-right: 1px solid rgba(148, 163, 184, 0.08) !important;
  }

  .vpn-shell {
    width: 100%;
    min-width: 0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    padding: 18px 22px 28px;
    height: 100%;
  }

  .vpn-nav-title {
    font-size: 20px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    letter-spacing: 0.01em;
  }

  .vpn-nav-subtitle {
    margin-top: 4px;
    font-size: 12px;
    color: rgba(226, 232, 240, 0.42);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .vpn-nav-group-label {
    margin: 14px 0 8px;
    font-size: 11px;
    color: rgba(148, 163, 184, 0.4);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .vpn-nav-button {
    width: 100%;
    min-height: 42px;
    justify-content: flex-start;
    border-radius: 10px;
    color: rgba(226, 232, 240, 0.76);
  }

  .vpn-nav-button .q-btn__content {
    width: 100%;
    justify-content: flex-start;
    gap: 10px;
    text-transform: none;
    font-size: 14px;
    font-weight: 500;
  }

  .vpn-nav-button:hover {
    background: rgba(148, 163, 184, 0.08);
    color: #fff;
  }

  .vpn-nav-button.is-active {
    background: rgba(94, 169, 255, 0.1);
    color: #e8f1ff;
    border: 1px solid rgba(94, 169, 255, 0.16);
    position: relative;
  }

  .vpn-nav-button.is-active::before {
    content: "";
    position: absolute;
    left: 0;
    top: 9px;
    bottom: 9px;
    width: 3px;
    border-radius: 999px;
    background: var(--vpn-accent);
  }

  .vpn-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
    padding: 12px 22px;
  }

  .vpn-header-meta {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .vpn-header-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--vpn-text-heading);
  }

  .vpn-header-subtitle {
    font-size: 12px;
    color: var(--vpn-text-soft);
  }

  .vpn-status-chip {
    padding: 6px 12px;
    border-radius: 999px;
    background: rgba(94, 169, 255, 0.1);
    border: 1px solid rgba(94, 169, 255, 0.14);
    color: #dbeafe;
    font-size: 12px;
    font-weight: 600;
  }

  .page-shell {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 18px;
    align-items: stretch;
    min-height: 0;
    height: 100%;
  }

  /* 仅防火墙页：在 .nicegui-content 中占满余高 */
  .page-shell.page-shell--firewall {
    flex: 1 1 0%;
    height: 100%;
  }

  /* 系统设置页（顶栏+页签）：与防火墙同型 flex 链 */
  .page-shell.page-shell--settings {
    flex: 1 1 0%;
  }

  .page-panel,
  .metric-card,
  .alert-card,
  .setup-shell,
  .setup-panel,
  .q-card {
    background: linear-gradient(180deg, rgba(24, 35, 56, 0.94), rgba(17, 27, 45, 0.96)) !important;
    border: 1px solid var(--vpn-border);
    border-radius: var(--vpn-radius-lg) !important;
    box-shadow: var(--vpn-shadow-soft);
  }

  .page-panel,
  .setup-panel {
    padding: 18px;
  }

  .overview-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px;
  }

  .metric-card {
    padding: 18px;
  }

  .metric-card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 14px;
  }

  .metric-card-label {
    font-size: 13px;
    color: var(--vpn-text-muted);
  }

  .metric-card-value {
    font-size: 30px;
    font-weight: 700;
    line-height: 1.1;
    color: var(--vpn-text-display);
  }

  .metric-card-note {
    margin-top: 8px;
    color: var(--vpn-text-soft);
    font-size: 13px;
    line-height: 1.6;
  }

  .section-kicker {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--vpn-accent-soft);
  }

  .section-title {
    margin-top: 6px;
    font-size: 20px;
    font-weight: 600;
    color: var(--vpn-text-heading);
    line-height: 1.35;
  }

  .section-caption {
    color: var(--vpn-text-muted);
    line-height: 1.7;
  }

  .dashboard-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.6fr) minmax(320px, 1fr);
    gap: 18px;
  }

  .dashboard-mini-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 18px;
  }

  .page-topbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .page-title-group {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .page-title {
    font-size: 22px;
    font-weight: 700;
    color: var(--vpn-text-heading);
  }

  .page-subtitle {
    color: var(--vpn-text-muted);
    line-height: 1.7;
  }

  .panel-toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .record-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .record-card {
    padding: 12px 14px;
    border-radius: 14px;
    border: 1px solid rgba(148, 163, 184, 0.1);
    background: linear-gradient(180deg, rgba(30, 41, 59, 0.72), rgba(15, 23, 42, 0.54));
    width: 100%;
  }

  .record-card-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    width: 100%;
  }

  .record-card-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--vpn-text-heading);
  }

  .record-card-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    color: var(--vpn-text-muted);
    font-size: 12px;
  }

  .meta-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border-radius: 999px;
    background: rgba(148, 163, 184, 0.08);
    border: 1px solid rgba(148, 163, 184, 0.12);
    color: #dbeafe;
    font-size: 11px;
  }

  .meta-badge.success {
    background: rgba(34, 197, 94, 0.12);
    border-color: rgba(34, 197, 94, 0.14);
    color: #dcfce7;
  }

  .meta-badge.warn {
    background: rgba(245, 158, 11, 0.12);
    border-color: rgba(245, 158, 11, 0.14);
    color: #fde68a;
  }

  .meta-badge.danger {
    background: rgba(251, 113, 133, 0.12);
    border-color: rgba(251, 113, 133, 0.14);
    color: #fecdd3;
  }

  .record-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    justify-content: flex-end;
  }

  .compact-panel {
    padding: 14px;
    border-radius: 16px;
    border: 1px solid rgba(148, 163, 184, 0.1);
    background: linear-gradient(180deg, rgba(15, 23, 42, 0.46), rgba(15, 23, 42, 0.28));
  }

  .hero-search-panel {
    padding: 22px;
    border-radius: 18px;
    border: 1px solid rgba(148, 163, 184, 0.1);
    background: linear-gradient(180deg, rgba(17, 24, 39, 0.95), rgba(15, 23, 42, 0.92));
    box-shadow: var(--vpn-shadow-soft);
  }

  .search-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 14px;
    align-items: end;
    width: 100%;
  }

  .search-label {
    display: block;
    margin-bottom: 8px;
    color: var(--vpn-text-soft);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }

  .search-input-shell {
    position: relative;
    width: 100%;
  }

  .search-icon {
    position: absolute;
    left: 14px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--vpn-text-soft);
    pointer-events: none;
  }

  .search-input-shell .q-field {
    width: 100%;
  }

  .search-input-shell .q-field__control {
    padding-left: 30px;
    border-radius: 14px !important;
    background: rgba(10, 18, 32, 0.9) !important;
    border: 1px solid rgba(148, 163, 184, 0.08);
  }

  .empty-stage {
    margin-top: 22px;
    min-height: 340px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    border: 2px dashed rgba(31, 41, 55, 0.7);
    border-radius: 20px;
    background: rgba(17, 24, 39, 0.28);
    text-align: center;
    color: var(--vpn-text-muted);
  }

  .empty-stage-icon {
    width: 80px;
    height: 80px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 999px;
    background: rgba(31, 41, 55, 0.32);
    color: rgba(148, 163, 184, 0.5);
  }

  /* 防火墙页：search-shell、tab-bar、规则卡片/行等；通用面板见同文件内 .mgmt-* */
  .firewall-search-shell {
    position: relative;
    width: 100%;
    margin-top: 10px;
  }

  .firewall-search-shell .q-field {
    width: 100%;
  }

  .firewall-search-shell .q-field__control {
    min-height: 52px;
    padding-left: 34px;
    border-radius: 16px !important;
    background: rgba(17, 24, 39, 0.95) !important;
    border: 1px solid rgba(31, 41, 55, 0.95);
  }

  .firewall-search-shell .q-field--outlined .q-field__control:before {
    border-color: rgba(31, 41, 55, 0.95) !important;
  }

  .firewall-rule-card {
    width: 100%;
    padding: 14px 16px;
    border-radius: 14px;
    border: 1px solid rgba(31, 41, 55, 0.9);
    background: rgba(17, 24, 39, 0.86);
  }

  .firewall-rule-card-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 12px;
    align-items: center;
  }

  .firewall-rule-line {
    margin-top: 10px;
    color: #94a3b8;
    font-size: 13px;
    word-break: break-all;
  }

  /* 对端远端：中间列表区吃满 Tab 余高（basis 用 0 避免被内容高度撑死） */
  .firewall-remote-list-stretch {
    display: flex;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    min-width: 0;
    width: 100%;
  }

  /* 防火墙顶栏行：`firewall.py` 用 `firewall-tab-bar`（Tab 外观）+ `mgmt-toolbar-btn` 工具钮（与组管理一致：仅 is-primary 高亮） */
  .firewall-control-header {
    display: flex;
    flex-wrap: nowrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    min-height: 40px;
    margin-bottom: 20px;
    overflow-x: auto;
  }

  .firewall-control-header .firewall-compact-tabs,
  .firewall-control-header .firewall-compact-tabs.q-tabs {
    align-items: center;
    align-self: center;
  }

  .firewall-control-header .firewall-compact-tabs .q-tabs__content.row {
    align-items: center;
  }

  .firewall-control-header .firewall-btn-group {
    flex-wrap: nowrap;
    align-items: center;
    flex-shrink: 0;
  }

  /* ========= script/py/vpn/t.html 顶栏一段（.control-header-row ~ .btn-new-optimized）数值照抄，仅加命名空间 .firewall-tab-bar ========= */
  .firewall-tab-bar.firewall-control-header {
    flex-wrap: nowrap;
    align-items: center;
    justify-content: space-between;
    column-gap: 12px;
    min-height: 40px;
    margin-bottom: 20px;
  }

  .firewall-tab-bar .firewall-compact-tabs.q-tabs {
    flex: 0 0 auto;
    min-height: 40px;
    max-height: 40px;
    box-sizing: border-box;
    align-items: center;
    align-self: center;
    /* 与其他页 compact-panel 一致，避免纯黑底「看不见」 */
    background: var(--vpn-bg-soft);
    border: 1px solid var(--vpn-border-strong);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
    border-radius: 10px;
    padding: 3px 4px;
    display: flex;
  }

  .firewall-tab-bar .firewall-compact-tabs .q-tabs__content {
    align-items: center;
  }

  .firewall-tab-bar .firewall-compact-tabs .q-tabs__content.row {
    flex-wrap: nowrap;
    min-height: 100%;
  }

  .firewall-tab-bar .firewall-compact-tabs .q-tab {
    min-height: 32px;
    height: 32px;
    min-width: 0;
    padding: 0 14px;
    font-size: 13px;
    border-radius: 8px;
    color: var(--vpn-text-muted);
    font-weight: 600;
    text-transform: none;
    border: 1px solid transparent;
    background: rgba(255, 255, 255, 0.03);
    box-shadow: none;
    display: flex;
    align-items: center;
  }

  .firewall-tab-bar .firewall-compact-tabs .q-tab:hover {
    color: var(--vpn-text-heading);
    background: rgba(255, 255, 255, 0.08);
  }

  .firewall-tab-bar .firewall-compact-tabs .q-tab--active {
    color: #2dd4bf !important;
    background: var(--vpn-panel) !important;
    font-weight: 700;
    box-shadow: 0 0 0 1px rgba(45, 212, 191, 0.45), 0 2px 10px rgba(0, 0, 0, 0.25);
  }

  .firewall-tab-bar .firewall-compact-tabs .q-tab--active .q-tab__content,
  .firewall-tab-bar .firewall-compact-tabs .q-tab--active .q-tab__label {
    color: #2dd4bf !important;
  }

  .firewall-tab-bar .firewall-compact-tabs .q-tab__indicator {
    display: none;
  }

  .firewall-tab-bar .firewall-compact-tabs .q-tabs__arrow {
    display: none;
  }

  .firewall-tab-bar .firewall-btn-group {
    display: flex;
    align-items: center;
    align-self: center;
    min-height: 40px;
    gap: 8px;
    flex-wrap: nowrap;
  }

  .firewall-btn-group {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }

  .firewall-search-row {
    width: 100%;
    margin-bottom: 16px;
    flex-shrink: 0;
  }

  /* 与 t.html full-search-bar 同构：单条全宽、左输入右提交 */
  .firewall-inline-search {
    display: flex;
    width: 100%;
    align-items: stretch;
    background: var(--vpn-bg-soft);
    border: 1px solid var(--vpn-border);
    border-radius: 6px;
    overflow: hidden;
    min-height: 40px;
  }
  /* 中心策略：筛选条单独强化，圆角/阴影/高度与顶栏更协调 */
  .firewall-center-owner-search {
    min-height: 44px;
    border-radius: 12px;
    border: 1px solid var(--vpn-border-strong);
    background: linear-gradient(180deg, rgba(30, 41, 59, 0.55) 0%, rgba(15, 23, 42, 0.65) 100%);
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.04),
      0 4px 18px rgba(0, 0, 0, 0.22);
    padding-left: 2px;
  }
  .firewall-center-owner-search .firewall-search-shell {
    padding: 0 0 0 4px;
  }
  .firewall-center-owner-search .q-field--borderless .q-field__control {
    color: var(--vpn-text) !important;
  }
  .firewall-center-owner-search .q-field--borderless .q-field__label {
    color: rgba(148, 163, 184, 0.65) !important;
  }
  .firewall-inline-search .firewall-search-shell {
    margin-top: 0;
    flex: 1 1 auto;
    min-width: 0;
  }
  .firewall-inline-search .q-field__control {
    min-height: 40px;
    border-radius: 0 !important;
    background: transparent !important;
  }
  .firewall-center-owner-search .q-field__control {
    min-height: 44px;
  }
  .firewall-inline-search .q-field--borderless .q-field__control:before,
  .firewall-inline-search .q-field--borderless .q-field__control:after,
  .firewall-inline-search .q-field--outlined .q-field__control:before,
  .firewall-inline-search .q-field--outlined .q-field__control:after {
    border: none !important;
  }
  /* 搜索：与组管理 outline 一致，仅与输入区左缘留一条分隔 */
  .firewall-inline-search .q-btn.firewall-search-submit.mgmt-toolbar-btn.is-outline {
    min-height: 40px;
    border-radius: 0 !important;
    padding: 0 16px;
    box-shadow: none !important;
    border-left: 1px solid var(--vpn-border) !important;
    border-top: none !important;
    border-bottom: none !important;
    border-right: none !important;
  }
  .firewall-center-owner-search .q-btn.firewall-search-submit.mgmt-toolbar-btn.is-outline {
    min-height: 44px;
    background: rgba(15, 23, 42, 0.35) !important;
  }
  .firewall-center-owner-search .q-btn.firewall-search-submit.mgmt-toolbar-btn.is-outline:hover {
    background: rgba(30, 41, 59, 0.55) !important;
  }

  /* 与中心策略复选列宽度对齐，避免拖拽列与标题错位 */
  .firewall-remote-card-checkbox {
    width: 40px;
    min-width: 40px;
    flex-shrink: 0;
  }

  /* 中心 / 远端策略 tab 主体，占满 tab_panels 余高 */
  .firewall-center-canvas,
  .firewall-remote-canvas {
    display: flex;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    /* 子项 flex 分配高度，避免中间列表随内容收缩后下方大块留白 */
    overflow: hidden;
    height: 100%;
  }

  /* 主内容区下：防火墙根列吃满 vpn-shell 纵轴，保证 tab_panels 能分到余量（消除页脚文案下方大块留白） */
  .nicegui-content.vpn-shell .page-shell.page-shell--firewall.mgmt-page {
    flex: 1 1 0%;
    min-height: 0;
    align-self: stretch;
    height: 100%;
  }

  /* 远端：拉取结果区（与 remote_policy_container 同高） */
  .firewall-remote-snapshot {
    display: flex;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    width: 100%;
    height: 100%;
  }

  .firewall-remote-snapshot > .mgmt-panel.mgmt-panel-flex {
    flex: 1 1 0%;
    min-height: 0;
    height: 100%;
  }

  .firewall-tabpanels.q-tab-panels {
    flex: 1 1 0%;
    min-height: 0;
    /* QTabPanels 在部分布局下子面板高度为 0，对端工具栏/下拉不显示，给最小高度兜底 */
    min-height: min(50vh, 28rem);
    background: transparent !important;
    color: var(--vpn-text) !important;
    height: 100%;
  }

  .firewall-tabpanels .q-panel {
    background: transparent !important;
    /* 与 tab_panels 形成纵向 flex，才能把手 stretch 到 rules_container */
    display: flex !important;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    height: 100%;
    overflow: hidden;
  }

  .firewall-tabpanels .q-tab-panel {
    padding: 8px 0 0 !important;
    background: transparent !important;
    /* 子元素：搜索行 + rules_container，后者需 flex:1 占满余高 */
    display: flex !important;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    height: 100%;
    overflow: hidden;
  }

  .firewall-tabpanels .q-tab-panel > .nicegui-column {
    display: flex;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    height: 100%;
    overflow: hidden;
  }

  /* 与组管理「分组列表」同构：外框为 mgmt-panel-list；内层去重，避免再叠一层与组页不一致的大卡片 */
  .firewall-center-shell {
    flex: 1 1 0%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    height: 100%;
  }

  /* NiceGUI 列容器：与 .mgmt-panel-list > .nicegui-column 一致，保证规则区可分配高度 */
  .firewall-center-shell > .nicegui-column {
    display: flex;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    height: 100%;
    overflow: hidden;
  }

  .firewall-center-shell .mgmt-panel.mgmt-panel-flex {
    background: transparent;
    border: none;
    box-shadow: none;
    padding: 0;
    flex: 1 1 0%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  /* 远端列表容器也需要 flex 属性以占满剩余空间 */
  .firewall-remote-list-stretch {
    flex: 1 1 0%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .firewall-peer-remote {
    display: flex;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    width: 100%;
    padding-top: 4px;
  }

  /* 远端策略：标题单独一行，选择框与刷新同排、同高对齐 */
  .firewall-remote-peer-block {
    width: 100%;
  }

  .firewall-remote-peer-toolbar {
    width: 100%;
    align-items: center;
    flex-shrink: 0;
  }

  .firewall-remote-select-wrap {
    flex: 1 1 auto;
    min-width: 0;
  }

  .firewall-remote-select-wrap .q-field {
    width: 100%;
  }

  .firewall-remote-peer-toolbar .q-field--outlined.q-field--dense .q-field__control {
    min-height: 40px;
  }

  .firewall-remote-peer-toolbar .q-btn.mgmt-toolbar-btn {
    min-height: 40px;
  }

  .group-page {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 24px;
    width: 100%;
    min-height: 0;
  }

  .group-header-panel,
  .group-list-panel {
    width: 100%;
    padding: 24px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: #111827;
    box-shadow: var(--vpn-shadow-soft);
  }

  .group-header-row,
  .group-list-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
  }

  .group-header-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .group-title {
    font-size: 30px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    line-height: 1.2;
  }

  .group-desc {
    color: var(--vpn-text-muted);
    font-size: 14px;
    line-height: 1.7;
  }

  .group-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .group-toolbar-btn {
    min-height: 42px;
    padding: 0 16px;
    border-radius: 10px !important;
    font-size: 13px;
    transition: border-color 0.2s ease, background 0.2s ease, color 0.2s ease;
  }

  .group-toolbar-btn .q-btn__content {
    gap: 8px;
    font-weight: 700;
  }

  .group-toolbar-btn .q-focus-helper,
  .group-icon-btn .q-focus-helper,
  .group-checkbox .q-focus-helper,
  .primary-action .q-focus-helper,
  .secondary-action .q-focus-helper,
  .ghost-action .q-focus-helper {
    display: none !important;
    opacity: 0 !important;
  }

  .group-toolbar-btn::before,
  .group-icon-btn::before,
  .primary-action::before,
  .secondary-action::before,
  .ghost-action::before {
    display: none !important;
    opacity: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
  }

  .group-toolbar-btn.q-btn--active,
  .group-toolbar-btn.q-btn--active:hover,
  .group-icon-btn.q-btn--active,
  .group-icon-btn.q-btn--active:hover,
  .primary-action.q-btn--active,
  .secondary-action.q-btn--active,
  .ghost-action.q-btn--active {
    background-image: none !important;
    box-shadow: none !important;
  }

  .q-btn.group-toolbar-btn.is-primary,
  .q-btn.user-toolbar-btn.is-primary,
  .q-btn.settings-btn.is-primary,
  .q-btn.primary-action {
    background: rgba(79, 143, 199, 0.12) !important;
    border: 1px solid rgba(79, 143, 199, 0.26) !important;
    color: #c8ddf0 !important;
    box-shadow: none !important;
  }

  .q-btn.group-toolbar-btn.is-primary:hover,
  .q-btn.user-toolbar-btn.is-primary:hover,
  .q-btn.settings-btn.is-primary:hover,
  .q-btn.primary-action:hover {
    background: rgba(79, 143, 199, 0.18) !important;
    border-color: rgba(118, 168, 212, 0.38) !important;
    color: #e1ebf5 !important;
  }

  .q-btn.group-toolbar-btn.is-outline,
  .q-btn.user-toolbar-btn.is-outline,
  .q-btn.service-toolbar-btn.is-outline,
  .q-btn.settings-btn.is-outline,
  .q-btn.secondary-action,
  .q-btn.ghost-action {
    background: rgba(8, 15, 28, 0.56) !important;
    border: 1px solid rgba(88, 103, 125, 0.34) !important;
    color: #7f8b9c !important;
    box-shadow: none !important;
  }

  .q-btn.group-toolbar-btn.is-outline:hover,
  .q-btn.user-toolbar-btn.is-outline:hover,
  .q-btn.service-toolbar-btn.is-outline:hover,
  .q-btn.settings-btn.is-outline:hover,
  .q-btn.secondary-action:hover,
  .q-btn.ghost-action:hover {
    color: #c0cad6 !important;
    background: rgba(12, 22, 40, 0.72) !important;
    border-color: rgba(107, 124, 148, 0.46) !important;
  }

  .group-list-head {
    padding-bottom: 16px;
    margin-bottom: 18px;
    border-bottom: 1px solid rgba(31, 41, 55, 0.95);
  }

  .group-list-panel {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
    background: #111827;
    border: 1px solid rgba(255, 255, 255, 0.05);
  }

  .group-list-kicker {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #6b7280;
  }

  .group-list-count {
    color: #6b7280;
    font-size: 12px;
  }

  .group-record-list {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 12px;
    width: 100%;
    min-height: 0;
  }

  .group-record-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
    padding: 16px 20px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: rgba(255, 255, 255, 0.02);
    transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease;
  }

  .group-record-card:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(45, 212, 191, 0.45);
    transform: translateY(-1px);
  }

  .group-record-main {
    display: flex;
    align-items: center;
    gap: 24px;
    min-width: 0;
    flex: 1 1 auto;
  }

  .group-checkbox {
    flex: 0 0 auto;
  }

  .group-checkbox .q-checkbox__bg {
    border-color: rgba(75, 85, 99, 0.95) !important;
    background: rgba(17, 24, 39, 0.95);
  }

  .group-record-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }

  .group-record-title {
    color: #2dd4bf;
    font-size: 15px;
    font-weight: 700;
    line-height: 1.2;
  }

  .group-record-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 16px;
    color: #6b7280;
    font-size: 12px;
  }

  .group-record-meta-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .group-record-side {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .group-status-badge {
    padding: 4px 8px;
    border-radius: 6px;
    border: 1px solid rgba(34, 197, 94, 0.24);
    background: rgba(34, 197, 94, 0.12);
    color: #22c55e;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .group-status-badge.is-disabled {
    border-color: rgba(245, 158, 11, 0.24);
    background: rgba(245, 158, 11, 0.12);
    color: #f59e0b;
  }

  .group-uuid-badge {
    padding: 6px 10px;
    border-radius: 8px;
    background: rgba(0, 0, 0, 0.3);
    color: #4b5563;
    font-size: 11px;
    font-family: "Cascadia Code", "Consolas", monospace;
    user-select: all;
  }

  .group-actions {
    display: flex;
    align-items: center;
    gap: 4px;
    justify-content: flex-end;
    flex: 0 0 auto;
  }

  .group-icon-btn {
    min-width: 36px;
    min-height: 36px;
    padding: 0;
    border-radius: 8px !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
  }

  .group-icon-btn .q-icon {
    font-size: 20px !important;
  }

  .group-icon-btn.is-edit {
    color: #2dd4bf !important;
  }

  .group-icon-btn.is-toggle {
    color: #f59e0b !important;
  }

  .group-icon-btn.is-delete {
    color: #fb7185 !important;
  }

  .group-icon-btn.is-edit:hover {
    background: rgba(45, 212, 191, 0.1) !important;
  }

  .group-icon-btn.is-toggle:hover {
    background: rgba(245, 158, 11, 0.1) !important;
  }

  .group-icon-btn.is-delete:hover {
    background: rgba(251, 113, 133, 0.1) !important;
  }

  .group-icon-btn:active,
  .group-icon-btn:focus,
  .group-icon-btn:focus-visible {
    background: transparent !important;
    box-shadow: none !important;
    outline: none !important;
  }

  .group-icon-btn,
  .group-toolbar-btn,
  .primary-action,
  .secondary-action,
  .ghost-action {
    overflow: hidden;
  }

  .user-page {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 24px;
    width: 100%;
    min-height: 0;
  }

  .user-header-panel,
  .user-list-panel {
    width: 100%;
    padding: 24px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: #111827;
    box-shadow: var(--vpn-shadow-soft);
  }

  .user-header-row,
  .user-list-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
  }

  .user-header-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .user-title {
    font-size: 30px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    line-height: 1.2;
  }

  .user-desc {
    color: var(--vpn-text-muted);
    font-size: 14px;
    line-height: 1.7;
  }

  .user-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .user-toolbar-btn {
    min-height: 42px;
    padding: 0 16px;
    border-radius: 10px !important;
    font-size: 13px;
    transition: border-color 0.2s ease, background 0.2s ease, color 0.2s ease;
    overflow: hidden;
  }

  .user-toolbar-btn .q-btn__content {
    gap: 8px;
    font-weight: 700;
  }

  .user-toolbar-btn .q-focus-helper,
  .user-icon-btn .q-focus-helper,
  .user-copy-chip .q-focus-helper {
    display: none !important;
    opacity: 0 !important;
  }

  .user-toolbar-btn::before,
  .user-icon-btn::before,
  .user-copy-chip::before {
    display: none !important;
    opacity: 0 !important;
    background: transparent !important;
    box-shadow: none !important;
  }

  .user-toolbar-btn.q-btn--active,
  .user-icon-btn.q-btn--active,
  .user-copy-chip.q-btn--active {
    background-image: none !important;
    box-shadow: none !important;
  }


  .user-list-head {
    padding-bottom: 16px;
    margin-bottom: 18px;
    border-bottom: 1px solid rgba(31, 41, 55, 0.95);
  }

  .user-list-panel {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
    background: #111827;
    border: 1px solid rgba(255, 255, 255, 0.05);
  }

  .user-list-kicker {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #6b7280;
  }

  .user-record-list {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 12px;
    width: 100%;
    min-height: 0;
  }

  .user-record-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
    padding: 16px 20px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: rgba(255, 255, 255, 0.02);
    transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease;
  }

  .user-record-card:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(45, 212, 191, 0.45);
    transform: translateY(-1px);
  }

  .user-record-main {
    display: flex;
    align-items: center;
    gap: 24px;
    min-width: 0;
    flex: 1 1 auto;
  }

  .user-checkbox {
    flex: 0 0 auto;
  }

  .user-checkbox .q-checkbox__bg {
    border-color: rgba(75, 85, 99, 0.95) !important;
    background: rgba(17, 24, 39, 0.95);
  }

  .user-record-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }

  .user-record-title {
    color: #2dd4bf;
    font-size: 15px;
    font-weight: 700;
    line-height: 1.2;
  }

  .user-record-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    color: #6b7280;
    font-size: 12px;
  }

  .user-record-meta-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .user-copy-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 6px !important;
    background: rgba(0, 0, 0, 0.2) !important;
    color: #9ca3af !important;
    font-size: 11px;
    border: none !important;
    box-shadow: none !important;
    text-transform: none !important;
    cursor: pointer;
    transition: color 0.2s ease, background 0.2s ease, transform 0.2s ease;
    overflow: hidden;
  }

  .user-copy-chip .q-btn__content {
    gap: 4px;
    font-size: 11px;
    font-weight: 500;
  }

  .user-copy-chip .q-icon {
    font-size: 12px !important;
  }

  .user-copy-chip:hover {
    color: #2dd4bf !important;
    background: rgba(45, 212, 191, 0.1) !important;
  }

  .user-copy-chip:active {
    transform: scale(0.97);
  }

  .user-record-side {
    display: flex;
    align-items: center;
    gap: 24px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .user-status-badge {
    padding: 4px 8px;
    border-radius: 6px;
    border: 1px solid rgba(34, 197, 94, 0.2);
    background: rgba(34, 197, 94, 0.1);
    color: #22c55e;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
  }

  .user-status-badge.is-disabled {
    border-color: rgba(245, 158, 11, 0.24);
    background: rgba(245, 158, 11, 0.12);
    color: #f59e0b;
  }

  .user-session-badge {
    padding: 4px 8px;
    border-radius: 6px;
    border: 1px solid rgba(45, 212, 191, 0.2);
    background: rgba(45, 212, 191, 0.1);
    color: #2dd4bf;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
  }

  .user-session-badge.is-offline {
    border-color: rgba(75, 85, 99, 0.35);
    background: rgba(31, 41, 55, 0.72);
    color: #94a3b8;
  }

  .user-side-copy {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: #6b7280;
    font-size: 11px;
    font-family: "Cascadia Code", "Consolas", monospace;
  }

  .user-actions {
    display: flex;
    align-items: center;
    gap: 4px;
    justify-content: flex-end;
    flex: 0 0 auto;
  }

  .user-icon-btn {
    min-width: 36px;
    min-height: 36px;
    padding: 0;
    border-radius: 8px !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    overflow: hidden;
  }

  .user-icon-btn .q-icon {
    font-size: 20px !important;
  }

  .user-icon-btn.is-link {
    color: #2dd4bf !important;
  }

  .user-icon-btn.is-send {
    color: #22c55e !important;
  }

  .user-icon-btn.is-delete {
    color: #fb7185 !important;
  }

  .user-icon-btn.is-link:hover {
    background: rgba(45, 212, 191, 0.1) !important;
  }

  .user-icon-btn.is-send:hover {
    background: rgba(34, 197, 94, 0.1) !important;
  }

  .user-icon-btn.is-delete:hover {
    background: rgba(251, 113, 133, 0.1) !important;
  }

  .user-icon-btn:active,
  .user-icon-btn:focus,
  .user-icon-btn:focus-visible,
  .user-copy-chip:focus,
  .user-copy-chip:focus-visible {
    background-image: none !important;
    box-shadow: none !important;
    outline: none !important;
  }

  .cert-page {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 24px;
    width: 100%;
    min-height: 0;
  }

  .cert-header-panel,
  .cert-list-panel,
  .cert-advanced-panel {
    width: 100%;
    padding: 24px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: #111827;
    box-shadow: var(--vpn-shadow-soft);
  }

  .cert-header-row,
  .cert-list-head,
  .cert-row,
  .cert-advanced-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
  }

  .cert-header-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .cert-title {
    font-size: 30px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    line-height: 1.2;
  }

  .cert-desc {
    color: #9ca3af;
    font-size: 14px;
    font-style: italic;
    line-height: 1.7;
  }

  .cert-refresh-btn,
  .cert-icon-btn,
  .cert-copy-chip,
  .cert-advanced-toggle,
  .cert-danger-btn,
  .cert-outline-btn {
    overflow: hidden;
  }

  .cert-refresh-btn,
  .cert-icon-btn {
    min-width: 36px;
    min-height: 36px;
    padding: 0;
    border-radius: 8px !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
  }

  .cert-refresh-btn {
    color: #9ca3af !important;
  }

  .cert-refresh-btn:hover {
    color: #f8fafc !important;
    background: rgba(255, 255, 255, 0.04) !important;
  }

  .cert-list-panel {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
  }

  .cert-list-head {
    padding-bottom: 16px;
    margin-bottom: 18px;
    border-bottom: 1px solid rgba(31, 41, 55, 0.95);
  }

  .cert-list-kicker {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #6b7280;
  }

  .cert-record-list {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 12px;
    width: 100%;
    min-height: 0;
  }

  .cert-row {
    padding: 16px 20px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: rgba(255, 255, 255, 0.02);
    transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease, opacity 0.2s ease;
  }

  .cert-row:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(45, 212, 191, 0.45);
    transform: translateY(-1px);
  }

  .cert-row.is-muted {
    opacity: 0.6;
  }

  .cert-main {
    display: flex;
    align-items: center;
    gap: 24px;
    min-width: 0;
    flex: 1 1 auto;
  }

  .cert-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }

  .cert-name {
    color: #2dd4bf;
    font-size: 15px;
    font-weight: 700;
    line-height: 1.2;
  }

  .cert-name.is-muted {
    color: #9ca3af;
  }

  .cert-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    color: #6b7280;
    font-size: 11px;
  }

  .cert-meta-item {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }

  .cert-copy-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 0 !important;
    min-height: auto !important;
    background: transparent !important;
    color: #9ca3af !important;
    border: none !important;
    box-shadow: none !important;
    text-transform: none !important;
    cursor: pointer;
    transition: color 0.2s ease, text-decoration-color 0.2s ease;
  }

  .cert-copy-chip .q-btn__content {
    gap: 4px;
    font-size: 11px;
    font-weight: 500;
  }

  .cert-copy-chip .q-icon {
    font-size: 12px !important;
  }

  .cert-copy-chip:hover {
    color: #2dd4bf !important;
    text-decoration: underline;
  }

  .cert-side {
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .cert-status {
    padding: 4px 8px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
  }

  .cert-status.success {
    background: rgba(45, 212, 191, 0.1);
    color: #2dd4bf;
    border: 1px solid rgba(45, 212, 191, 0.2);
  }

  .cert-status.warn {
    background: rgba(245, 158, 11, 0.12);
    color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.24);
  }

  .cert-status.danger {
    background: rgba(251, 113, 133, 0.1);
    color: #fb7185;
    border: 1px solid rgba(251, 113, 133, 0.2);
  }

  .cert-actions {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .cert-icon-btn .q-icon {
    font-size: 20px !important;
  }

  .cert-icon-btn.is-renew {
    color: #2dd4bf !important;
  }

  .cert-icon-btn.is-revoke {
    color: #fb7185 !important;
  }

  .cert-icon-btn.is-renew:hover {
    background: rgba(45, 212, 191, 0.1) !important;
  }

  .cert-icon-btn.is-revoke:hover {
    background: rgba(251, 113, 133, 0.1) !important;
  }

  .cert-icon-btn:disabled {
    color: #1f2937 !important;
    cursor: not-allowed !important;
    background: transparent !important;
  }

  .cert-advanced-panel {
    padding: 0;
    overflow: hidden;
    border-color: rgba(127, 29, 29, 0.3);
  }

  .cert-advanced-toggle {
    width: 100%;
    padding: 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    cursor: pointer;
    transition: background 0.2s ease;
  }

  .cert-advanced-toggle:hover {
    background: rgba(255, 255, 255, 0.04);
  }

  .cert-advanced-copy {
    display: flex;
    align-items: center;
    gap: 12px;
    color: #fb7185;
  }

  .cert-advanced-title {
    font-size: 14px;
    font-weight: 700;
  }

  .cert-advanced-icon {
    color: #374151;
    transition: transform 0.2s ease;
  }

  .cert-advanced-icon.is-open {
    transform: rotate(180deg);
  }

  .cert-advanced-body {
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding: 0 20px 20px;
    border-top: 1px solid rgba(255, 255, 255, 0.05);
  }

  .cert-advanced-note {
    color: #6b7280;
    font-size: 12px;
    line-height: 1.7;
  }

  .cert-advanced-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .cert-outline-btn,
  .cert-danger-btn {
    min-height: 38px;
    padding: 0 16px;
    border-radius: 8px !important;
    font-size: 12px;
  }

  .cert-outline-btn {
    background: transparent !important;
    border: 1px solid #1f2937 !important;
    color: #d1d5db !important;
  }

  .cert-outline-btn:hover {
    border-color: #4b5563 !important;
  }

  .cert-danger-btn {
    background: transparent !important;
    border: 1px solid rgba(127, 29, 29, 0.5) !important;
    color: #fb7185 !important;
  }

  .cert-danger-btn:hover {
    background: rgba(244, 63, 94, 0.12) !important;
  }

  .cert-refresh-btn .q-focus-helper,
  .cert-icon-btn .q-focus-helper,
  .cert-copy-chip .q-focus-helper,
  .cert-outline-btn .q-focus-helper,
  .cert-danger-btn .q-focus-helper {
    display: none !important;
    opacity: 0 !important;
  }

  .cert-refresh-btn::before,
  .cert-icon-btn::before,
  .cert-copy-chip::before,
  .cert-outline-btn::before,
  .cert-danger-btn::before {
    display: none !important;
    background: transparent !important;
    box-shadow: none !important;
  }

  .cert-refresh-btn:focus,
  .cert-refresh-btn:focus-visible,
  .cert-icon-btn:focus,
  .cert-icon-btn:focus-visible,
  .cert-copy-chip:focus,
  .cert-copy-chip:focus-visible,
  .cert-outline-btn:focus,
  .cert-outline-btn:focus-visible,
  .cert-danger-btn:focus,
  .cert-danger-btn:focus-visible {
    outline: none !important;
    box-shadow: none !important;
  }

  .service-page {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 24px;
    width: 100%;
    min-height: 0;
  }

  .service-header-panel,
  .service-list-panel {
    width: 100%;
    padding: 24px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: #111827;
    box-shadow: var(--vpn-shadow-soft);
  }

  .service-header-row,
  .service-list-head,
  .service-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
  }

  .service-header-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .service-title {
    font-size: 30px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    line-height: 1.2;
  }

  .service-desc {
    color: var(--vpn-text-muted);
    font-size: 14px;
    line-height: 1.7;
  }

  .service-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .service-toolbar-btn,
  .service-icon-btn {
    overflow: hidden;
  }

  .service-toolbar-btn {
    min-height: 42px;
    padding: 0 16px;
    border-radius: 10px !important;
    font-size: 13px;
    transition: border-color 0.2s ease, background 0.2s ease, color 0.2s ease;
  }

  .service-toolbar-btn .q-btn__content {
    gap: 8px;
    font-weight: 700;
  }


  .service-list-panel {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
  }

  .service-list-head {
    padding-bottom: 16px;
    margin-bottom: 18px;
    border-bottom: 1px solid rgba(31, 41, 55, 0.95);
  }

  .service-list-meta {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }

  .service-list-kicker {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #6b7280;
  }

  .service-running-chip {
    padding: 2px 8px;
    border-radius: 6px;
    border: 1px solid rgba(45, 212, 191, 0.2);
    background: rgba(45, 212, 191, 0.1);
    color: #2dd4bf;
    font-size: 10px;
    font-weight: 600;
  }

  .service-record-list {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 12px;
    width: 100%;
    min-height: 0;
  }

  .service-row {
    padding: 16px 20px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: rgba(255, 255, 255, 0.02);
    transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease;
  }

  .service-row:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(45, 212, 191, 0.45);
    transform: translateY(-1px);
  }

  .service-main {
    display: flex;
    align-items: center;
    gap: 24px;
    min-width: 0;
    flex: 1 1 auto;
  }

  .service-status-shell {
    width: 40px;
    height: 40px;
    border-radius: 999px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(45, 212, 191, 0.05);
    border: 1px solid rgba(45, 212, 191, 0.1);
    flex: 0 0 auto;
  }

  .service-status-dot {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    display: inline-block;
  }

  .service-status-dot.is-online {
    background: #2dd4bf;
    box-shadow: 0 0 8px rgba(45, 212, 191, 0.5);
    animation: service-pulse 2s infinite;
  }

  .service-status-dot.is-offline {
    background: #6b7280;
  }

  @keyframes service-pulse {
    0% { opacity: 1; }
    50% { opacity: 0.5; }
    100% { opacity: 1; }
  }

  .service-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }

  .service-name-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .service-name {
    font-size: 15px;
    font-weight: 700;
    color: var(--vpn-text-heading);
  }

  .service-proto {
    padding: 2px 8px;
    border-radius: 6px;
    background: rgba(31, 41, 55, 0.95);
    color: #9ca3af;
    font-size: 10px;
    text-transform: uppercase;
  }

  .service-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    color: #6b7280;
    font-size: 11px;
  }

  .service-meta-item {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }

  .service-meta-item.is-network {
    color: rgba(45, 212, 191, 0.8);
  }

  .service-actions {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .service-icon-btn {
    min-width: 36px;
    min-height: 36px;
    padding: 0;
    border-radius: 8px !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
  }

  .service-icon-btn .q-icon {
    font-size: 20px !important;
  }

  .service-icon-btn.is-neutral {
    color: #9ca3af !important;
  }

  .service-icon-btn.is-restart {
    color: #f59e0b !important;
  }

  .service-icon-btn.is-stop {
    color: #fb7185 !important;
  }

  .service-icon-btn.is-start {
    color: #2dd4bf !important;
  }

  .service-icon-btn:hover {
    background: rgba(255, 255, 255, 0.06) !important;
  }

  .service-icon-btn.is-restart:hover {
    background: rgba(245, 158, 11, 0.1) !important;
  }

  .service-icon-btn.is-stop:hover {
    background: rgba(251, 113, 133, 0.1) !important;
  }

  .service-icon-btn.is-start:hover {
    background: rgba(45, 212, 191, 0.1) !important;
  }

  .service-empty {
    width: 100%;
    flex: 1 1 auto;
    min-height: 0;
    padding: 48px 24px;
    text-align: center;
    border: 1px dashed rgba(255, 255, 255, 0.1);
    border-radius: 12px;
    color: #4b5563;
    background: rgba(17, 24, 39, 0.2);
  }

  .service-toolbar-btn .q-focus-helper,
  .service-icon-btn .q-focus-helper {
    display: none !important;
    opacity: 0 !important;
  }

  .service-toolbar-btn::before,
  .service-icon-btn::before {
    display: none !important;
    background: transparent !important;
    box-shadow: none !important;
  }

  .service-toolbar-btn:focus,
  .service-toolbar-btn:focus-visible,
  .service-icon-btn:focus,
  .service-icon-btn:focus-visible {
    outline: none !important;
    box-shadow: none !important;
  }

  .settings-page {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 24px;
    width: 100%;
    min-height: 0;
  }

  .settings-header-panel,
  .settings-panel {
    width: 100%;
    padding: 24px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: #111827;
    box-shadow: var(--vpn-shadow-soft);
  }

  .settings-header-row,
  .settings-panel-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
  }

  .settings-header-copy,
  .settings-panel-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .settings-title {
    font-size: 30px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    line-height: 1.2;
  }

  .settings-desc,
  .settings-panel-desc {
    color: var(--vpn-text-muted);
    font-size: 14px;
    line-height: 1.7;
  }

  .settings-kicker {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #6b7280;
  }

  .settings-panel-title {
    font-size: 20px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    line-height: 1.3;
  }

  .settings-stack {
    display: flex;
    flex-direction: column;
    gap: 12px;
    width: 100%;
    margin-top: 18px;
  }

  .settings-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    width: 100%;
    margin-top: 18px;
  }

  .settings-btn,
  .settings-status-chip {
    overflow: hidden;
  }

  .settings-btn {
    min-height: 42px;
    padding: 0 16px;
    border-radius: 10px !important;
    font-size: 13px;
    transition: border-color 0.2s ease, background 0.2s ease, color 0.2s ease;
  }

  .settings-btn .q-btn__content {
    gap: 8px;
    font-weight: 700;
  }

  .settings-btn.is-warn:hover {
      border-color: #f59e0b !important;
  }

  .settings-status-text {
    margin-top: 12px;
    color: #94a3b8;
    font-size: 13px;
    line-height: 1.7;
  }

  .settings-inline-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
    width: 100%;
  }

  .settings-btn .q-focus-helper {
    display: none !important;
    opacity: 0 !important;
  }

  .settings-btn::before {
    display: none !important;
    background: transparent !important;
    box-shadow: none !important;
  }

  .settings-btn:focus,
  .settings-btn:focus-visible {
    outline: none !important;
    box-shadow: none !important;
  }

  .dashboard-page {
    display: flex;
    flex-direction: column;
    gap: 24px;
    width: 100%;
  }

  .dashboard-header {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
  }

  .dashboard-header-copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .dashboard-kicker {
    color: #2dd4bf;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }

  .dashboard-title {
    font-size: 28px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    line-height: 1.2;
  }

  .dashboard-updated {
    color: #64748b;
    font-size: 12px;
    text-align: right;
  }

  .dashboard-metric-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 20px;
    width: 100%;
  }

  .dashboard-glass-card {
    background: #111b2d;
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 16px;
    padding: 24px;
    box-shadow: var(--vpn-shadow-soft);
  }

  .dashboard-metric-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .dashboard-metric-label {
    font-size: 12px;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .dashboard-metric-value {
    margin: 8px 0;
    font-size: 28px;
    font-weight: 700;
    color: var(--vpn-text-display);
    font-family: "Cascadia Code", "Consolas", monospace;
  }

  .dashboard-metric-footer {
    font-size: 11px;
    color: #475569;
  }

  /* 流量卡：总上行 / 总下行两行同字号、等宽，单位大写 */
  .dashboard-metric--traffic-balance .dashboard-metric-value,
  .dashboard-metric--traffic-balance .dashboard-metric-footer {
    font-size: 22px !important;
    font-weight: 700;
    font-family: "Cascadia Code", "Fira Mono", "Consolas", monospace !important;
    letter-spacing: 0.02em;
  }

  .dashboard-metric--traffic-balance .dashboard-metric-value {
    margin: 6px 0 2px;
    color: var(--vpn-text-display);
  }

  .dashboard-metric--traffic-balance .dashboard-metric-footer {
    margin: 0 0 2px;
    color: var(--vpn-text-display) !important;
  }

  .dashboard-status-footer {
    color: #22c55e;
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }

  .dashboard-badge-pulse {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    background: #22c55e;
    box-shadow: 0 0 8px rgba(34, 197, 94, 0.5);
    animation: service-pulse 2s infinite;
  }

  .dashboard-main-grid {
    display: grid;
    grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr);
    gap: 20px;
    width: 100%;
  }

  .dashboard-card-title {
    font-size: 18px;
    font-weight: 700;
    color: var(--vpn-text-heading);
  }

  .dashboard-card-copy {
    margin-top: 6px;
    color: #64748b;
    font-size: 12px;
  }

  .dashboard-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    color: #cbd5e1;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .dashboard-legend-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  .dashboard-legend-dot {
    width: 8px;
    height: 8px;
    border-radius: 2px;
    display: inline-block;
  }

  .dashboard-line-wrap {
    margin-top: 20px;
    width: 100%;
  }

  .dashboard-line-chart {
    width: 100%;
    height: 220px;
    display: block;
  }

  .dashboard-axis {
    display: flex;
    justify-content: space-between;
    margin-top: 10px;
    color: #64748b;
    font-size: 11px;
    font-weight: 600;
  }

  .dashboard-summary-list {
    display: flex;
    flex-direction: column;
    gap: 0;
    margin-top: 20px;
  }

  .dashboard-summary-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  }

  .dashboard-summary-row:last-child {
    border-bottom: none;
  }

  .dashboard-summary-name {
    color: #64748b;
    font-size: 13px;
  }

  .dashboard-summary-val {
    color: var(--vpn-text-heading);
    font-size: 13px;
    font-weight: 600;
  }

  .dashboard-health-box {
    margin-top: 28px;
    padding: 16px;
    border-radius: 16px;
    background: rgba(15, 23, 42, 0.48);
    border: 1px solid rgba(30, 41, 59, 0.9);
    text-align: center;
  }

  .dashboard-health-label {
    color: #64748b;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }

  .dashboard-health-value {
    margin-top: 8px;
    color: #2dd4bf;
    font-size: 32px;
    font-weight: 800;
  }

  .dashboard-bottom-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 20px;
    width: 100%;
  }

  .dashboard-mini-title {
    color: var(--vpn-text-heading);
    font-size: 14px;
    font-weight: 700;
  }

  .dashboard-mini-copy {
    margin-top: 10px;
    color: #64748b;
    font-size: 12px;
    line-height: 1.7;
  }

  .dashboard-mini-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
  }

  .dashboard-mini-btn {
    min-height: 30px;
    padding: 0 12px;
    border-radius: 8px !important;
    background: rgba(255, 255, 255, 0.02) !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
    color: #6b7280 !important;
    font-size: 11px;
  }

  .dashboard-mini-btn .q-btn__content {
    font-size: 11px;
    font-weight: 600;
    text-transform: none;
  }

  .dashboard-mini-btn:hover {
    color: #ffffff !important;
    background: rgba(255, 255, 255, 0.05) !important;
  }

  .dashboard-mini-btn .q-focus-helper {
    display: none !important;
  }

  .dashboard-mini-btn::before {
    display: none !important;
  }

  .chart-surface {
    min-height: 280px;
    border-radius: 14px;
    border: 1px solid rgba(148, 163, 184, 0.08);
    background:
      linear-gradient(180deg, rgba(15, 23, 42, 0.5), rgba(15, 23, 42, 0.25));
    padding: 18px;
  }

  .chart-bars {
    display: flex;
    align-items: end;
    gap: 12px;
    height: 190px;
    margin-top: 18px;
  }

  .chart-bar {
    flex: 1;
    border-radius: 10px 10px 4px 4px;
    background: linear-gradient(180deg, rgba(45, 212, 191, 0.9), rgba(45, 212, 191, 0.28));
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12);
  }

  .chart-axis {
    display: flex;
    justify-content: space-between;
    margin-top: 10px;
    color: var(--vpn-text-soft);
    font-size: 12px;
  }

  .tiny-donut {
    width: 132px;
    height: 132px;
    margin: 18px auto 12px;
    border-radius: 50%;
    background:
      radial-gradient(circle at center, #182338 38%, transparent 39%),
      conic-gradient(var(--vpn-accent) 0 38%, rgba(148, 163, 184, 0.16) 38% 100%);
    box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.08);
  }

  .tiny-donut.warn {
    background:
      radial-gradient(circle at center, #182338 38%, transparent 39%),
      conic-gradient(var(--vpn-accent-2) 0 56%, rgba(148, 163, 184, 0.16) 56% 100%);
  }

  .tiny-donut.danger {
    background:
      radial-gradient(circle at center, #182338 38%, transparent 39%),
      conic-gradient(var(--vpn-danger) 0 24%, rgba(148, 163, 184, 0.16) 24% 100%);
  }

  .panel-stat {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 0;
    border-bottom: 1px solid rgba(148, 163, 184, 0.08);
  }

  .panel-stat:last-child {
    border-bottom: none;
  }

  .panel-stat-label {
    color: var(--vpn-text-muted);
  }

  .panel-stat-value {
    color: #f8fafc;
    font-weight: 600;
  }

  .action-row {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
  }

  .primary-action,
  .secondary-action,
  .ghost-action {
    border-radius: 10px !important;
  }

  .q-btn .q-btn__content {
    gap: 8px;
    text-transform: none;
    font-weight: 500;
  }

  .q-btn {
    box-shadow: none !important;
  }

  .q-btn--rectangle:before,
  .q-btn--round:before {
    display: none !important;
  }


  /* 锁定视口高度，避免右栏表单过长时被父级 overflow:hidden 裁切；双列时左右各自可滚动 */
  .setup-shell {
    min-height: calc(100vh - 36px);
    height: calc(100vh - 36px);
    max-height: calc(100vh - 36px);
    display: grid;
    grid-template-columns: minmax(0, 1.08fr) minmax(440px, 540px);
    overflow: hidden;
    border-radius: 24px;
    align-items: stretch;
  }

  .setup-brand {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: center;
    min-height: 0;
    padding: 64px 54px;
    background:
      radial-gradient(circle at 16% 18%, rgba(45, 212, 191, 0.18), transparent 27%),
      radial-gradient(circle at 82% 78%, rgba(79, 143, 199, 0.18), transparent 30%),
      linear-gradient(135deg, #07111f 0%, #101b2e 54%, #172640 100%);
    color: #f8fafc;
    overflow-x: hidden;
    overflow-y: auto;
  }

  .setup-brand::after {
    content: "";
    position: absolute;
    right: -100px;
    bottom: -120px;
    width: 320px;
    height: 320px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(45, 212, 191, 0.14), transparent 68%);
  }

  .setup-brand::before {
    content: "";
    position: absolute;
    inset: 0;
    background-image:
      linear-gradient(rgba(148, 163, 184, 0.04) 1px, transparent 1px),
      linear-gradient(90deg, rgba(148, 163, 184, 0.04) 1px, transparent 1px);
    background-size: 42px 42px;
    mask-image: linear-gradient(90deg, rgba(0, 0, 0, 0.68), transparent 82%);
    pointer-events: none;
  }

  .setup-brand-badge {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-radius: 999px;
    background: rgba(45, 212, 191, 0.12);
    border: 1px solid rgba(45, 212, 191, 0.12);
    color: #ccfbf1;
    font-size: 13px;
    font-weight: 600;
    width: fit-content;
    position: relative;
    z-index: 1;
  }

  .setup-brand-title {
    margin-top: 28px;
    font-size: clamp(34px, 4vw, 50px);
    font-weight: 700;
    line-height: 1.18;
    max-width: 560px;
    position: relative;
    z-index: 1;
  }

  .setup-brand-copy {
    margin-top: 18px;
    max-width: 520px;
    color: rgba(226, 232, 240, 0.82);
    line-height: 1.85;
    font-size: 15px;
    position: relative;
    z-index: 1;
  }

  .setup-status-card {
    position: relative;
    z-index: 1;
    width: min(100%, 520px);
    margin-top: 34px;
    padding: 20px 22px;
    border-radius: 20px;
    background:
      linear-gradient(135deg, rgba(15, 23, 42, 0.68), rgba(30, 41, 59, 0.42));
    border: 1px solid rgba(148, 163, 184, 0.16);
    box-shadow: 0 22px 48px rgba(2, 6, 23, 0.24);
    backdrop-filter: blur(14px);
  }

  .setup-status-kicker {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.14em;
    color: rgba(94, 234, 212, 0.72);
    text-transform: uppercase;
  }

  .setup-status-title {
    margin-top: 8px;
    font-size: 24px;
    font-weight: 700;
    color: #f8fafc;
  }

  .setup-status-copy {
    margin-top: 8px;
    color: rgba(226, 232, 240, 0.66);
    line-height: 1.72;
    font-size: 13px;
  }

  .setup-points {
    display: grid;
    gap: 14px;
    margin-top: 24px;
    position: relative;
    z-index: 1;
    width: min(100%, 520px);
  }

  .setup-point {
    padding: 16px 18px;
    border-radius: 16px;
    background: rgba(148, 163, 184, 0.08);
    border: 1px solid rgba(148, 163, 184, 0.08);
    backdrop-filter: blur(8px);
  }

  .setup-point-title {
    font-size: 14px;
    font-weight: 600;
    color: #fff;
  }

  .setup-point-copy {
    margin-top: 6px;
    color: rgba(226, 232, 240, 0.68);
    line-height: 1.7;
    font-size: 13px;
  }

  .setup-panel {
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    gap: 20px;
    min-height: 0;
    overflow-x: hidden;
    overflow-y: auto;
    padding: 54px 46px !important;
    border-left: 1px solid rgba(148, 163, 184, 0.12) !important;
  }

  .setup-panel-header {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .setup-title {
    font-size: 30px;
    font-weight: 700;
    color: #f8fafc;
  }

  .setup-subtitle {
    color: var(--vpn-text-muted);
    line-height: 1.7;
  }

  .setup-steps {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 8px;
  }

  .step-chip {
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(148, 163, 184, 0.06);
    border: 1px solid rgba(148, 163, 184, 0.08);
    min-height: 64px;
  }

  .step-chip.is-current {
    background: rgba(45, 212, 191, 0.12);
    border-color: rgba(45, 212, 191, 0.16);
  }

  .step-chip.is-done {
    background: rgba(34, 197, 94, 0.1);
    border-color: rgba(34, 197, 94, 0.14);
  }

  .step-chip-index {
    font-size: 11px;
    font-weight: 700;
    color: var(--vpn-text-soft);
    text-transform: uppercase;
  }

  .step-chip-title {
    margin-top: 4px;
    font-size: 13px;
    font-weight: 600;
    color: #f8fafc;
  }

  .log-frame {
    overflow: hidden;
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, 0.1);
    background: #0a1220;
  }

  .log-frame .q-log {
    background: transparent !important;
    color: #dbeafe !important;
    font-family: "Cascadia Code", "Consolas", monospace;
  }

  .empty-state {
    padding: 32px 20px;
    width: 100%;
    min-height: 0;
    flex: 1 1 auto;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 24px;
    border: 2px dashed rgba(31, 41, 55, 0.7);
    background: rgba(17, 24, 39, 0.2);
    color: #9ca3af;
    text-align: center;
  }

  .alert-card {
    padding: 16px 18px;
  }

  .q-field__control,
  .q-field--outlined .q-field__control {
    min-height: 46px;
    border-radius: 10px !important;
    background: rgba(15, 23, 42, 0.46);
    color: #f8fafc;
  }

  .q-field__native,
  .q-field__input {
    color: #f8fafc !important;
  }

  .q-field__label,
  .q-placeholder {
    color: var(--vpn-text-soft) !important;
  }

  .q-field--outlined .q-field__control:before {
    border-color: rgba(148, 163, 184, 0.16) !important;
  }

  .q-separator {
    background: rgba(148, 163, 184, 0.08) !important;
  }

  .page-panel .q-field,
  .page-panel .q-select,
  .page-panel .q-textarea,
  .page-panel .q-input,
  .page-panel .q-table,
  .page-panel .q-table__container,
  .page-panel .q-row,
  .page-panel .q-column,
  .compact-panel .q-row,
  .compact-panel .q-column {
    width: 100%;
    min-width: 0;
  }

  @media (max-width: 1280px) {
    .overview-grid,
    .dashboard-mini-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 1120px) {
    .dashboard-grid,
    .setup-shell {
      grid-template-columns: 1fr;
    }

    /* 窄屏纵向堆叠：恢复整页自然滚动，避免双列视口锁高导致布局异常 */
    .setup-shell {
      height: auto;
      max-height: none;
    }

    .setup-brand {
      min-height: min(520px, 70vh);
    }

    .setup-panel {
      overflow-y: visible;
    }

    .record-card-head {
      grid-template-columns: 1fr;
    }

    .group-record-card,
    .user-record-card {
      flex-direction: column;
      align-items: flex-start;
    }

    .group-record-side,
    .user-record-side,
    .group-actions,
    .user-actions {
      justify-content: flex-start;
    }
  }

  @media (max-width: 768px) {
    .vpn-shell {
      padding: 16px;
    }

    .vpn-header {
      padding: 10px 16px;
    }

    .page-panel,
    .metric-card,
    .setup-brand,
    .setup-panel {
      padding: 18px;
    }

    .overview-grid,
    .dashboard-mini-grid,
    .setup-steps {
      grid-template-columns: 1fr;
    }
  }

  /* ========= mgmt-* 通用管理页面组件 ========= */

  .mgmt-page {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 24px;
    min-height: 0;
  }

  .mgmt-panel {
    width: 100%;
    padding: 24px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: #111827;
    box-shadow: var(--vpn-shadow-soft);
  }

  /* 与 .mgmt-panel 同写在一元素上，用于 Tab/分栏内纵向占满 + 可滚动子区 */
  .mgmt-panel-flex {
    display: flex;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    box-sizing: border-box;
    height: 100%;
    overflow: hidden;
  }

  .mgmt-dashed-empty {
    margin-top: 28px;
    min-height: 0;
    flex: 1 1 auto;
    width: 100%;
    border: 2px dashed rgba(31, 41, 55, 0.7);
    border-radius: 24px;
    background: rgba(17, 24, 39, 0.2);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 20px 24px;
  }

  .mgmt-dashed-empty-badge {
    width: 80px;
    height: 80px;
    border-radius: 999px;
    background: rgba(31, 41, 55, 0.36);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #4b5563;
    margin-bottom: 20px;
  }

  .mgmt-dashed-empty-title {
    font-size: 22px;
    font-weight: 600;
    color: #9ca3af;
  }

  .mgmt-dashed-empty-copy {
    margin-top: 8px;
    font-size: 14px;
    color: #6b7280;
  }

  .mgmt-panel-scroll {
    display: flex;
    flex-direction: column;
    gap: 10px;
    width: 100%;
    flex: 1 1 0%;
    min-height: 0;
    overflow-y: auto;
    height: 100%;
    padding-bottom: 20px;
  }

  .mgmt-toolbar-cjk-2 {
    min-width: 5.75rem;
  }

  .mgmt-panel-list {
    display: flex;
    flex-direction: column;
    /* 与 .mgmt-stretch 一致：在 mgmt-page 中作为第二块占满余高 */
    flex: 1 1 0%;
    min-height: 0;
    height: 100%;
  }

  .mgmt-panel-list > .nicegui-column {
    display: flex;
    flex-direction: column;
    flex: 1 1 0%;
    min-height: 0;
    height: 100%;
  }

  .mgmt-header-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .mgmt-header-copy {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .mgmt-title {
    font-size: 22px;
    font-weight: 700;
    color: var(--vpn-text-heading);
    line-height: 1.2;
  }

  .mgmt-desc {
    color: #64748b;
    font-size: 13px;
    letter-spacing: 0.02em;
  }

  .mgmt-toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }

  .mgmt-toolbar-btn {
    min-height: 40px;
    padding: 0 18px;
    border-radius: 10px !important;
    font-size: 13px;
  }

  .mgmt-toolbar-btn.is-primary {
    background: linear-gradient(135deg, #2dd4bf 0%, #14b8a6 100%) !important;
    color: #0f172a !important;
  }

  .mgmt-toolbar-btn.is-primary .q-btn__content {
    font-weight: 700;
    text-transform: none;
  }

  .mgmt-toolbar-btn.is-outline {
    background: rgba(255, 255, 255, 0.02) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    color: #94a3b8 !important;
  }

  .mgmt-toolbar-btn.is-outline .q-btn__content {
    font-weight: 600;
    text-transform: none;
  }

  .mgmt-toolbar-btn.is-outline:hover {
    color: #ffffff !important;
    background: rgba(255, 255, 255, 0.05) !important;
  }

  .mgmt-toolbar-btn.is-outline.is-danger,
  .mgmt-toolbar-btn.is-outline.is-danger .q-btn__content {
    color: var(--vpn-danger) !important;
  }

  .mgmt-toolbar-btn.is-outline.is-danger {
    border-color: rgba(251, 113, 133, 0.45) !important;
  }

  .mgmt-toolbar-btn.is-outline.is-danger:hover,
  .mgmt-toolbar-btn.is-outline.is-danger:hover .q-btn__content {
    color: #fecaca !important;
    background: rgba(251, 113, 133, 0.12) !important;
    border-color: var(--vpn-danger) !important;
  }

  .mgmt-search-input {
    min-width: 200px;
    max-width: 280px;
  }

  .mgmt-search-btn {
    min-width: 72px;
  }

  .mgmt-list-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
    padding-bottom: 16px;
    margin-bottom: 18px;
    border-bottom: 1px solid rgba(31, 41, 55, 0.95);
    flex-shrink: 0;
  }

  .mgmt-kicker {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #6b7280;
  }

  /* 列表/分块内二级标题与说明（如「规则顺序」；大于 kicker，各管理页可复用） */
  .mgmt-section-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--vpn-text-heading);
  }

  .mgmt-section-sub {
    color: #6b7280;
    font-size: 13px;
  }

  .mgmt-page-foot {
    flex-shrink: 0;
    padding-top: 12px;
    margin-top: auto;
    width: 100%;
  }

  .mgmt-page-footer-row {
    /* 与 t.html .footer-row 同构 */
    padding: 12px 0;
    opacity: 0.5;
    font-size: 11px;
    font-weight: 700;
  }

  /* 对端站点页：行高、标签与操作条（顶栏与组管理页同构，无独立 peers-header） */

  .mgmt-record-card.mgmt-peer-row {
    min-height: 80px;
    padding: 0 24px;
    border-left: 4px solid #334155;
    border-radius: 8px;
    box-shadow: none;
  }

  .mgmt-record-card.mgmt-peer-row:hover {
    border-left-color: #2dd4bf;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    background: rgba(45, 212, 191, 0.02);
  }

  .mgmt-peer-avatar {
    width: 40px;
    height: 40px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    background: rgba(30, 41, 59, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.05);
    color: #94a3b8;
  }

  .mgmt-peer-name {
    font-size: 16px;
    font-weight: 700;
    color: #f8fafc;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .mgmt-peer-title-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    min-width: 0;
  }

  .mgmt-peer-meta-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
  }

  .mgmt-peer-meta-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 500;
    color: #64748b;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.05);
    white-space: nowrap;
    max-width: 100%;
  }

  .mgmt-peer-meta-tag .q-icon {
    font-size: 14px !important;
    opacity: 0.85;
  }

  .mgmt-peer-actions-rail {
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 0;
    border-radius: 0;
    background: transparent;
    border: none;
    flex-shrink: 0;
  }

  .mgmt-peer-action-sep {
    width: 1px;
    height: 16px;
    margin: 0 4px;
    background: rgba(255, 255, 255, 0.1);
    flex-shrink: 0;
  }

  /* 对端帮助侧栏：快捷跳转卡片按钮 */
  .peers-help-panel .peers-help-jump-label {
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #64748b;
    margin-top: 20px;
    margin-bottom: 2px;
  }

  .peers-help-jump-btn {
    width: 100% !important;
    min-height: 52px !important;
    padding: 0 16px !important;
    border-radius: 12px !important;
    justify-content: space-between !important;
    background: rgba(255, 255, 255, 0.04) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    color: #e2e8f0 !important;
    transition: border-color 0.2s ease, background 0.2s ease, transform 0.15s ease !important;
  }

  .peers-help-jump-btn .q-btn__content {
    width: 100%;
    justify-content: space-between !important;
    font-weight: 600;
    font-size: 14px;
  }

  .peers-help-jump-btn:hover {
    border-color: rgba(45, 212, 191, 0.45) !important;
    background: rgba(45, 212, 191, 0.08) !important;
    color: #f8fafc !important;
    transform: translateY(-1px);
  }

  .peers-help-jump-btn .q-icon {
    font-size: 22px !important;
    opacity: 0.85;
  }

  .mgmt-stretch {
    width: 100%;
    flex: 1 1 0%;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .mgmt-record-list {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    gap: 12px;
    width: 100%;
    min-height: 0;
  }

  .mgmt-record-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: 100%;
    padding: 16px 20px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    background: rgba(255, 255, 255, 0.02);
    transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease;
  }

  .mgmt-record-card:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(45, 212, 191, 0.45);
    transform: translateY(-1px);
  }

  .mgmt-record-card.is-muted {
    opacity: 0.6;
  }

  .mgmt-record-main {
    display: flex;
    align-items: center;
    gap: 24px;
    min-width: 0;
    flex: 1 1 auto;
  }

  .mgmt-record-copy {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .mgmt-record-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--vpn-text-heading);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .mgmt-record-title.is-muted {
    text-decoration: line-through;
    opacity: 0.5;
  }

  .mgmt-record-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    color: #64748b;
    font-size: 12px;
  }

  .mgmt-meta-item {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    white-space: nowrap;
  }

  /* 用户卡片：元信息分两行，避免宽屏下两行并排 */
  .user-card-meta {
    flex-direction: column !important;
    align-items: flex-start !important;
    gap: 8px !important;
    width: 100%;
  }

  .user-meta-session-line {
    width: 100%;
  }

  .mgmt-record-side {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
  }

  .mgmt-actions {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
  }

  .mgmt-icon-btn {
    color: #64748b !important;
    transition: color 0.2s;
  }

  .mgmt-icon-btn:hover { color: #94a3b8 !important; }
  .mgmt-icon-btn.is-link { color: #2dd4bf !important; }
  .mgmt-icon-btn.is-link:hover { color: #5eead4 !important; }
  .mgmt-icon-btn.is-delete { color: #f87171 !important; }
  .mgmt-icon-btn.is-delete:hover { color: #fb7185 !important; }
  .mgmt-icon-btn.is-send { color: #f59e0b !important; }
  .mgmt-icon-btn.is-send:hover { color: #fbbf24 !important; }
  .mgmt-icon-btn.is-renew { color: #2dd4bf !important; }
  .mgmt-icon-btn.is-renew:hover { color: #5eead4 !important; }
  .mgmt-icon-btn.is-revoke { color: #f87171 !important; }
  .mgmt-icon-btn.is-revoke:hover { color: #fb7185 !important; }
  .mgmt-icon-btn.is-neutral { color: #94a3b8 !important; }
  .mgmt-icon-btn.is-neutral:hover { color: #cbd5e1 !important; }
  .mgmt-icon-btn.is-restart { color: #f59e0b !important; }
  .mgmt-icon-btn.is-restart:hover { color: #fbbf24 !important; }
  .mgmt-icon-btn.is-stop { color: #f87171 !important; }
  .mgmt-icon-btn.is-stop:hover { color: #fb7185 !important; }
  .mgmt-icon-btn.is-start { color: #22c55e !important; }
  .mgmt-icon-btn.is-start:hover { color: #4ade80 !important; }

  .mgmt-checkbox .q-checkbox__inner {
    color: #475569 !important;
  }

  .mgmt-checkbox .q-checkbox__inner--truthy {
    color: #2dd4bf !important;
  }

  /* ========= 暗色弹窗 / 下拉框 / 选择器 ========= */

  .q-dialog .q-card,
  .q-menu,
  .q-select__dialog {
    background: #1e293b !important;
    color: var(--vpn-text) !important;
    border: 1px solid rgba(148, 163, 184, 0.12);
  }

  .q-item {
    color: var(--vpn-text) !important;
  }

  .q-item--active,
  .q-item.q-router-link--active {
    color: #2dd4bf !important;
  }

  .q-field__control,
  .q-field--outlined .q-field__control {
    min-height: 46px;
    border-radius: 10px !important;
    background: rgba(15, 23, 42, 0.46) !important;
    color: #f8fafc !important;
  }

  .q-field__native,
  .q-field__input {
    color: #f8fafc !important;
  }

  .q-field__label,
  .q-placeholder {
    color: var(--vpn-text-soft) !important;
  }

  .q-field--outlined .q-field__control:before {
    border-color: rgba(148, 163, 184, 0.16) !important;
  }

  .q-field--outlined.q-field--focused .q-field__control:before {
    border-color: #2dd4bf !important;
  }

  .q-field--float .q-field__label {
    color: #94a3b8 !important;
  }

  .q-select .q-field__native span {
    color: #f8fafc !important;
  }

  .q-chip {
    background: rgba(45, 212, 191, 0.15) !important;
    color: #2dd4bf !important;
  }

  /* ui.switch / QToggle：显式圆形滑块与胶囊轨道（避免被其它 border-radius 规则压成方形） */
  .q-toggle__track {
    border-radius: 999px !important;
  }

  .q-toggle__thumb {
    border-radius: 50% !important;
  }

  .q-toggle__inner--truthy .q-toggle__thumb {
    background: #2dd4bf !important;
  }

  .q-toggle__inner--truthy .q-toggle__track {
    background: rgba(45, 212, 191, 0.3) !important;
  }

  .q-toggle__inner:not(.q-toggle__inner--truthy) .q-toggle__thumb {
    background: #64748b !important;
  }

  .q-toggle__inner:not(.q-toggle__inner--truthy) .q-toggle__track {
    background: rgba(148, 163, 184, 0.22) !important;
  }

  /* Quasar btn-toggle 暗色 */
  .q-btn-toggle {
    background: rgba(15, 23, 42, 0.46) !important;
    border: 1px solid rgba(148, 163, 184, 0.16) !important;
    border-radius: 10px !important;
  }

  .q-btn-toggle .q-btn {
    color: #94a3b8 !important;
  }

  .q-btn-toggle .q-btn--active {
    background: rgba(45, 212, 191, 0.15) !important;
    color: #2dd4bf !important;
  }

  /* textarea 暗色 */
  .q-field--outlined textarea {
    color: #f8fafc !important;
  }

  /* 弹窗内 q-uploader：列表区默认白底导致暗色主题下文字不可见 */
  .q-dialog .q-uploader {
    background: rgba(15, 23, 42, 0.55) !important;
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 12px;
    color: var(--vpn-text) !important;
  }

  .q-dialog .q-uploader__header {
    background: rgba(79, 143, 199, 0.32) !important;
    color: var(--vpn-text-heading) !important;
    border-bottom: 1px solid rgba(148, 163, 184, 0.14);
  }

  .q-dialog .q-uploader__header-content {
    color: var(--vpn-text-heading) !important;
  }

  .q-dialog .q-uploader__subtitle {
    color: var(--vpn-text-muted) !important;
    opacity: 1 !important;
  }

  .q-dialog .q-uploader__list {
    background: rgba(15, 23, 42, 0.72) !important;
    color: var(--vpn-text) !important;
    /* 默认空列表过高易显大块留白，压缩后仍可容纳一条文件信息 */
    min-height: 56px !important;
  }

  .q-dialog .q-uploader__list .q-item,
  .q-dialog .q-uploader__list .q-item__section {
    color: var(--vpn-text) !important;
    background: transparent !important;
  }

  .q-dialog .q-uploader__list .q-item__label {
    color: var(--vpn-text-heading) !important;
  }

  .q-dialog .q-uploader__list .q-item__label--caption {
    color: var(--vpn-text-muted) !important;
  }

  .q-dialog .q-uploader__file {
    background: rgba(30, 41, 59, 0.75) !important;
    color: var(--vpn-text) !important;
    border: 1px solid rgba(148, 163, 184, 0.12) !important;
  }

  .q-dialog .q-uploader__file .q-spinner {
    color: var(--vpn-accent-soft) !important;
  }

  .q-dialog .q-uploader__title {
    color: var(--vpn-text-heading) !important;
  }

  .q-dialog .q-uploader .q-btn {
    color: var(--vpn-text-heading) !important;
  }

  .q-dialog .q-uploader .q-btn .q-icon {
    color: inherit !important;
  }

  .q-dialog .q-uploader .q-linear-progress {
    color: var(--vpn-accent) !important;
  }

  .q-dialog .q-uploader .q-linear-progress__track {
    background: rgba(148, 163, 184, 0.15) !important;
  }

  /* 防火墙规则流向文本 */
  .mgmt-meta-flow {
    color: #94a3b8;
    font-family: "Cascadia Code", "Fira Mono", "Consolas", monospace;
    font-size: 11px;
    letter-spacing: 0.02em;
  }

  /* 修复 flat/round 按钮白色闪烁 */
  .q-btn--flat .q-focus-helper,
  .q-btn--round .q-focus-helper,
  .mgmt-icon-btn .q-focus-helper {
    display: none !important;
  }

  .q-btn--flat::before,
  .q-btn--round::before,
  .mgmt-icon-btn::before {
    display: none !important;
    box-shadow: none !important;
  }

  .q-btn--flat .q-ripple,
  .q-btn--round .q-ripple,
  .mgmt-icon-btn .q-ripple {
    display: none !important;
  }

  /* 修复 Quasar 深色下 separator 颜色 */
  .q-separator {
    background: rgba(148, 163, 184, 0.12) !important;
  }

  /* 对端部署说明侧栏：精致工具文档，降低 Markdown 默认字号与标题冲击 */
  .peer-manual-drawer {
    display: flex;
    flex-direction: column;
    padding: 0 !important;
    overflow: hidden;
    background:
      linear-gradient(180deg, rgba(30, 41, 59, 0.98), rgba(15, 23, 42, 0.98)) !important;
  }

  .peer-manual-drawer-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 18px 20px 14px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.12);
    background: rgba(15, 23, 42, 0.36);
  }

  .peer-manual-title {
    color: var(--vpn-text-heading);
    font-size: 17px;
    font-weight: 750;
    letter-spacing: -0.01em;
    line-height: 1.25;
  }

  .peer-manual-subtitle {
    color: var(--vpn-text-muted);
    font-size: 12px;
    line-height: 1.35;
    margin-top: 3px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .peer-manual-hint {
    margin: 12px 20px 0;
    padding: 10px 12px;
    border-radius: 12px;
    border: 1px solid rgba(45, 212, 191, 0.14);
    background: rgba(45, 212, 191, 0.055);
    color: var(--vpn-text-muted);
    font-size: 12px;
    line-height: 1.45;
  }

  .peer-manual-preview {
    margin: 12px 20px 0;
    padding: 18px 20px !important;
    background: rgba(10, 18, 32, 0.62) !important;
    border: 1px solid rgba(148, 163, 184, 0.12);
    border-radius: 14px;
    color: var(--vpn-text) !important;
    min-height: 0;
  }

  .peer-manual-preview .nicegui-markdown {
    color: var(--vpn-text) !important;
    font-size: 13px;
    line-height: 1.58;
  }

  .peer-manual-preview .nicegui-markdown :is(h1, h2, h3, h4, h5, h6) {
    color: var(--vpn-text-heading) !important;
    margin-top: 1.05em;
    margin-bottom: 0.45em;
    line-height: 1.25;
    letter-spacing: -0.01em;
  }

  .peer-manual-preview .nicegui-markdown h1 {
    font-size: 20px;
    margin-top: 0;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.14);
  }

  .peer-manual-preview .nicegui-markdown h2 {
    font-size: 15px;
  }

  .peer-manual-preview .nicegui-markdown h3 {
    font-size: 14px;
  }

  .peer-manual-preview .nicegui-markdown :is(p, ul, ol, li, table) {
    color: var(--vpn-text) !important;
  }

  .peer-manual-preview .nicegui-markdown p {
    margin: 0.45em 0;
  }

  .peer-manual-preview .nicegui-markdown :is(ul, ol) {
    margin: 0.45em 0 0.75em;
    padding-left: 1.25rem;
  }

  .peer-manual-preview .nicegui-markdown li {
    margin: 0.22em 0;
  }

  .peer-manual-preview .nicegui-markdown strong {
    color: var(--vpn-text-heading) !important;
  }

  .peer-manual-preview .nicegui-markdown a {
    color: var(--vpn-accent-soft) !important;
  }

  .peer-manual-preview .nicegui-markdown hr {
    border: none;
    border-top: 1px solid rgba(148, 163, 184, 0.2);
    margin: 1em 0;
  }

  .peer-manual-preview .nicegui-markdown blockquote {
    border-left: 3px solid var(--vpn-accent);
    margin: 0.6em 0;
    padding-left: 12px;
    color: var(--vpn-text-muted) !important;
  }

  .peer-manual-preview .nicegui-markdown :not(pre) > code {
    background: rgba(148, 163, 184, 0.12) !important;
    color: #e2e8f0 !important;
    padding: 0.1em 0.35em;
    border-radius: 6px;
    font-size: 0.9em;
    border: 1px solid rgba(148, 163, 184, 0.12);
  }

  .peer-manual-preview .nicegui-markdown pre,
  .peer-manual-preview .nicegui-markdown .codehilite {
    background: rgba(15, 23, 42, 0.98) !important;
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 10px;
    padding: 10px 12px;
    overflow-x: auto;
    color: #e2e8f0 !important;
    font-size: 12px;
    line-height: 1.5;
  }

  .peer-manual-preview .nicegui-markdown pre code {
    background: transparent !important;
    padding: 0;
    color: inherit !important;
  }

  .peer-manual-preview .nicegui-markdown table {
    border-collapse: collapse;
    width: 100%;
    margin: 0.75em 0;
  }

  .peer-manual-preview .nicegui-markdown th,
  .peer-manual-preview .nicegui-markdown td {
    border: 1px solid rgba(148, 163, 184, 0.2);
    padding: 7px 9px;
    color: var(--vpn-text) !important;
    font-size: 12px;
  }

  .peer-manual-preview .nicegui-markdown th {
    background: rgba(148, 163, 184, 0.08);
    color: var(--vpn-text-heading) !important;
  }

  .peer-manual-drawer-foot {
    width: 100%;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 20px 16px;
    border-top: 1px solid rgba(148, 163, 184, 0.1);
    background: rgba(15, 23, 42, 0.5);
    flex-shrink: 0;
  }

  .peer-manual-page {
    gap: 18px;
  }

  .peer-manual-hero,
  .peer-manual-summary-card,
  .peer-manual-step-card,
  .peer-manual-empty {
    border: 1px solid rgba(148, 163, 184, 0.14);
    background:
      linear-gradient(180deg, rgba(24, 35, 56, 0.94), rgba(15, 23, 42, 0.94));
    border-radius: 18px;
    box-shadow: var(--vpn-shadow-soft);
  }

  .peer-manual-hero {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 18px;
    padding: 22px;
  }

  .peer-manual-hero-copy {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .peer-manual-hero-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: wrap;
  }

  .peer-manual-page-title {
    color: var(--vpn-text-heading);
    font-size: 24px;
    font-weight: 760;
    letter-spacing: -0.02em;
    line-height: 1.22;
  }

  .peer-manual-page-subtitle {
    color: var(--vpn-text-muted);
    font-size: 13px;
    line-height: 1.55;
  }

  .peer-manual-chip-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 6px;
  }

  .peer-manual-chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    max-width: 100%;
    padding: 6px 10px;
    border-radius: 999px;
    border: 1px solid rgba(94, 169, 255, 0.16);
    background: rgba(94, 169, 255, 0.08);
    color: var(--vpn-text);
  }

  .peer-manual-chip-label {
    color: var(--vpn-text-soft);
    font-size: 11px;
  }

  .peer-manual-chip-value {
    color: var(--vpn-text-heading);
    font-size: 12px;
    font-weight: 650;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .peer-manual-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(280px, 0.76fr);
    gap: 16px;
  }

  .peer-manual-summary-card {
    padding: 18px;
  }

  .peer-manual-section-title {
    color: var(--vpn-text-heading);
    font-size: 15px;
    font-weight: 720;
    letter-spacing: -0.01em;
    margin-bottom: 12px;
  }

  .peer-manual-note {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 9px 0;
    color: var(--vpn-text-muted);
    border-top: 1px solid rgba(148, 163, 184, 0.08);
  }

  .peer-manual-note:first-of-type {
    border-top: 0;
    padding-top: 0;
  }

  .peer-manual-note-text {
    font-size: 13px;
    line-height: 1.5;
  }

  .peer-manual-meta-row {
    display: grid;
    grid-template-columns: 92px minmax(0, 1fr);
    gap: 10px;
    padding: 8px 0;
    border-top: 1px solid rgba(148, 163, 184, 0.08);
  }

  .peer-manual-meta-row:first-of-type {
    border-top: 0;
    padding-top: 0;
  }

  .peer-manual-meta-label {
    color: var(--vpn-text-soft);
    font-size: 12px;
  }

  .peer-manual-meta-value {
    color: var(--vpn-text-heading);
    font-size: 12px;
    line-height: 1.45;
    word-break: break-all;
  }

  .peer-manual-steps {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .peer-manual-step-card {
    padding: 16px;
  }

  .peer-manual-step-head {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 12px;
  }

  .peer-manual-step-index {
    width: 28px;
    height: 28px;
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 10px;
    color: #dbeafe;
    background: rgba(94, 169, 255, 0.12);
    border: 1px solid rgba(94, 169, 255, 0.18);
    font-size: 13px;
    font-weight: 760;
  }

  .peer-manual-step-title {
    color: var(--vpn-text-heading);
    font-size: 15px;
    font-weight: 720;
    line-height: 1.35;
  }

  .peer-manual-step-summary {
    color: var(--vpn-text-muted);
    font-size: 12px;
    line-height: 1.5;
    margin-top: 2px;
  }

  .peer-manual-command {
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 14px;
    overflow: hidden;
    background: rgba(6, 12, 24, 0.72);
  }

  .peer-manual-command-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 8px 10px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.1);
    background: rgba(15, 23, 42, 0.78);
  }

  .peer-manual-command-lang {
    color: var(--vpn-text-soft);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .peer-manual-copy-btn {
    color: #dbeafe !important;
    border-radius: 8px;
  }

  .peer-manual-command-code {
    display: block;
  }

  .peer-manual-command-code pre {
    margin: 0;
    padding: 13px 14px;
    overflow-x: auto;
    color: #e2e8f0;
    font-size: 12px;
    line-height: 1.55;
    font-family: "Cascadia Mono", "Consolas", "SFMono-Regular", monospace;
    white-space: pre;
  }

  .peer-manual-command-code code {
    color: inherit;
    font-family: inherit;
  }

  .peer-manual-empty {
    min-height: 280px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    text-align: center;
    padding: 36px 18px;
  }

  @media (max-width: 900px) {
    .peer-manual-hero {
      flex-direction: column;
    }

    .peer-manual-hero-actions {
      width: 100%;
      justify-content: flex-start;
    }

    .peer-manual-grid {
      grid-template-columns: 1fr;
    }
  }

  .peer-install-log-frame {
    min-height: 150px;
    max-height: 240px;
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, 0.14);
    background: rgba(10, 18, 32, 0.74);
    overflow: hidden;
  }

  .peer-install-log-viewport {
    width: 100%;
    height: 190px;
    overflow: auto;
    padding: 10px 12px;
    color: #cbd5e1;
    font-family: "Cascadia Code", "Fira Mono", "Consolas", monospace;
    font-size: 11px;
    line-height: 1.5;
    white-space: pre-wrap;
  }

  .peer-remote-log-drawer {
    display: flex;
    flex-direction: column;
    padding: 0 !important;
    overflow: hidden;
    background:
      linear-gradient(180deg, rgba(30, 41, 59, 0.98), rgba(15, 23, 42, 0.98)) !important;
  }

  .peer-remote-log-frame {
    margin: 12px 20px 0;
    flex: 1 1 auto;
    min-height: 0;
    border-radius: 14px;
    border: 1px solid rgba(148, 163, 184, 0.14);
    background: rgba(10, 18, 32, 0.74);
    overflow: auto;
  }

  .peer-remote-log-viewport {
    width: 100%;
    height: 100%;
    max-height: 100%;
    min-height: 0;
    overflow: auto;
    padding: 12px 14px;
    color: #cbd5e1;
    font-family: "Cascadia Code", "Fira Mono", "Consolas", monospace;
    font-size: 11px;
    line-height: 1.55;
    white-space: pre-wrap;
  }

  .peer-remote-log-frame::-webkit-scrollbar,
  .peer-remote-log-viewport::-webkit-scrollbar {
    width: 10px;
    height: 10px;
  }

  .peer-remote-log-frame::-webkit-scrollbar-thumb,
  .peer-remote-log-viewport::-webkit-scrollbar-thumb {
    border-radius: 999px;
    background: rgba(148, 163, 184, 0.38);
    border: 2px solid rgba(10, 18, 32, 0.74);
  }
</style>
"""


def setup_theme():
    ui.add_head_html(_THEME_STYLE)
