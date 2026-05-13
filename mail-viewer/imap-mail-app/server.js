const express = require('express');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { simpleParser } = require('mailparser');
const nodemailer = require('nodemailer');
const MailComposer = require('nodemailer/lib/mail-composer');
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
const GOOGLE_AUTH_SCOPE = `${GOOGLE_SCOPE} openid email profile`;
const MICROSOFT_SCOPE = 'offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send openid email profile';
const MICROSOFT_TOKEN_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/token';
const MICROSOFT_AUTH_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize';
const VIRTUAL_ALL_FOLDER = '__memail_all__';
const VIRTUAL_UNREAD_FOLDER = '__memail_unread__';
const VIRTUAL_FOLDER_SCAN_LIMIT = 30;
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

function getOAuthProxyBaseUrl() {
  const publicBaseUrl = getPublicBaseUrl();
  if (!publicBaseUrl) return '';
  try {
    const url = new URL(publicBaseUrl);
    const pathname = url.pathname.replace(/\/+$/, '');
    url.pathname = pathname.endsWith('/imap') ? pathname : `${pathname}/imap`;
    url.search = '';
    url.hash = '';
    return normalizePublicBaseUrl(url.toString());
  } catch {
    return publicBaseUrl.endsWith('/imap') ? publicBaseUrl : `${publicBaseUrl}/imap`;
  }
}

function buildOAuthRedirectUri(provider) {
  const baseUrl = getOAuthProxyBaseUrl();
  return baseUrl ? `${baseUrl}/api/oauth/${provider}/callback` : '';
}

function getGoogleClientId() {
  return (runtimeSettings.google_client_id || process.env.GOOGLE_CLIENT_ID || '').trim();
}

function getGoogleClientSecret() {
  return (runtimeSettings.google_client_secret || process.env.GOOGLE_CLIENT_SECRET || '').trim();
}

function getGoogleRedirectUri() {
  return buildOAuthRedirectUri('gmail');
}

function getMicrosoftClientId() {
  return (runtimeSettings.microsoft_client_id || process.env.MICROSOFT_CLIENT_ID || '').trim();
}

function getMicrosoftClientSecret() {
  return (runtimeSettings.microsoft_client_secret || process.env.MICROSOFT_CLIENT_SECRET || '').trim();
}

function getMicrosoftRedirectUri() {
  return buildOAuthRedirectUri('outlook');
}

function getGoogleOAuthSettings() {
  const publicBaseUrl = getPublicBaseUrl();
  const clientId = getGoogleClientId();
  const clientSecret = getGoogleClientSecret();
  const redirectUri = getGoogleRedirectUri();
  return {
    enabled: Boolean(clientId && clientSecret && redirectUri),
    public_base_url: publicBaseUrl,
    oauth_base_url: getOAuthProxyBaseUrl(),
    google_client_id: clientId,
    google_client_secret_configured: Boolean(clientSecret),
    google_redirect_uri: redirectUri,
    redirect_uri_locked: true,
    scope: GOOGLE_AUTH_SCOPE,
  };
}

function getMicrosoftOAuthSettings() {
  const publicBaseUrl = getPublicBaseUrl();
  const clientId = getMicrosoftClientId();
  const clientSecret = getMicrosoftClientSecret();
  const redirectUri = getMicrosoftRedirectUri();
  return {
    enabled: Boolean(clientId && clientSecret && redirectUri),
    public_base_url: publicBaseUrl,
    oauth_base_url: getOAuthProxyBaseUrl(),
    microsoft_client_id: clientId,
    microsoft_client_secret_configured: Boolean(clientSecret),
    microsoft_redirect_uri: redirectUri,
    redirect_uri_locked: true,
    scope: MICROSOFT_SCOPE,
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
      google_client_secret: decryptSecret(data.google_client_secret_encrypted),
      microsoft_client_id: (data.microsoft_client_id || '').trim(),
      microsoft_client_secret: decryptSecret(data.microsoft_client_secret_encrypted),
    };
  } catch (err) {
    console.warn(`读取 IMAP 运行配置失败: ${err.message}`);
    runtimeSettings = {};
  }
}

function saveSettings() {
  const dir = path.dirname(SETTINGS_FILE);
  fs.mkdirSync(dir, { recursive: true });
  const existing = (() => {
    if (!fs.existsSync(SETTINGS_FILE)) return {};
    try {
      const parsed = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf-8'));
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  })();
  const data = {
    public_base_url: runtimeSettings.public_base_url || '',
    google_client_id: runtimeSettings.google_client_id || '',
    microsoft_client_id: runtimeSettings.microsoft_client_id || '',
  };
  const encryptedSecret = runtimeSettings.clear_google_client_secret ? null : encryptSecret(runtimeSettings.google_client_secret || '');
  if (encryptedSecret) {
    data.google_client_secret_encrypted = encryptedSecret;
  } else if (!runtimeSettings.clear_google_client_secret && existing.google_client_secret_encrypted) {
    data.google_client_secret_encrypted = existing.google_client_secret_encrypted;
  }
  const encryptedMicrosoftSecret = runtimeSettings.clear_microsoft_client_secret ? null : encryptSecret(runtimeSettings.microsoft_client_secret || '');
  if (encryptedMicrosoftSecret) {
    data.microsoft_client_secret_encrypted = encryptedMicrosoftSecret;
  } else if (!runtimeSettings.clear_microsoft_client_secret && existing.microsoft_client_secret_encrypted) {
    data.microsoft_client_secret_encrypted = existing.microsoft_client_secret_encrypted;
  }
  delete runtimeSettings.clear_google_client_secret;
  delete runtimeSettings.clear_microsoft_client_secret;
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

function normalizeSendName(value) {
  return normalizeDisplayName(value, '');
}

function normalizeAccountGroup(value) {
  return String(value || '').trim().slice(0, 40);
}

function normalizeSmtpConfig(smtp) {
  if (!smtp?.host) return null;
  const port = parseInt(smtp.port, 10) || 465;
  return {
    host: String(smtp.host || '').trim(),
    port,
    secure: smtp.secure !== undefined ? smtp.secure !== false : port === 465,
    requireTLS: smtp.requireTLS !== undefined ? !!smtp.requireTLS : port === 587 || port === 25,
  };
}

function effectiveSmtp(account) {
  if (account?.smtp?.host) return account.smtp;
  const preset = account?.name || detectPreset(account?.auth?.user);
  const smtp = PRESETS[preset]?.smtp;
  return normalizeSmtpConfig(smtp);
}

function buildCustomAccount({ email, password, displayName, host, port, smtpHost, smtpPort, name }) {
  if (!host) throw new Error('自定义配置需要填写 IMAP 服务器地址');
  const imapPort = parseInt(port, 10) || 993;
  const normalizedEmail = String(email || '').trim().toLowerCase();
  return {
    name: String(name || normalizedEmail.split('@')[1] || 'custom').trim().toLowerCase(),
    host: String(host || '').trim(),
    port: imapPort,
    secure: imapPort !== 143,
    smtp: normalizeSmtpConfig({
      host: String(smtpHost || '').trim(),
      port: parseInt(smtpPort, 10) || 465,
    }),
    auth: { user: normalizedEmail, pass: password },
    displayName: normalizeDisplayName(displayName, normalizedEmail),
  };
}

function isGmailAccount(preset, email) {
  const normalizedPreset = String(preset || '').trim().toLowerCase();
  const normalizedEmail = String(email || '').trim().toLowerCase();
  return normalizedPreset === 'gmail' ||
    normalizedEmail.endsWith('@gmail.com') ||
    normalizedEmail.endsWith('@googlemail.com');
}

function normalizeManualPassword(preset, email, password) {
  const raw = String(password || '');
  if (!raw) return raw;
  if (isGmailAccount(preset, email)) {
    // Google displays app passwords in grouped blocks; IMAP/SMTP should receive the compact token.
    return raw.replace(/\s+/g, '');
  }
  return raw;
}

function buildAccountFromRequest(body, fallbackAccount = null) {
  const preset = body?.preset || fallbackAccount?.name || 'auto';
  const email = String(body?.email || fallbackAccount?.auth?.user || '').trim().toLowerCase();
  const rawPassword = body?.password !== undefined ? String(body.password || '') : (fallbackAccount?.auth?.pass || '');
  const password = normalizeManualPassword(preset, email, rawPassword);
  const displayName = body?.displayName !== undefined ? body.displayName : fallbackAccount?.displayName;
  const sendName = body?.sendName !== undefined ? body.sendName : fallbackAccount?.sendName;
  const group = body?.group !== undefined ? body.group : fallbackAccount?.group;
  if (fallbackAccount?.oauth?.provider) {
    if (email && email !== String(fallbackAccount.auth?.user || '').toLowerCase()) {
      throw new Error('OAuth 账号不能直接修改邮箱地址，请重新登录授权');
    }
    return {
      ...fallbackAccount,
      displayName: normalizeDisplayName(displayName, fallbackAccount.auth?.user || email),
      sendName: normalizeSendName(sendName),
      group: normalizeAccountGroup(group),
    };
  }
  if (!email) throw new Error('请填写邮箱');
  if (!password) throw new Error('请填写邮箱密码或授权码');

  let account;
  const hasManualConfig = body?.host || body?.smtpHost || body?.port || body?.smtpPort;
  if (fallbackAccount && preset !== 'custom' && hasManualConfig) {
    account = buildCustomAccount({
      email,
      password,
      displayName,
      host: body?.host || fallbackAccount?.host || '',
      port: body?.port || fallbackAccount?.port || 993,
      smtpHost: body?.smtpHost || fallbackAccount?.smtp?.host || '',
      smtpPort: body?.smtpPort || fallbackAccount?.smtp?.port || 465,
      name: body?.name || fallbackAccount?.name || email.split('@')[1] || 'custom',
    });
  } else if (!preset || preset === 'auto') {
    account = autoDetect(email, password);
  } else if (preset !== 'custom') {
    account = fromPreset(preset, email, password);
  } else {
    account = buildCustomAccount({
      email,
      password,
      displayName,
      host: body?.host || fallbackAccount?.host || '',
      port: body?.port || fallbackAccount?.port || 993,
      smtpHost: body?.smtpHost || fallbackAccount?.smtp?.host || '',
      smtpPort: body?.smtpPort || fallbackAccount?.smtp?.port || 465,
      name: body?.name || fallbackAccount?.name || email.split('@')[1] || 'custom',
    });
  }
  account.displayName = normalizeDisplayName(displayName, email);
  account.sendName = normalizeSendName(sendName);
  account.group = normalizeAccountGroup(group);
  return account;
}

function accountSummary(id, account, extra = {}) {
  const email = account?.auth?.user || '';
  const smtp = effectiveSmtp(account);
  return {
    id,
    name: account?.name || detectPreset(email) || 'custom',
    email,
    displayName: normalizeDisplayName(account?.displayName, email),
    sendName: normalizeSendName(account?.sendName),
    group: normalizeAccountGroup(account?.group),
    host: account?.host || '',
    port: account?.port || 993,
    secure: account?.secure !== false,
    smtp: smtp ? {
      host: smtp.host,
      port: smtp.port,
      secure: smtp.secure,
      requireTLS: !!smtp.requireTLS,
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
  const smtp = effectiveSmtp(account);
  if (!smtp?.host) return null;
  const transport = nodemailer.createTransport({
    host: smtp.host,
    port: smtp.port || 465,
    secure: smtp.secure !== false,
    requireTLS: !!smtp.requireTLS,
    auth: account.auth?.pass ? account.auth : undefined,
    connectionTimeout: 12000,
    greetingTimeout: 12000,
    socketTimeout: 12000,
    logger: false,
  });
  try {
    await transport.verify();
    return resultOk('smtp', smtp);
  } catch (err) {
    return resultFail('smtp', smtp, err);
  } finally {
    transport.close();
  }
}

async function createSmtpTransport(account) {
  const smtp = effectiveSmtp(account);
  if (!smtp?.host) {
    throw new Error('该账号未配置 SMTP，无法发信');
  }
  let auth = account.auth?.pass ? account.auth : undefined;
  if (account.oauth?.provider) {
    const token = await refreshOAuthToken(account);
    auth = {
      type: 'OAuth2',
      user: account.auth?.user || '',
      accessToken: token.access_token,
    };
  }
  return nodemailer.createTransport({
    host: smtp.host,
    port: smtp.port || 465,
    secure: smtp.secure !== false,
    requireTLS: !!smtp.requireTLS,
    auth,
    connectionTimeout: 20000,
    greetingTimeout: 20000,
    socketTimeout: 30000,
    logger: false,
  });
}

function buildRawMessage(mailOptions) {
  return new MailComposer(mailOptions).compile().build();
}

function isSelectableFolder(folder) {
  return folder && !folder.flags?.has('\\Noselect') && !folder.flags?.has('\\NonExistent');
}

function imapApiError(err, context = {}) {
  const raw = err?.message || String(err || 'IMAP 操作失败');
  const response = String(err?.response || '').trim();
  const folder = context.folder ? String(context.folder) : '';
  const command = context.command ? String(context.command) : '';
  const parts = [];
  if (folder) parts.push(`文件夹: ${folder}`);
  if (command) parts.push(`操作: ${command}`);
  if (response && !raw.includes(response)) parts.push(`服务器返回: ${response}`);

  let message = raw;
  if (/Command failed/i.test(raw)) {
    if (/STATUS/i.test(command)) {
      message = '服务器拒绝读取该文件夹状态，已避免使用不兼容的 STATUS 流程，请刷新后重试';
    } else if (/SELECT|EXAMINE|getMailboxLock/i.test(command)) {
      message = '服务器拒绝打开该文件夹，可能是文件夹已被删除、不可选或缓存已过期';
    } else {
      message = 'IMAP 服务器拒绝当前操作';
    }
  } else if (/Mailbox doesn't exist|No such mailbox|not found|不存在|not selectable|NonExistent/i.test(raw + response)) {
    message = '该文件夹不存在或不可选择，请刷新文件夹列表后重试';
  } else if (/Connection not available|Socket closed|Timed out|timeout|closed/i.test(raw + response)) {
    message = '邮箱连接已断开或超时，请稍后重试';
  }

  return [message, ...parts].join('。');
}

function selectedMailboxStatus(client, fallbackFolder) {
  const mailbox = client.client.mailbox || {};
  return {
    messages: Number.isFinite(Number(mailbox.exists)) ? Number(mailbox.exists) : 0,
    unseen: Number.isFinite(Number(mailbox.unseen)) ? Number(mailbox.unseen) : 0,
    path: mailbox.path || fallbackFolder,
  };
}

function isVirtualFolder(folderPath) {
  return folderPath === VIRTUAL_ALL_FOLDER || folderPath === VIRTUAL_UNREAD_FOLDER;
}

function isAllMailFolder(folder) {
  const specialUse = String(folder?.specialUse || '').toLowerCase();
  const name = String(folder?.name || folder?.path || '').toLowerCase();
  return specialUse === '\\all' || name === 'all mail' || name.includes('[gmail]/all mail') || name.includes('所有邮件');
}

function aggregateFolders(folders) {
  const selectable = (Array.isArray(folders) ? folders : []).filter(isSelectableFolder);
  const allMail = selectable.find(isAllMailFolder);
  return (allMail ? [allMail] : selectable).slice(0, VIRTUAL_FOLDER_SCAN_LIMIT);
}

function mapEnvelopeMessage(msg, folder) {
  return {
    uid: msg.uid,
    seq: msg.seq,
    folder: folder?.path || '',
    folderName: folder?.name || folder?.path || '',
    date: msg.envelope.date,
    from: msg.envelope.from?.[0]
      ? { name: msg.envelope.from[0].name || '', address: msg.envelope.from[0].address }
      : { name: '', address: '(unknown)' },
    to: msg.envelope.to?.map(t => ({ name: t.name || '', address: t.address })) || [],
    subject: msg.envelope.subject || '(no subject)',
    seen: msg.flags?.has('\\Seen') || false,
    flagged: msg.flags?.has('\\Flagged') || false,
  };
}

async function fetchFolderSlice(client, folder, count, unreadOnly = false) {
  const lock = await client.client.getMailboxLock(folder.path);
  try {
    const status = selectedMailboxStatus(client, folder.path);
    const total = unreadOnly ? (status.unseen || 0) : (status.messages || 0);
    if (!total) {
      return { messages: [], total, unseen: status.unseen || 0, hasMore: false };
    }

    const messages = [];
    if (unreadOnly) {
      const uids = await client.client.search({ seen: false }, { uid: true });
      const selectedUids = uids.slice(-count);
      if (selectedUids.length) {
        for await (const msg of client.client.fetch(selectedUids, {
          envelope: true,
          flags: true,
          uid: true,
        }, { uid: true })) {
          messages.push(mapEnvelopeMessage(msg, folder));
        }
      }
      return { messages, total, unseen: status.unseen || 0, hasMore: uids.length > selectedUids.length };
    }

    const endSeq = status.messages;
    const startSeq = Math.max(1, endSeq - count + 1);
    for await (const msg of client.client.fetch(`${startSeq}:${endSeq}`, {
      envelope: true,
      flags: true,
      uid: true,
    })) {
      messages.push(mapEnvelopeMessage(msg, folder));
    }
    return { messages, total, unseen: status.unseen || 0, hasMore: startSeq > 1 };
  } finally {
    lock.release();
  }
}

async function fetchVirtualFolderMessages(client, folderPath, count) {
  const unreadOnly = folderPath === VIRTUAL_UNREAD_FOLDER;
  const folders = aggregateFolders(await client.client.list());
  const allMessages = [];
  let total = 0;
  let unseen = 0;
  let hasMore = false;

  for (const folder of folders) {
    try {
      const result = await fetchFolderSlice(client, folder, count, unreadOnly);
      total += result.total;
      unseen += result.unseen;
      hasMore = hasMore || result.hasMore;
      allMessages.push(...result.messages);
    } catch (err) {
      console.warn(`Failed to aggregate folder ${folder.path}: ${imapApiError(err, { folder: folder.path, command: 'SELECT/FETCH' })}`);
    }
  }

  allMessages.sort((a, b) => new Date(b.date || 0) - new Date(a.date || 0));
  return {
    total,
    unseen,
    mails: allMessages.slice(0, count),
    hasMore: hasMore || allMessages.length > count,
    virtual: true,
  };
}

function findSentFolder(folders) {
  const selectable = (Array.isArray(folders) ? folders : []).filter(isSelectableFolder);
  return (
    selectable.find(f => f.specialUse === '\\Sent') ||
    selectable.find(f => f.flags?.has('\\Sent')) ||
    selectable.find(f => /^(sent|sent messages|sent mail|已发送|已發送|寄件備份|寄件备份)$/i.test(String(f.name || f.path || '').trim())) ||
    selectable.find(f => /sent|已发送|已發送|寄件/i.test(String(f.name || f.path || ''))) ||
    null
  );
}

async function appendToSentFolder(client, rawMessage) {
  await client.ensureConnected();
  const folders = await client.client.list();
  const sentFolder = findSentFolder(folders);
  if (!sentFolder?.path) {
    throw new Error('未找到已发送文件夹');
  }
  return client.client.append(sentFolder.path, rawMessage, ['\\Seen'], new Date());
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
    hints.push('个人 Gmail 可以用 16 位应用专用密码通过 IMAP/SMTP 登录，普通 Google 登录密码不会通过');
    hints.push('请确认 Gmail 已开启 IMAP；如果是 Google Workspace，管理员可能禁用应用专用密码或要求 OAuth2');
    hints.push('也可以在邮箱账号设置里配置 Google OAuth 后使用“Gmail 登录”授权接入');
  }
  if (preset === 'outlook') {
    hints.push('Outlook 支持手动填写 IMAP/SMTP 服务器，但 Microsoft 官方认证方式通常是 OAuth2/Modern Auth');
    hints.push('当前表单会使用邮箱+密码尝试传统认证；如果服务器返回 Basic Auth disabled，请改用“Outlook 登录”授权');
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

function hasMicrosoftOAuthConfig() {
  return getMicrosoftOAuthSettings().enabled;
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

async function exchangeMicrosoftToken(params) {
  const resp = await fetch(MICROSOFT_TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(params),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.error_description || data.error || 'Microsoft OAuth token exchange failed');
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
  if (!account.oauth?.provider) throw new Error('Unsupported OAuth provider');
  if (account.oauth.access_token && account.oauth.expires_at && Date.now() < account.oauth.expires_at - 60000) {
    return { access_token: account.oauth.access_token };
  }
  let token;
  if (account.oauth.provider === 'gmail') {
    token = await exchangeGoogleToken({
      client_id: getGoogleClientId(),
      client_secret: getGoogleClientSecret(),
      refresh_token: account.oauth.refresh_token,
      grant_type: 'refresh_token',
    });
  } else if (account.oauth.provider === 'microsoft') {
    token = await exchangeMicrosoftToken({
      client_id: getMicrosoftClientId(),
      client_secret: getMicrosoftClientSecret(),
      refresh_token: account.oauth.refresh_token,
      grant_type: 'refresh_token',
      scope: MICROSOFT_SCOPE,
    });
  } else {
    throw new Error('Unsupported OAuth provider');
  }
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
    smtp: {
      host: PRESETS.gmail.smtp.host,
      port: PRESETS.gmail.smtp.port,
      secure: PRESETS.gmail.smtp.secure !== false,
      requireTLS: !!PRESETS.gmail.smtp.requireTLS,
    },
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

function buildMicrosoftAccount(email, token) {
  return {
    name: 'outlook',
    host: PRESETS.outlook.host,
    port: PRESETS.outlook.port,
    secure: PRESETS.outlook.secure,
    smtp: {
      host: PRESETS.outlook.smtp.host,
      port: PRESETS.outlook.smtp.port,
      secure: PRESETS.outlook.smtp.secure !== false,
      requireTLS: !!PRESETS.outlook.smtp.requireTLS,
    },
    auth: { user: email },
    oauth: {
      provider: 'microsoft',
      refresh_token: token.refresh_token,
      access_token: token.access_token,
      expires_at: Date.now() + ((token.expires_in || 3600) * 1000),
      scope: token.scope || MICROSOFT_SCOPE,
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
    scope: GOOGLE_AUTH_SCOPE,
  });
});

app.get('/api/oauth/outlook/status', (req, res) => {
  const settings = getMicrosoftOAuthSettings();
  res.json({
    enabled: settings.enabled,
    redirectUri: settings.microsoft_redirect_uri,
    scope: MICROSOFT_SCOPE,
  });
});

app.get('/api/settings', (req, res) => {
  res.json({
    success: true,
    settings: {
      ...getGoogleOAuthSettings(),
      microsoft: getMicrosoftOAuthSettings(),
    },
  });
});

app.post('/api/settings', (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  const data = req.body || {};
  runtimeSettings.public_base_url = normalizePublicBaseUrl(data.public_base_url || '');
  runtimeSettings.google_client_id = (data.google_client_id || '').trim();
  runtimeSettings.microsoft_client_id = (data.microsoft_client_id || '').trim();

  if (data.clear_google_client_secret) {
    runtimeSettings.google_client_secret = '';
    runtimeSettings.clear_google_client_secret = true;
  } else if (typeof data.google_client_secret === 'string' && data.google_client_secret.trim()) {
    runtimeSettings.google_client_secret = data.google_client_secret.trim();
    runtimeSettings.clear_google_client_secret = false;
  }
  if (data.clear_microsoft_client_secret) {
    runtimeSettings.microsoft_client_secret = '';
    runtimeSettings.clear_microsoft_client_secret = true;
  } else if (typeof data.microsoft_client_secret === 'string' && data.microsoft_client_secret.trim()) {
    runtimeSettings.microsoft_client_secret = data.microsoft_client_secret.trim();
    runtimeSettings.clear_microsoft_client_secret = false;
  }

  try {
    saveSettings();
    res.json({
      success: true,
      settings: {
        ...getGoogleOAuthSettings(),
        microsoft: getMicrosoftOAuthSettings(),
      },
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
  oauthStates.set(state, { createdAt: Date.now(), provider: 'gmail' });
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: 'code',
    scope: GOOGLE_AUTH_SCOPE,
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
  if (!stateInfo || stateInfo.provider !== 'gmail' || Date.now() - stateInfo.createdAt > 10 * 60 * 1000) {
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
    account.group = normalizeAccountGroup('');
    const client = createMailClient(account);
    await client.connect();
    const id = setClient(account, client);
    res.send(`<!doctype html><meta charset="utf-8"><script>window.opener&&window.opener.postMessage({type:'gmail-oauth-success',id:${JSON.stringify(id)},email:${JSON.stringify(email)}},window.location.origin);window.close();</script><p>Gmail OAuth 登录成功，可以关闭此窗口。</p>`);
  } catch (err) {
    res.status(500).send(`Gmail OAuth failed: ${err.message}`);
  }
});

app.post('/api/oauth/outlook/start', (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  if (!hasMicrosoftOAuthConfig()) {
    return res.status(400).json({ error: 'Outlook OAuth 未配置，请在后台系统配置中填写 Microsoft Client ID / Secret / Redirect URI' });
  }
  const state = crypto.randomBytes(24).toString('hex');
  oauthStates.set(state, { createdAt: Date.now(), provider: 'microsoft' });
  const params = new URLSearchParams({
    client_id: getMicrosoftClientId(),
    redirect_uri: getMicrosoftRedirectUri(),
    response_type: 'code',
    response_mode: 'query',
    scope: MICROSOFT_SCOPE,
    prompt: 'select_account',
    state,
  });
  res.json({ url: `${MICROSOFT_AUTH_URL}?${params.toString()}` });
});

app.get('/api/oauth/outlook/callback', async (req, res) => {
  if (!ACCOUNTS_SECRET) {
    return res.status(500).send('IMAP_ACCOUNTS_SECRET or APP_SECRET is not configured; external IMAP accounts cannot be persisted.');
  }
  if (!hasMicrosoftOAuthConfig()) {
    return res.status(500).send('Outlook OAuth is not configured.');
  }
  const { code, state, error, error_description: errorDescription } = req.query;
  if (error) {
    return res.status(400).send(`Microsoft OAuth failed: ${errorDescription || error}`);
  }
  const stateInfo = oauthStates.get(state);
  oauthStates.delete(state);
  if (!stateInfo || stateInfo.provider !== 'microsoft' || Date.now() - stateInfo.createdAt > 10 * 60 * 1000) {
    return res.status(400).send('Invalid or expired OAuth state');
  }

  try {
    const token = await exchangeMicrosoftToken({
      client_id: getMicrosoftClientId(),
      client_secret: getMicrosoftClientSecret(),
      code,
      grant_type: 'authorization_code',
      redirect_uri: getMicrosoftRedirectUri(),
      scope: MICROSOFT_SCOPE,
    });
    if (!token.refresh_token) {
      throw new Error('Microsoft did not return a refresh token. Check offline_access permission and consent settings.');
    }
    const claims = JSON.parse(Buffer.from(String(token.id_token || '').split('.')[1] || '', 'base64url').toString('utf8') || '{}');
    const email = String(claims.email || claims.preferred_username || claims.upn || '').toLowerCase();
    if (!email) {
      throw new Error('Microsoft 未返回邮箱地址');
    }
    const account = buildMicrosoftAccount(email, token);
    account.displayName = normalizeDisplayName(claims.name || email, email);
    account.group = normalizeAccountGroup('');
    const client = createMailClient(account);
    await client.connect();
    const id = setClient(account, client);
    res.send(`<!doctype html><meta charset="utf-8"><script>window.opener&&window.opener.postMessage({type:'outlook-oauth-success',id:${JSON.stringify(id)},email:${JSON.stringify(email)}},window.location.origin);window.close();</script><p>Outlook OAuth 登录成功，可以关闭此窗口。</p>`);
  } catch (err) {
    res.status(500).send(`Outlook OAuth failed: ${err.message}`);
  }
});

// 添加账户
app.post('/api/accounts', async (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  let account;
  try {
    account = buildAccountFromRequest(req.body);
  } catch (e) {
    return res.status(400).json({ error: e.message });
  }
  const email = account.auth.user;

  // 检查是否已连接同一邮箱
  for (const [existingId, existing] of clients) {
    if (existing.account.auth.user === email) {
      existing.account.displayName = account.displayName;
      existing.account.sendName = account.sendName;
      existing.account.group = account.group;
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
      smtpCheck = resultFail('smtp', effectiveSmtp(account) || {}, smtpErr);
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
      smtpCheck = resultFail('smtp', effectiveSmtp(account) || {}, smtpErr);
    }
    res.status(500).json({
      error: buildDiagnosticMessage(imapCheck, smtpCheck, account, err),
      checks: { imap: imapCheck, smtp: smtpCheck },
    });
  }
});

// 已连接账户列表
app.get('/api/accounts', (req, res) => {
  try {
    const list = [];
    clients.forEach((c, id) => {
      list.push(accountSummary(id, c.account));
    });
    res.json(list);
  } catch (err) {
    res.status(500).json({ error: err.message || '账户列表加载失败' });
  }
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
app.patch('/api/accounts/:id', async (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  const id = parseInt(req.params.id, 10);
  const client = clients.get(id);
  if (!client) return res.status(404).json({ error: '账户不存在' });
  let account;
  try {
    account = buildAccountFromRequest(req.body || {}, client.account);
  } catch (e) {
    return res.status(400).json({ error: e.message });
  }

  for (const [existingId, existing] of clients) {
    if (existingId !== id && existing.account.auth.user === account.auth.user) {
      return res.status(409).json({ error: '该邮箱账号已存在，不能重复保存' });
    }
  }

  const updatedClient = createMailClient(account);
  try {
    await updatedClient.connect();
    const imapCheck = resultOk('imap', account);
    let smtpCheck = null;
    try {
      smtpCheck = await verifySmtp(account);
    } catch (smtpErr) {
      smtpCheck = resultFail('smtp', effectiveSmtp(account) || {}, smtpErr);
    }
    try { await client.disconnect(); } catch {}
    clients.set(id, updatedClient);
    saveAccounts();
    res.json(accountSummary(id, account, { checks: { imap: imapCheck, smtp: smtpCheck } }));
  } catch (err) {
    const imapCheck = resultFail('imap', account, err);
    let smtpCheck = null;
    try {
      smtpCheck = await verifySmtp(account);
    } catch (smtpErr) {
      smtpCheck = resultFail('smtp', effectiveSmtp(account) || {}, smtpErr);
    }
    res.status(500).json({
      error: buildDiagnosticMessage(imapCheck, smtpCheck, account, err),
      checks: { imap: imapCheck, smtp: smtpCheck },
    });
  }
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
      specialUse: f.specialUse || '',
    })));
  } catch (err) {
    res.status(500).json({ error: imapApiError(err, { command: 'LIST' }) });
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
    if (isVirtualFolder(folder)) {
      return res.json(await fetchVirtualFolderMessages(client, folder, count));
    }
    const lock = await client.client.getMailboxLock(folder);
    try {
      const status = selectedMailboxStatus(client, folder);
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
        mails.push(mapEnvelopeMessage(msg, { path: folder, name: folder }));
      }

      mails.sort((a, b) => new Date(b.date) - new Date(a.date));
      res.json({ total, unseen: status.unseen, mails, hasMore: startSeq > 1 });
    } finally {
      lock.release();
    }
  } catch (err) {
    res.status(500).json({ error: imapApiError(err, { folder, command: 'SELECT/FETCH' }) });
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
    res.status(500).json({ error: imapApiError(err, { folder, command: 'SELECT/DOWNLOAD' }) });
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
    res.status(500).json({ error: imapApiError(err, { folder, command: 'SELECT/DOWNLOAD_ATTACHMENT' }) });
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
    res.status(500).json({ error: imapApiError(err, { folder, command: 'SEARCH' }) });
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

// 使用外部账号 SMTP 发信
app.post('/api/accounts/:id/send', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });
  const to = String(req.body?.to || '').trim();
  const subject = String(req.body?.subject || '').trim();
  const text = String(req.body?.text || '').trim();
  const html = String(req.body?.html || '').trim();
  const requestedFromName = String(req.body?.fromName || '').trim();
  if (!to) return res.status(400).json({ error: '请填写收件人' });
  if (!subject) return res.status(400).json({ error: '请填写主题' });
  if (!text && !html) return res.status(400).json({ error: '请填写邮件正文' });

  const account = client.account;
  const fromEmail = account.auth?.user || '';
  const fromName = normalizeSendName(requestedFromName)
    || normalizeSendName(account.sendName)
    || normalizeDisplayName(fromEmail.split('@')[0], fromEmail);
  const mailOptions = {
    from: fromName && fromName !== fromEmail ? `${fromName} <${fromEmail}>` : fromEmail,
    to: to.split(',').map(item => item.trim()).filter(Boolean),
    subject,
    text,
    html,
    replyTo: fromEmail,
  };
  let transport;
  try {
    transport = await createSmtpTransport(account);
    const info = await transport.sendMail(mailOptions);
    let sentSaved = false;
    try {
      const rawMessage = await buildRawMessage({
        ...mailOptions,
        messageId: info.messageId || undefined,
        date: new Date(),
      });
      await appendToSentFolder(client, rawMessage);
      sentSaved = true;
    } catch (appendErr) {
      console.warn(`保存已发送失败 (${fromEmail}): ${appendErr.message}`);
    }
    res.json({ ok: true, messageId: info.messageId || '', sentSaved });
  } catch (err) {
    res.status(502).json({ error: err.message || '发送失败' });
  } finally {
    if (transport) transport.close();
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
        smtpCheck = resultFail('smtp', effectiveSmtp(account) || {}, smtpErr);
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
        smtpCheck = resultFail('smtp', effectiveSmtp(fallbackAccount) || {}, smtpErr);
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

app.use((err, req, res, next) => {
  console.error(`IMAP API 未处理错误 ${req.method} ${req.originalUrl}:`, err);
  if (res.headersSent) {
    return next(err);
  }
  return res.status(500).json({ error: err.message || 'IMAP 服务内部错误' });
});
