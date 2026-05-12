const express = require('express');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { simpleParser } = require('mailparser');
const nodemailer = require('nodemailer');
const MailClient = require('./client');
const { fromPreset, PRESETS, DOMAIN_MAP, autoDetect } = require('./config');
const { prepareHtmlForRender } = require('./sanitize');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// 存储已连接的客户端
const clients = new Map();
let clientId = 0;

// 持久化文件路径
const ACCOUNTS_FILE = process.env.ACCOUNTS_FILE || path.join(__dirname, 'accounts.json');
const SETTINGS_FILE = process.env.SETTINGS_FILE || path.join(path.dirname(ACCOUNTS_FILE), 'settings.json');
const ACCOUNTS_SECRET = process.env.IMAP_ACCOUNTS_SECRET || process.env.SECRET_KEY || process.env.APP_SECRET || '';
const GOOGLE_SCOPE = 'https://mail.google.com/';
const oauthStates = new Map();
let runtimeSettings = {};
if (!ACCOUNTS_SECRET) {
  console.warn('WARNING: IMAP_ACCOUNTS_SECRET/APP_SECRET is not configured; external IMAP accounts cannot be persisted.');
}

function deriveAccountsKey() {
  if (!ACCOUNTS_SECRET) return null;
  return crypto.createHash('sha256').update(ACCOUNTS_SECRET).digest();
}

function encryptSecret(value) {
  const key = deriveAccountsKey();
  if (!key || !value) return null;
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(value, 'utf8'), cipher.final()]);
  return {
    v: 1,
    alg: 'aes-256-gcm',
    iv: iv.toString('base64'),
    tag: cipher.getAuthTag().toString('base64'),
    data: ciphertext.toString('base64'),
  };
}

function decryptSecret(encrypted) {
  const key = deriveAccountsKey();
  if (!key || !encrypted || encrypted.alg !== 'aes-256-gcm') return '';
  try {
    const decipher = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(encrypted.iv, 'base64'));
    decipher.setAuthTag(Buffer.from(encrypted.tag, 'base64'));
    const plaintext = Buffer.concat([
      decipher.update(Buffer.from(encrypted.data, 'base64')),
      decipher.final(),
    ]);
    return plaintext.toString('utf8');
  } catch {
    return '';
  }
}

function normalizePublicBaseUrl(value) {
  return (value || '').trim().replace(/\/+$/, '');
}

function getPublicBaseUrl() {
  return normalizePublicBaseUrl(runtimeSettings.public_base_url || process.env.PUBLIC_BASE_URL || '');
}

function getGoogleClientId() {
  return (runtimeSettings.google_client_id || process.env.GOOGLE_CLIENT_ID || '').trim();
}

function getGoogleClientSecret() {
  return (runtimeSettings.google_client_secret || process.env.GOOGLE_CLIENT_SECRET || '').trim();
}

function getGoogleRedirectUri() {
  const explicit = (runtimeSettings.google_redirect_uri || process.env.GOOGLE_REDIRECT_URI || '').trim();
  if (explicit) return explicit;
  const publicBaseUrl = getPublicBaseUrl();
  return publicBaseUrl ? `${publicBaseUrl}/api/oauth/gmail/callback` : '';
}

function getGoogleOAuthSettings() {
  const publicBaseUrl = getPublicBaseUrl();
  const clientId = getGoogleClientId();
  const clientSecret = getGoogleClientSecret();
  const redirectUri = getGoogleRedirectUri();
  return {
    enabled: Boolean(clientId && clientSecret && redirectUri),
    public_base_url: publicBaseUrl,
    google_client_id: clientId,
    google_client_secret_configured: Boolean(clientSecret),
    google_redirect_uri: redirectUri,
    scope: GOOGLE_SCOPE,
  };
}

function loadSettings() {
  runtimeSettings = {};
  if (!fs.existsSync(SETTINGS_FILE)) return;
  try {
    const data = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf-8'));
    runtimeSettings = {
      public_base_url: normalizePublicBaseUrl(data.public_base_url || ''),
      google_client_id: (data.google_client_id || '').trim(),
      google_redirect_uri: (data.google_redirect_uri || '').trim(),
      google_client_secret: decryptSecret(data.google_client_secret_encrypted),
    };
  } catch (err) {
    console.warn(`读取 IMAP 运行配置失败: ${err.message}`);
    runtimeSettings = {};
  }
}

function saveSettings() {
  const dir = path.dirname(SETTINGS_FILE);
  fs.mkdirSync(dir, { recursive: true });
  const data = {
    public_base_url: runtimeSettings.public_base_url || '',
    google_client_id: runtimeSettings.google_client_id || '',
    google_redirect_uri: runtimeSettings.google_redirect_uri || '',
  };
  const encryptedSecret = encryptSecret(runtimeSettings.google_client_secret || '');
  if (encryptedSecret) data.google_client_secret_encrypted = encryptedSecret;
  const tmpPath = `${SETTINGS_FILE}.tmp`;
  fs.writeFileSync(tmpPath, JSON.stringify(data, null, 2), 'utf-8');
  fs.renameSync(tmpPath, SETTINGS_FILE);
}

function serializeAccount(account) {
  const safeAccount = {
    ...account,
    auth: { ...account.auth },
    oauth: account.oauth ? { ...account.oauth } : undefined,
  };
  const encryptedPass = encryptSecret(account.auth?.pass || '');
  const encryptedRefreshToken = encryptSecret(account.oauth?.refresh_token || '');
  if (!encryptedPass && !encryptedRefreshToken) return null;
  if (encryptedPass) {
    delete safeAccount.auth.pass;
    safeAccount.auth.pass_encrypted = encryptedPass;
  }
  if (safeAccount.oauth && encryptedRefreshToken) {
    delete safeAccount.oauth.refresh_token;
    delete safeAccount.oauth.access_token;
    delete safeAccount.oauth.expires_at;
    safeAccount.oauth.refresh_token_encrypted = encryptedRefreshToken;
  }
  return safeAccount;
}

function deserializeAccount(item) {
  const account = item.account || {};
  const auth = account.auth || {};
  const oauth = account.oauth || null;
  let pass = auth.pass || '';
  if (auth.pass_encrypted) {
    pass = decryptSecret(auth.pass_encrypted);
  }
  let refreshToken = oauth?.refresh_token || '';
  if (oauth?.refresh_token_encrypted) {
    refreshToken = decryptSecret(oauth.refresh_token_encrypted);
  }
  if (!pass && !refreshToken) return null;
  const { pass_encrypted: _passEncrypted, ...safeAuth } = auth;
  const { refresh_token_encrypted: _refreshTokenEncrypted, ...safeOauth } = oauth || {};
  return {
    ...account,
    auth: {
      ...safeAuth,
      ...(pass ? { pass } : {}),
    },
    ...(oauth ? { oauth: { ...safeOauth, refresh_token: refreshToken } } : {}),
  };
}

function createMailClient(account) {
  return new MailClient(account, { refreshOAuthToken });
}

function detectPreset(email) {
  const domain = String(email || '').split('@')[1]?.toLowerCase();
  return DOMAIN_MAP[domain] || '';
}

function normalizeDisplayName(value, fallback = '') {
  const trimmed = String(value || '').trim();
  return (trimmed || fallback).slice(0, 80);
}

function accountSummary(id, account, extra = {}) {
  const email = account?.auth?.user || '';
  return {
    id,
    name: account?.name || detectPreset(email) || 'custom',
    email,
    displayName: normalizeDisplayName(account?.displayName, email),
    smtp: account?.smtp ? {
      host: account.smtp.host,
      port: account.smtp.port,
      secure: account.smtp.secure,
      requireTLS: !!account.smtp.requireTLS,
    } : null,
    ...extra,
  };
}

function resultOk(protocol, config) {
  return {
    protocol,
    ok: true,
    host: config?.host || '',
    port: config?.port || '',
    secure: config?.secure !== false,
  };
}

function resultFail(protocol, config, err) {
  return {
    protocol,
    ok: false,
    host: config?.host || '',
    port: config?.port || '',
    secure: config?.secure !== false,
    error: err?.message || String(err || '连接失败'),
    response: err?.response || '',
  };
}

async function verifySmtp(account) {
  if (!account.smtp?.host) return null;
  const transport = nodemailer.createTransport({
    host: account.smtp.host,
    port: account.smtp.port || 465,
    secure: account.smtp.secure !== false,
    requireTLS: !!account.smtp.requireTLS,
    auth: account.auth?.pass ? account.auth : undefined,
    connectionTimeout: 12000,
    greetingTimeout: 12000,
    socketTimeout: 12000,
    logger: false,
  });
  try {
    await transport.verify();
    return resultOk('smtp', account.smtp);
  } catch (err) {
    return resultFail('smtp', account.smtp, err);
  } finally {
    transport.close();
  }
}

function buildDiagnosticMessage(imapResult, smtpResult, account, err) {
  const parts = [formatConnectionError(err, account)];
  if (imapResult) {
    parts.push(`IMAP 检查: ${imapResult.ok ? '正常' : '失败'} (${imapResult.host}:${imapResult.port})`);
  }
  if (smtpResult) {
    parts.push(`SMTP 检查: ${smtpResult.ok ? '正常' : '失败'} (${smtpResult.host}:${smtpResult.port})`);
    if (!imapResult?.ok && smtpResult.ok) {
      parts.push('SMTP 正常只能说明发信链路可用；收取邮件必须 IMAP 正常');
    }
    if (smtpResult.error && !smtpResult.ok) {
      parts.push(`SMTP 错误: ${smtpResult.error}`);
    }
  } else {
    parts.push('SMTP 检查: 当前预设没有 SMTP 配置');
  }
  return parts.join('。');
}

function formatConnectionError(err, account) {
  const raw = err?.message || String(err || '未知错误');
  const response = String(err?.response || '');
  const responseText = [raw, response].filter(Boolean).join(' ');
  const preset = String(account?.name || detectPreset(account?.auth?.user) || '').toLowerCase();
  const email = account?.auth?.user || '';
  const host = account?.host || '';
  const port = account?.port || '';
  const hints = [];

  if (preset === 'qq' || email.endsWith('@qq.com') || email.endsWith('@foxmail.com')) {
    hints.push('QQ 邮箱请确认已在 QQ 邮箱设置中开启 IMAP/SMTP 服务，并使用“授权码”而不是 QQ 登录密码');
    hints.push('如果刚开启 IMAP，请等待几分钟后重试；服务器应为 imap.qq.com:993');
  }
  if (preset === 'gmail') {
    hints.push('Gmail 推荐使用 OAuth2 登录；如果用密码方式，需要应用专用密码且账号已允许 IMAP');
  }
  if (preset === 'outlook') {
    hints.push('Outlook/Hotmail 请确认账号已开启 IMAP，并优先使用应用专用密码；企业账号还可能被管理员禁用 IMAP');
    hints.push('如果账号已启用微软安全默认值或组织禁用基础认证，密码/应用密码方式会失败，需要后续接入 Microsoft OAuth2');
  }

  if (/AUTHENTICATE failed|Authentication unsuccessful|LOGIN failed|Invalid credentials/i.test(responseText)) {
    hints.push('服务器明确拒绝认证，请重新生成授权码或应用专用密码后再试');
  } else if (/Command failed/i.test(raw)) {
    hints.push('IMAP 服务器拒绝了登录命令，通常是账号、授权码、IMAP 开关或邮箱类型选择不正确');
  }

  const detail = [`连接失败: ${raw}`, `邮箱类型: ${preset || 'auto'}`, `服务器: ${host}:${port}`];
  if (response && !raw.includes(response)) detail.push(`服务器返回: ${response}`);
  if (hints.length) detail.push(`提示: ${hints.join('；')}`);
  return detail.join('。');
}

function upsertClient(account) {
  for (const [existingId, existing] of clients) {
    if (existing.account.auth.user === account.auth.user) {
      existing.account = account;
      existing.client = existing.createClient();
      saveAccounts();
      return existingId;
    }
  }
  const id = ++clientId;
  clients.set(id, createMailClient(account));
  saveAccounts();
  return id;
}

function setClient(account, client) {
  for (const [existingId, existing] of clients) {
    if (existing.account.auth.user === account.auth.user) {
      existing.disconnect().catch(() => {});
      clients.set(existingId, client);
      saveAccounts();
      return existingId;
    }
  }
  const id = ++clientId;
  clients.set(id, client);
  saveAccounts();
  return id;
}

function hasGmailOAuthConfig() {
  return getGoogleOAuthSettings().enabled;
}

async function exchangeGoogleToken(params) {
  const resp = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(params),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.error_description || data.error || 'Google OAuth token exchange failed');
  }
  return data;
}

async function getGoogleUserInfo(accessToken) {
  const resp = await fetch('https://openidconnect.googleapis.com/v1/userinfo', {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.error_description || data.error || 'Failed to load Google user info');
  }
  return data;
}

async function refreshOAuthToken(account) {
  if (account.oauth?.provider !== 'gmail') {
    throw new Error('Unsupported OAuth provider');
  }
  if (account.oauth.access_token && account.oauth.expires_at && Date.now() < account.oauth.expires_at - 60000) {
    return { access_token: account.oauth.access_token };
  }
  const clientId = getGoogleClientId();
  const clientSecret = getGoogleClientSecret();
  const token = await exchangeGoogleToken({
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token: account.oauth.refresh_token,
    grant_type: 'refresh_token',
  });
  account.oauth.access_token = token.access_token;
  account.oauth.expires_at = Date.now() + ((token.expires_in || 3600) * 1000);
  saveAccounts();
  return token;
}

function buildGmailAccount(email, token) {
  return {
    name: 'gmail',
    host: PRESETS.gmail.host,
    port: PRESETS.gmail.port,
    secure: PRESETS.gmail.secure,
    auth: { user: email },
    oauth: {
      provider: 'gmail',
      refresh_token: token.refresh_token,
      access_token: token.access_token,
      expires_at: Date.now() + ((token.expires_in || 3600) * 1000),
      scope: token.scope || GOOGLE_SCOPE,
    },
  };
}

function requirePersistenceSecret(res) {
  if (ACCOUNTS_SECRET) return true;
  res.status(500).json({ error: '未配置 IMAP_ACCOUNTS_SECRET 或 APP_SECRET，无法持久化保存外部邮箱账号' });
  return false;
}

// 保存账户配置到文件
function saveAccounts() {
  const data = [];
  clients.forEach((client, id) => {
    const account = serializeAccount(client.account);
    if (account) data.push({ id, account });
  });
  fs.writeFileSync(ACCOUNTS_FILE, JSON.stringify(data, null, 2), 'utf-8');
}

function reorderAccounts(order) {
  const normalized = Array.isArray(order)
    ? order.map(id => parseInt(id, 10)).filter(id => Number.isInteger(id))
    : [];
  const next = new Map();
  for (const id of normalized) {
    if (clients.has(id)) next.set(id, clients.get(id));
  }
  clients.forEach((client, id) => {
    if (!next.has(id)) next.set(id, client);
  });
  clients.clear();
  next.forEach((client, id) => clients.set(id, client));
  saveAccounts();
}

// 启动时恢复已保存的账户
async function restoreAccounts() {
  if (!fs.existsSync(ACCOUNTS_FILE)) return;
  let data;
  try {
    data = JSON.parse(fs.readFileSync(ACCOUNTS_FILE, 'utf-8'));
  } catch {
    return;
  }
  if (!Array.isArray(data) || data.length === 0) return;

  console.log(`正在恢复 ${data.length} 个已保存的账户...`);
  for (const item of data) {
    const account = deserializeAccount(item);
    if (!account) {
      console.log('  ✗ 跳过账户：未配置 IMAP_ACCOUNTS_SECRET/APP_SECRET 或密码无法解密');
      continue;
    }
    const client = createMailClient(account);
    const id = ++clientId;
    clients.set(id, client);
    try {
      await client.connect();
      console.log(`  ✓ ${account.auth.user} 已恢复`);
    } catch (err) {
      console.log(`  ✗ ${account.auth.user} 暂未连接，已保留账号: ${err.message}`);
    }
  }
  // 重写持久化文件，迁移旧的明文格式；连接失败的账号仍保留。
  saveAccounts();
}

// 获取可用预设列表
app.get('/api/presets', (req, res) => {
  res.json(Object.keys(PRESETS));
});

app.get('/api/oauth/gmail/status', (req, res) => {
  const settings = getGoogleOAuthSettings();
  res.json({
    enabled: settings.enabled,
    redirectUri: settings.google_redirect_uri,
    scope: GOOGLE_SCOPE,
  });
});

app.get('/api/settings', (req, res) => {
  res.json({
    success: true,
    settings: getGoogleOAuthSettings(),
  });
});

app.post('/api/settings', (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  const data = req.body || {};
  runtimeSettings.public_base_url = normalizePublicBaseUrl(data.public_base_url || '');
  runtimeSettings.google_client_id = (data.google_client_id || '').trim();
  runtimeSettings.google_redirect_uri = (data.google_redirect_uri || '').trim();

  if (data.clear_google_client_secret) {
    runtimeSettings.google_client_secret = '';
  } else if (typeof data.google_client_secret === 'string' && data.google_client_secret.trim()) {
    runtimeSettings.google_client_secret = data.google_client_secret.trim();
  }

  try {
    saveSettings();
    res.json({
      success: true,
      settings: getGoogleOAuthSettings(),
    });
  } catch (err) {
    res.status(500).json({ success: false, error: `保存 IMAP 运行配置失败: ${err.message}` });
  }
});

app.post('/api/oauth/gmail/start', (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  if (!hasGmailOAuthConfig()) {
    return res.status(400).json({ error: 'Gmail OAuth 未配置，请在后台系统配置中填写 Google Client ID / Secret / Redirect URI' });
  }
  const clientId = getGoogleClientId();
  const redirectUri = getGoogleRedirectUri();
  const state = crypto.randomBytes(24).toString('hex');
  oauthStates.set(state, { createdAt: Date.now() });
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: 'code',
    scope: `${GOOGLE_SCOPE} openid email`,
    access_type: 'offline',
    prompt: 'consent',
    include_granted_scopes: 'true',
    state,
  });
  res.json({ url: `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}` });
});

app.get('/api/oauth/gmail/callback', async (req, res) => {
  if (!ACCOUNTS_SECRET) {
    return res.status(500).send('IMAP_ACCOUNTS_SECRET or APP_SECRET is not configured; external IMAP accounts cannot be persisted.');
  }
  if (!hasGmailOAuthConfig()) {
    return res.status(500).send('Gmail OAuth is not configured.');
  }
  const { code, state, error } = req.query;
  if (error) {
    return res.status(400).send(`Google OAuth failed: ${error}`);
  }
  const stateInfo = oauthStates.get(state);
  oauthStates.delete(state);
  if (!stateInfo || Date.now() - stateInfo.createdAt > 10 * 60 * 1000) {
    return res.status(400).send('Invalid or expired OAuth state');
  }

  try {
    const clientId = getGoogleClientId();
    const clientSecret = getGoogleClientSecret();
    const redirectUri = getGoogleRedirectUri();
    const token = await exchangeGoogleToken({
      client_id: clientId,
      client_secret: clientSecret,
      code,
      grant_type: 'authorization_code',
      redirect_uri: redirectUri,
    });
    if (!token.refresh_token) {
      throw new Error('Google did not return a refresh token. Revoke the app grant and try again, or keep prompt=consent.');
    }
    const profile = await getGoogleUserInfo(token.access_token);
    const email = (profile.email || '').toLowerCase();
    if (!email || profile.email_verified === false) {
      throw new Error('Google 未返回已验证邮箱地址');
    }
    const account = buildGmailAccount(email, token);
    account.displayName = normalizeDisplayName(profile.name || profile.email, email);
    const client = createMailClient(account);
    await client.connect();
    const id = setClient(account, client);
    res.send(`<!doctype html><meta charset="utf-8"><script>window.opener&&window.opener.postMessage({type:'gmail-oauth-success',id:${JSON.stringify(id)},email:${JSON.stringify(email)}},window.location.origin);window.close();</script><p>Gmail OAuth 登录成功，可以关闭此窗口。</p>`);
  } catch (err) {
    res.status(500).send(`Gmail OAuth failed: ${err.message}`);
  }
});

// 添加账户
app.post('/api/accounts', async (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  const { preset, host, port, email, password, displayName } = req.body;
  if (!email || !password) {
    return res.status(400).json({ error: '请填写邮箱和密码' });
  }

  let account;
  if (!preset || preset === 'auto') {
    try {
      account = autoDetect(email, password);
    } catch (e) {
      return res.status(400).json({ error: e.message });
    }
  } else if (preset !== 'custom') {
    try {
      account = fromPreset(preset, email, password);
    } catch (e) {
      return res.status(400).json({ error: e.message });
    }
  } else {
    if (!host) return res.status(400).json({ error: '自定义配置需要填写服务器地址' });
    const smtpHost = String(req.body.smtpHost || '').trim();
    const smtpPort = parseInt(req.body.smtpPort, 10) || 465;
    account = {
      name: email.split('@')[1] || 'custom',
      host,
      port: parseInt(port, 10) || 993,
      secure: true,
      smtp: smtpHost ? {
        host: smtpHost,
        port: smtpPort,
        secure: smtpPort === 465,
        requireTLS: smtpPort === 587,
      } : null,
      auth: { user: email, pass: password },
    };
  }
  account.displayName = normalizeDisplayName(displayName, email);

  // 检查是否已连接同一邮箱
  for (const [existingId, existing] of clients) {
    if (existing.account.auth.user === email) {
      existing.account.displayName = account.displayName;
      saveAccounts();
      return res.json(accountSummary(existingId, existing.account, { exists: true }));
    }
  }

  const client = createMailClient(account);
  try {
    await client.connect();
    const imapCheck = resultOk('imap', account);
    let smtpCheck = null;
    try {
      smtpCheck = await verifySmtp(account);
    } catch (smtpErr) {
      smtpCheck = resultFail('smtp', account.smtp || {}, smtpErr);
    }
    const id = ++clientId;
    clients.set(id, client);
    saveAccounts();
    res.json(accountSummary(id, account, { checks: { imap: imapCheck, smtp: smtpCheck } }));
  } catch (err) {
    const imapCheck = resultFail('imap', account, err);
    let smtpCheck = null;
    try {
      smtpCheck = await verifySmtp(account);
    } catch (smtpErr) {
      smtpCheck = resultFail('smtp', account.smtp || {}, smtpErr);
    }
    res.status(500).json({
      error: buildDiagnosticMessage(imapCheck, smtpCheck, account, err),
      checks: { imap: imapCheck, smtp: smtpCheck },
    });
  }
});

// 已连接账户列表
app.get('/api/accounts', (req, res) => {
  const list = [];
  clients.forEach((c, id) => {
    list.push(accountSummary(id, c.account));
  });
  res.json(list);
});

// 账户排序
app.post('/api/accounts/reorder', (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  reorderAccounts(req.body?.order || []);
  const list = [];
  clients.forEach((c, id) => {
    list.push(accountSummary(id, c.account));
  });
  res.json({ ok: true, accounts: list });
});

// 更新账户信息
app.patch('/api/accounts/:id', (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  const id = parseInt(req.params.id, 10);
  const client = clients.get(id);
  if (!client) return res.status(404).json({ error: '账户不存在' });
  const email = client.account.auth?.user || '';
  client.account.displayName = normalizeDisplayName(req.body?.displayName, email);
  saveAccounts();
  res.json(accountSummary(id, client.account));
});

// 断开账户
app.delete('/api/accounts/:id', async (req, res) => {
  const id = parseInt(req.params.id, 10);
  const client = clients.get(id);
  if (!client) return res.status(404).json({ error: '账户不存在' });
  try { await client.disconnect(); } catch {}
  clients.delete(id);
  saveAccounts();
  res.json({ ok: true });
});

// 获取文件夹列表
app.get('/api/accounts/:id/folders', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });
  try {
    await client.ensureConnected();
    const folders = await client.client.list();
    res.json(folders.map(f => ({
      path: f.path,
      name: f.name,
      noselect: f.flags?.has('\\Noselect') || false,
    })));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 获取邮件列表
app.get('/api/accounts/:id/mails', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const count = parseInt(req.query.count, 10) || 20;
  const before = req.query.before ? parseInt(req.query.before, 10) : null;

  try {
    await client.ensureConnected();
    const lock = await client.client.getMailboxLock(folder);
    try {
      const status = await client.client.status(folder, { messages: true, unseen: true });
      const total = status.messages;
      if (total === 0) {
        return res.json({ total: 0, unseen: status.unseen, mails: [], hasMore: false });
      }

      let startSeq, endSeq;
      if (before) {
        endSeq = before - 1;
        if (endSeq < 1) {
          return res.json({ total, unseen: status.unseen, mails: [], hasMore: false });
        }
        startSeq = Math.max(1, endSeq - count + 1);
      } else {
        endSeq = total;
        startSeq = Math.max(1, total - count + 1);
      }

      const mails = [];

      for await (const msg of client.client.fetch(`${startSeq}:${endSeq}`, {
        envelope: true,
        flags: true,
        uid: true,
      })) {
        mails.push({
          uid: msg.uid,
          seq: msg.seq,
          date: msg.envelope.date,
          from: msg.envelope.from?.[0]
            ? { name: msg.envelope.from[0].name || '', address: msg.envelope.from[0].address }
            : { name: '', address: '(unknown)' },
          to: msg.envelope.to?.map(t => ({ name: t.name || '', address: t.address })) || [],
          subject: msg.envelope.subject || '(no subject)',
          seen: msg.flags?.has('\\Seen') || false,
          flagged: msg.flags?.has('\\Flagged') || false,
        });
      }

      mails.sort((a, b) => new Date(b.date) - new Date(a.date));
      res.json({ total, unseen: status.unseen, mails, hasMore: startSeq > 1 });
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 读取单封邮件
app.get('/api/accounts/:id/mails/:uid', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const uid = req.params.uid;

  try {
    await client.ensureConnected();
    const lock = await client.client.getMailboxLock(folder);
    try {
      const source = await client.client.download(uid, undefined, { uid: true });
      const parsed = await simpleParser(source.content);

      await client.client.messageFlagsAdd(uid, ['\\Seen'], { uid: true });

      res.json({
        subject: parsed.subject || '(no subject)',
        from: parsed.from?.text || '',
        to: parsed.to?.text || '',
        cc: parsed.cc?.text || '',
        date: parsed.date,
        text: parsed.text || '',
        html: parsed.html ? prepareHtmlForRender(parsed.html) : '',
        attachments: (parsed.attachments || []).map((a, i) => ({
          index: i,
          filename: a.filename || `attachment_${i}`,
          size: a.size,
          contentType: a.contentType,
        })),
      });
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 下载附件
app.get('/api/accounts/:id/mails/:uid/attachments/:index', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const index = parseInt(req.params.index, 10);
  const uid = req.params.uid;

  try {
    await client.ensureConnected();
    const lock = await client.client.getMailboxLock(folder);
    try {
      const source = await client.client.download(uid, undefined, { uid: true });
      const parsed = await simpleParser(source.content);

      const att = parsed.attachments?.[index];
      if (!att) return res.status(404).json({ error: '附件不存在' });

      const filename = att.filename || `attachment_${index}`;
      res.setHeader('Content-Type', att.contentType);
      res.setHeader('Content-Disposition', `attachment; filename="${encodeURIComponent(filename)}"`);
      res.send(att.content);
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 搜索邮件
app.get('/api/accounts/:id/search', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const keyword = req.query.q || '';
  const field = req.query.field || 'subject';
  if (!keyword) return res.status(400).json({ error: '请输入搜索关键词' });

  try {
    await client.ensureConnected();
    const lock = await client.client.getMailboxLock(folder);
    try {
      let searchQuery;
      if (field === 'all') {
        searchQuery = { or: [{ subject: keyword }, { from: keyword }, { body: keyword }] };
      } else if (['from', 'body', 'subject'].includes(field)) {
        searchQuery = { [field]: keyword };
      } else {
        searchQuery = { subject: keyword };
      }
      const uids = await client.client.search(searchQuery, { uid: true });
      if (uids.length === 0) return res.json([]);

      const mails = [];
      for await (const msg of client.client.fetch(uids.slice(0, 30), {
        envelope: true,
        flags: true,
        uid: true,
      }, { uid: true })) {
        mails.push({
          uid: msg.uid,
          date: msg.envelope.date,
          from: msg.envelope.from?.[0]
            ? { name: msg.envelope.from[0].name || '', address: msg.envelope.from[0].address }
            : { name: '', address: '(unknown)' },
          subject: msg.envelope.subject || '(no subject)',
          seen: msg.flags?.has('\\Seen') || false,
        });
      }
      mails.sort((a, b) => new Date(b.date) - new Date(a.date));
      res.json(mails);
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 删除邮件
app.delete('/api/accounts/:id/mails/:uid', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const uid = req.params.uid;

  try {
    await client.ensureConnected();
    const lock = await client.client.getMailboxLock(folder);
    try {
      await client.client.messageDelete(uid, { uid: true });
      res.json({ ok: true });
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 修改邮件标记 (已读/未读/星标)
const ALLOWED_FLAGS = new Set(['\\Seen', '\\Flagged', '\\Answered', '\\Draft', '\\Deleted']);

app.put('/api/accounts/:id/mails/:uid/flags', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const uid = req.params.uid;
  const { action, flags } = req.body || {};

  if (!action || !Array.isArray(flags) || flags.length === 0) {
    return res.status(400).json({ error: '需要 action 和 flags 参数' });
  }
  if (!['add', 'remove', 'set'].includes(action)) {
    return res.status(400).json({ error: 'action 必须是 add / remove / set' });
  }
  const safeFlags = flags.filter(f => ALLOWED_FLAGS.has(f));
  if (safeFlags.length === 0) {
    return res.status(400).json({ error: '无有效的 flag' });
  }

  const methods = { add: 'messageFlagsAdd', remove: 'messageFlagsRemove', set: 'messageFlagsSet' };

  try {
    await client.ensureConnected();
    const lock = await client.client.getMailboxLock(folder);
    try {
      await client.client[methods[action]](uid, safeFlags, { uid: true });
      res.json({ ok: true });
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 移动邮件到其他文件夹
app.post('/api/accounts/:id/mails/:uid/move', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const uid = req.params.uid;
  const { destination } = req.body || {};

  if (!destination) {
    return res.status(400).json({ error: '需要 destination 参数' });
  }

  try {
    await client.ensureConnected();
    const lock = await client.client.getMailboxLock(folder);
    try {
      await client.client.messageMove(uid, destination, { uid: true });
      res.json({ ok: true });
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 批量邮件操作
app.post('/api/accounts/:id/batch', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const { uids, action, destination } = req.body || {};

  if (!Array.isArray(uids) || uids.length === 0 || !action) {
    return res.status(400).json({ error: '需要 uids 和 action 参数' });
  }

  const uidRange = uids.join(',');

  try {
    await client.ensureConnected();
    const lock = await client.client.getMailboxLock(folder);
    try {
      switch (action) {
        case 'delete':
          await client.client.messageDelete(uidRange, { uid: true });
          break;
        case 'read':
          await client.client.messageFlagsAdd(uidRange, ['\\Seen'], { uid: true });
          break;
        case 'unread':
          await client.client.messageFlagsRemove(uidRange, ['\\Seen'], { uid: true });
          break;
        case 'flag':
          await client.client.messageFlagsAdd(uidRange, ['\\Flagged'], { uid: true });
          break;
        case 'unflag':
          await client.client.messageFlagsRemove(uidRange, ['\\Flagged'], { uid: true });
          break;
        case 'move':
          if (!destination) return res.status(400).json({ error: '移动操作需要 destination 参数' });
          await client.client.messageMove(uidRange, destination, { uid: true });
          break;
        default:
          return res.status(400).json({ error: `未知操作: ${action}` });
      }
      res.json({ ok: true, count: uids.length });
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 获取所有文件夹的未读数
app.get('/api/accounts/:id/folders/status', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  try {
    await client.ensureConnected();
    const folders = await client.client.list();
    const selectable = folders.filter(f => !f.flags?.has('\\Noselect')).slice(0, 20);

    const statuses = [];
    for (const f of selectable) {
      try {
        const st = await client.client.status(f.path, { messages: true, unseen: true });
        statuses.push({ path: f.path, name: f.name, messages: st.messages, unseen: st.unseen });
      } catch {
        statuses.push({ path: f.path, name: f.name, messages: 0, unseen: 0 });
      }
    }
    res.json(statuses);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// 批量导入账户 (格式: email:password，每行一个)
app.post('/api/accounts/batch', async (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  const { lines } = req.body;
  if (!lines || !lines.length) {
    return res.status(400).json({ error: '请提供账户列表' });
  }

  const results = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    // 支持 email:password 格式
    const sepIdx = trimmed.indexOf(':');
    if (sepIdx === -1) {
      results.push({ email: trimmed, ok: false, error: '格式错误，应为 email:password' });
      continue;
    }

    const email = trimmed.substring(0, sepIdx).trim();
    const password = trimmed.substring(sepIdx + 1).trim();
    if (!email || !password) {
      results.push({ email: email || '(空)', ok: false, error: '邮箱或密码为空' });
      continue;
    }

    // 检查是否已连接
    let alreadyExists = false;
    for (const [existingId, existing] of clients) {
      if (existing.account.auth.user === email) {
        results.push(accountSummary(existingId, existing.account, { ok: true, exists: true }));
        alreadyExists = true;
        break;
      }
    }
    if (alreadyExists) continue;

    try {
      const account = autoDetect(email, password);
      const client = createMailClient(account);
      await client.connect();
      let smtpCheck = null;
      try {
        smtpCheck = await verifySmtp(account);
      } catch (smtpErr) {
        smtpCheck = resultFail('smtp', account.smtp || {}, smtpErr);
      }
      const id = ++clientId;
      clients.set(id, client);
      results.push(accountSummary(id, account, {
        ok: true,
        checks: { imap: resultOk('imap', account), smtp: smtpCheck },
      }));
    } catch (err) {
      const fallbackAccount = (() => {
        try {
          return autoDetect(email, password);
        } catch {
          return { name: detectPreset(email) || 'auto', host: '', port: '', auth: { user: email } };
        }
      })();
      const imapCheck = resultFail('imap', fallbackAccount, err);
      let smtpCheck = null;
      try {
        smtpCheck = await verifySmtp(fallbackAccount);
      } catch (smtpErr) {
        smtpCheck = resultFail('smtp', fallbackAccount.smtp || {}, smtpErr);
      }
      results.push({
        email,
        ok: false,
        error: buildDiagnosticMessage(imapCheck, smtpCheck, fallbackAccount, err),
        checks: { imap: imapCheck, smtp: smtpCheck },
      });
    }
  }

  if (results.some(r => r.ok)) saveAccounts();
  res.json(results);
});

const PORT = process.env.PORT || 3939;

loadSettings();
restoreAccounts().then(() => {
  app.listen(PORT, () => {
    console.log(`IMAP Mail Client 已启动: http://localhost:${PORT}`);
  });
});
