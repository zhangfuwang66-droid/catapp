'use strict';

// 在样式加载前恢复主题；这里只保存外观，不保存账号、密码或会话。
(() => {
  let saved;
  try { saved = localStorage.getItem('admin-theme'); } catch {}
  const preferred = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = ['light', 'dark'].includes(saved) ? saved : preferred;
})();

document.addEventListener('DOMContentLoaded', () => {
  const byId = id => document.getElementById(id);
  const form = byId('adminLoginForm'), username = byId('adminUsername'), password = byId('adminPassword');
  const submit = byId('adminLoginSubmit'), label = byId('loginSubmitLabel');
  const notice = byId('loginNotice'), errorBox = byId('loginError'), status = byId('loginStatus');
  let submitting = false, leaving = false;
  requestAnimationFrame(() => document.documentElement.classList.add('theme-ready'));

  function updateThemeButton() {
    const dark = document.documentElement.dataset.theme === 'dark';
    byId('loginTheme').setAttribute('aria-label', dark ? '切换为浅色主题' : '切换为深色主题');
    byId('loginTheme').setAttribute('aria-pressed', String(dark));
  }
  updateThemeButton();
  byId('loginTheme').addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('admin-theme', theme); } catch {}
    updateThemeButton();
  });
  window.addEventListener('storage', event => {
    if (event.key === 'admin-theme' && ['light', 'dark'].includes(event.newValue)) {
      document.documentElement.dataset.theme = event.newValue;
      updateThemeButton();
    }
  });

  const reason = new URLSearchParams(location.search).get('notice');
  if (['expired', 'login', 'logout'].includes(reason)) {
    notice.textContent = reason === 'logout' ? '已安全退出管理后台。' : '登录状态已失效，请重新验证管理员身份。';
    notice.hidden = false;
  }
  byId('passwordToggle').addEventListener('click', () => {
    const visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    byId('passwordToggle').setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    byId('passwordToggle').setAttribute('aria-pressed', String(visible));
  });
  for (const eventName of ['keydown', 'keyup']) password.addEventListener(eventName, event => {
    byId('capsLock').hidden = !event.getModifierState?.('CapsLock');
  });
  password.addEventListener('blur', () => { byId('capsLock').hidden = true; });
  const help = byId('loginHelpDialog');
  byId('loginHelp').addEventListener('click', () => help.showModal());
  byId('closeLoginHelp').addEventListener('click', () => help.close());
  help.addEventListener('click', event => { if (event.target === help) {
    const box = help.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) help.close();
  }});

  function busy(value) {
    submitting = value;
    submit.disabled = value;
    username.disabled = password.disabled = value;
    byId('passwordToggle').disabled = value;
    submit.setAttribute('aria-busy', String(value));
    form.setAttribute('aria-busy', String(value));
    label.textContent = value ? '正在验证身份' : '进入管理后台';
  }
  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    password.setAttribute('aria-invalid', 'true');
    errorBox.focus({preventScroll: true});
  }
  form.addEventListener('input', () => {
    errorBox.hidden = true;
    password.removeAttribute('aria-invalid');
    status.textContent = '';
  });

  async function checkSession() {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 7000);
    try {
      const response = await fetch('/api/admin/session', {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      if (response.ok && !submitting && !leaving) {
        leaving = true;
        password.value = '';
        status.textContent = '管理员身份已验证，正在进入…';
        location.replace('/admin');
      }
    } catch { /* 预检不阻塞表单；提交时给出具体连接反馈。 */ }
    finally { clearTimeout(timeout); }
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submitting || leaving || !form.reportValidity()) return;
    const body = {username: username.value.trim(), password: password.value};
    errorBox.hidden = true; notice.hidden = true;
    password.removeAttribute('aria-invalid');
    busy(true); status.textContent = '正在建立管理会话…';
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/api/admin/login', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, body: JSON.stringify(body), signal: controller.signal});
      if (!response.ok) {
        const messages = {401: '账号或密码不正确，请核对后重试。', 403: '此账号没有管理员权限，请使用已授权的管理员账号。',
          404: '此服务尚未开放管理后台，请联系维护者。', 422: '账号或密码格式不正确，请核对输入。',
          429: '尝试次数较多，请稍后再试。'};
        throw new Error(messages[response.status] || '暂时无法建立管理会话，请稍后重试。');
      }
      // 只前往固定管理入口，不接受 URL 或响应传入的外部跳转地址。
      leaving = true; password.value = ''; label.textContent = '验证成功';
      status.textContent = '正在进入管理工作区…';
      location.assign('/admin');
    } catch (error) {
      status.textContent = '';
      busy(false);
      showError(error.name === 'AbortError' ? '连接超时，请检查网络后重试。' : error instanceof TypeError ? '暂时无法连接服务器，请检查网络后重试。' : error.message);
    } finally { clearTimeout(timeout); }
  });
  // 浏览器后退缓存不得恢复密码或停留在已提交的表单状态。
  window.addEventListener('pagehide', () => { password.value = ''; });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { leaving = false; password.value = ''; busy(false); checkSession(); }
  });
  checkSession();
});
