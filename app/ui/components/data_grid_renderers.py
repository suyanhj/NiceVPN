# -*- coding: utf-8 -*-
"""AG Grid 共享展示型 cell renderer。

仅负责视觉表达，不承载业务请求或状态变更。
"""

STATUS_PILL_RENDERER = r"""(params) => {
  const field = String(params.colDef?.field ?? '');
  const fieldTone = field ? params.data?.[field + '_tone'] : null;
  const rawTone = String(fieldTone ?? params.data?.status_tone ?? 'unknown');
  const tone = ['success', 'warning', 'failed'].includes(rawTone) ? rawTone : 'unknown';

  const pill = document.createElement('span');
  pill.className = 'vpn-grid-status-pill vpn-grid-status-pill--' + tone;

  const dot = document.createElement('span');
  dot.className = 'vpn-grid-status-pill__dot';

  const text = document.createElement('span');
  text.textContent = String(params.value ?? params.data?.status_label ?? '未知');

  pill.append(dot, text);
  return pill;
}"""

EXPIRY_WITH_DETAIL_RENDERER = r"""(params) => {
  const root = document.createElement('div');
  root.className = 'vpn-grid-stacked-value';

  const primary = document.createElement('div');
  primary.className = 'vpn-grid-stacked-value__primary';
  primary.textContent = String(params.value ?? params.data?.expires_display ?? '—');

  const secondary = document.createElement('div');
  secondary.className = 'vpn-grid-stacked-value__secondary';

  const days = params.data?.days_left;
  if (days === null || days === undefined) {
    secondary.textContent = '有效期未知';
  } else if (Number(days) < 0) {
    secondary.classList.add('vpn-grid-stacked-value__secondary--danger');
    secondary.textContent = '已过期 ' + Math.abs(Number(days)) + ' 天';
  } else {
    if (String(params.data?.status) === 'expiring') {
      secondary.classList.add('vpn-grid-stacked-value__secondary--warning');
    }
    secondary.textContent = '剩余 ' + Number(days) + ' 天';
  }

  root.append(primary, secondary);
  return root;
}"""

ACTION_BUTTON_RENDERER = r"""(params) => {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'vpn-grid-inline-action';
  button.textContent = String(params.value ?? '操作');
  button.title = '打开操作菜单';
  return button;
}"""
