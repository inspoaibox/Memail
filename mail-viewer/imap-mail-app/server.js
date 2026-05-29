const express = require('express');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { simpleParser } = require('mailparser');
const nodemailer = require('nodemailer');
const MailComposer = require('nodemailer/lib/mail-composer');
const { MongoClient } = require('mongodb');
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
const MONGO_URL = process.env.MONGO_URL || '';
const MONGO_DB_NAME = process.env.MONGO_DB_NAME || process.env.DB_NAME || 'mailserver';
const IMAP_CACHE_TTL_MS = Math.max(30, parseInt(process.env.IMAP_CACHE_TTL_SECONDS || '86400', 10)) * 1000;
const IMAP_CACHE_SYNC_WINDOW = Math.max(30, parseInt(process.env.IMAP_CACHE_SYNC_WINDOW || '100', 10));
const IMAP_CACHE_LOOKBACK = Math.max(20, parseInt(process.env.IMAP_CACHE_LOOKBACK || '50', 10));
const IMAP_CACHE_BODY_PREFETCH = Math.min(100, Math.max(0, parseInt(process.env.IMAP_CACHE_BODY_PREFETCH || '50', 10)));
const MAX_SEND_ATTACHMENT_BYTES = Math.max(1024 * 1024, parseInt(process.env.MAX_SEND_ATTACHMENT_BYTES || String(15 * 1024 * 1024), 10));
const MAX_SEND_ATTACHMENTS_BYTES = Math.max(1024 * 1024, parseInt(process.env.MAX_SEND_ATTACHMENTS_BYTES || String(25 * 1024 * 1024), 10));
const IMAP_SYNC_SCHEDULER_ENABLED = process.env.IMAP_SYNC_SCHEDULER_ENABLED !== '0';
const IMAP_SYNC_CHECK_INTERVAL_MS = Math.max(60, parseInt(process.env.IMAP_SYNC_CHECK_INTERVAL_SECONDS || '900', 10)) * 1000;
const IMAP_SYNC_STARTUP_DELAY_MS = Math.max(5, parseInt(process.env.IMAP_SYNC_STARTUP_DELAY_SECONDS || '20', 10)) * 1000;
const GOOGLE_SCOPE = 'https://mail.google.com/';
const GOOGLE_AUTH_SCOPE = `${GOOGLE_SCOPE} openid email profile`;
const MICROSOFT_SCOPE = 'offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send openid email profile';
const MICROSOFT_TOKEN_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/token';
const MICROSOFT_AUTH_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize';
const VIRTUAL_ALL_FOLDER = '__memail_all__';
const VIRTUAL_UNREAD_FOLDER = '__memail_unread__';
const ACCOUNT_SYNC_STATE_FOLDER = '__memail_account__';
const VIRTUAL_FOLDER_SCAN_LIMIT = 30;
const oauthStates = new Map();
let runtimeSettings = {};
let mongoClient = null;
let cacheDb = null;
const accountSyncJobs = new Map();
const bodyPrefetchJobs = new Set();
let syncSchedulerTimer = null;
let syncSchedulerRunning = false;
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

function normalizeAddressList(value) {
  if (Array.isArray(value)) return value.map(item => String(item || '').trim()).filter(Boolean);
  return String(value || '').split(',').map(item => item.trim()).filter(Boolean);
}

function normalizeSendAttachments(value) {
  if (!Array.isArray(value)) return [];
  let totalSize = 0;
  return value.slice(0, 20).map(item => {
    if (!item || typeof item !== 'object') return null;
    const filename = String(item.filename || item.name || 'attachment').trim() || 'attachment';
    const content = String(item.content || '').trim();
    if (!content) return null;
    const data = Buffer.from(content, 'base64');
    if (data.length > MAX_SEND_ATTACHMENT_BYTES) return null;
    totalSize += data.length;
    if (totalSize > MAX_SEND_ATTACHMENTS_BYTES) return null;
    return {
      filename,
      content: data,
      contentType: String(item.contentType || item.content_type || 'application/octet-stream').trim() || 'application/octet-stream',
    };
  }).filter(Boolean);
}

function stableAccountKey(account) {
  const raw = [
    String(account?.auth?.user || '').trim().toLowerCase(),
    String(account?.host || '').trim().toLowerCase(),
    String(account?.port || ''),
  ].join('|');
  return crypto.createHash('sha256').update(raw).digest('hex');
}

function cacheCollections() {
  if (!cacheDb) return null;
  return {
    folders: cacheDb.collection('external_folders'),
    messages: cacheDb.collection('external_messages'),
    bodies: cacheDb.collection('external_message_bodies'),
    syncState: cacheDb.collection('external_sync_state'),
  };
}

async function initCacheDb() {
  if (!MONGO_URL) {
    console.warn('IMAP cache is disabled: MONGO_URL is not configured.');
    return;
  }
  mongoClient = new MongoClient(MONGO_URL, { serverSelectionTimeoutMS: 5000 });
  await mongoClient.connect();
  cacheDb = mongoClient.db(MONGO_DB_NAME);
  const collections = cacheCollections();
  await Promise.all([
    collections.messages.createIndex({ accountKey: 1, folder: 1, uid: 1 }, { unique: true }),
    collections.messages.createIndex({ accountKey: 1, folder: 1, date: -1 }),
    collections.messages.createIndex({ accountKey: 1, seen: 1, date: -1 }),
    collections.messages.createIndex({ accountKey: 1, date: -1 }),
    collections.bodies.createIndex({ accountKey: 1, folder: 1, uid: 1 }, { unique: true }),
    collections.bodies.createIndex({ accountKey: 1, updatedAt: -1 }),
    collections.folders.createIndex({ accountKey: 1, path: 1 }, { unique: true }),
    collections.syncState.createIndex({ accountKey: 1, folder: 1 }, { unique: true }),
  ]);
  console.log(`IMAP cache connected: ${MONGO_DB_NAME}`);
}

async function closeCacheDb() {
  if (mongoClient) {
    await mongoClient.close();
    mongoClient = null;
    cacheDb = null;
  }
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

function normalizeUid(uid) {
  const num = Number(uid);
  return Number.isFinite(num) ? num : String(uid || '');
}

function normalizeUidList(uids) {
  return (Array.isArray(uids) ? uids : [uids])
    .map(normalizeUid)
    .filter(uid => uid !== '');
}

function normalizeCacheMessage(doc) {
  return {
    accountKey: doc.accountKey || '',
    accountEmail: doc.accountEmail || '',
    provider: doc.provider || '',
    uid: doc.uid,
    seq: doc.seq || 0,
    folder: doc.folder,
    folderName: doc.folderName || doc.folder,
    date: doc.date,
    from: doc.from || { name: '', address: '(unknown)' },
    to: Array.isArray(doc.to) ? doc.to : [],
    subject: doc.subject || '(no subject)',
    seen: !!doc.seen,
    flagged: !!doc.flagged,
    cached: true,
  };
}

function attachCurrentAccountIds(mails) {
  const accountIdsByKey = new Map();
  for (const [id, client] of clients.entries()) {
    accountIdsByKey.set(stableAccountKey(client.account), id);
  }
  return (Array.isArray(mails) ? mails : []).map(mail => ({
    ...mail,
    accountId: accountIdsByKey.get(mail.accountKey) || mail.accountId || null,
  }));
}

function normalizeCachedBody(doc) {
  return {
    subject: doc.subject || '(no subject)',
    from: doc.from || '',
    to: doc.to || '',
    cc: doc.cc || '',
    date: doc.date || null,
    text: doc.text || '',
    html: doc.html || '',
    attachments: Array.isArray(doc.attachments) ? doc.attachments : [],
    cached: true,
    cachedAt: doc.updatedAt || doc.createdAt || null,
  };
}

function normalizeSyncStatus(state) {
  if (!state) return { synced: false, stale: true, syncing: false };
  const syncedAtMs = new Date(state.syncedAt || 0).getTime();
  return {
    synced: !!state.syncedAt,
    stale: !state.syncedAt || Date.now() - syncedAtMs > IMAP_CACHE_TTL_MS,
    syncing: !!state.syncing,
    syncedAt: state.syncedAt || null,
    error: state.error || '',
    uidValidity: state.uidValidity ? String(state.uidValidity) : '',
    highestUid: state.highestUid || 0,
    messages: state.messages || 0,
    unseen: state.unseen || 0,
    folders: state.folders || 0,
  };
}

function messageDetailFromParsed(parsed) {
  return {
    subject: parsed.subject || '(no subject)',
    from: parsed.from?.text || '',
    to: parsed.to?.text || '',
    cc: parsed.cc?.text || '',
    date: parsed.date || null,
    text: parsed.text || '',
    html: parsed.html ? prepareHtmlForRender(parsed.html) : '',
    attachments: (parsed.attachments || []).map((a, i) => ({
      index: i,
      filename: a.filename || `attachment_${i}`,
      size: a.size,
      contentType: a.contentType,
    })),
  };
}

async function readCachedMessages(client, folderPath, count, before = null, offset = 0) {
  const collections = cacheCollections();
  if (!collections) return null;
  const accountKey = stableAccountKey(client.account);
  const baseQuery = { accountKey };
  if (folderPath === VIRTUAL_UNREAD_FOLDER) {
    baseQuery.seen = false;
  } else if (folderPath !== VIRTUAL_ALL_FOLDER) {
    baseQuery.folder = folderPath;
  }
  const query = { ...baseQuery };
  if (before) query.seq = { $lt: before };
  const sortSpec = folderPath === VIRTUAL_ALL_FOLDER || folderPath === VIRTUAL_UNREAD_FOLDER ? { date: -1 } : { seq: -1 };
  const safeOffset = Math.max(parseInt(offset, 10) || 0, 0);
  const cursor = collections.messages
    .find(query)
    .sort(sortSpec);
  if (!before && safeOffset > 0) cursor.skip(safeOffset);
  const docs = await cursor.limit(count).toArray();
  const total = await collections.messages.countDocuments(baseQuery);
  const pageRemaining = await collections.messages.countDocuments(query);
  const unseen = await collections.messages.countDocuments({ accountKey, ...(baseQuery.folder ? { folder: baseQuery.folder } : {}), seen: false });
  const mails = docs.map(normalizeCacheMessage);
  if (folderPath !== VIRTUAL_ALL_FOLDER && folderPath !== VIRTUAL_UNREAD_FOLDER) {
    mails.sort((a, b) => new Date(b.date || 0) - new Date(a.date || 0));
  }
  return {
    total,
    unseen,
    mails,
    hasMore: before ? pageRemaining > mails.length : (safeOffset + mails.length) < total,
    cached: true,
    syncStatus: await readSyncStatus(client, folderPath),
  };
}

async function readCachedMessagesByAccountKeys(accountKeys, options = {}) {
  const collections = cacheCollections();
  if (!collections) return null;
  const keys = (Array.isArray(accountKeys) ? accountKeys : []).filter(Boolean);
  if (!keys.length) return { total: 0, unseen: 0, mails: [], hasMore: false, cached: true };
  const count = Math.min(Math.max(parseInt(options.count, 10) || 30, 1), 100);
  const offset = Math.max(parseInt(options.offset, 10) || 0, 0);
  const before = options.before ? new Date(options.before) : null;
  const query = { accountKey: { $in: keys } };
  if (options.unreadOnly) query.seen = false;
  if (before && Number.isFinite(before.getTime())) query.date = { $lt: before };
  const cursor = collections.messages
    .find(query)
    .sort({ date: -1 });
  if (!before && offset > 0) cursor.skip(offset);
  const docs = await cursor.limit(count).toArray();
  const baseQuery = { accountKey: { $in: keys }, ...(options.unreadOnly ? { seen: false } : {}) };
  const total = await collections.messages.countDocuments(baseQuery);
  const pageRemaining = await collections.messages.countDocuments(query);
  const unseen = await collections.messages.countDocuments({ accountKey: { $in: keys }, seen: false });
  return {
    total,
    unseen,
    mails: docs.map(normalizeCacheMessage),
    hasMore: before ? pageRemaining > docs.length : (offset + docs.length) < total,
    cached: true,
  };
}

async function searchCachedMessages(accountKeys, keyword, options = {}) {
  const collections = cacheCollections();
  if (!collections) return null;
  const keys = (Array.isArray(accountKeys) ? accountKeys : []).filter(Boolean);
  const q = String(keyword || '').trim();
  if (!keys.length || !q) return { total: 0, mails: [], cached: true };
  const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rx = new RegExp(escaped, 'i');
  const bodyMatches = await collections.bodies
    .find({ accountKey: { $in: keys }, $or: [{ text: rx }, { html: rx }, { from: rx }, { to: rx }, { subject: rx }] })
    .project({ accountKey: 1, folder: 1, uid: 1 })
    .limit(100)
    .toArray();
  const bodyKeys = bodyMatches.map(doc => ({
    accountKey: doc.accountKey,
    folder: doc.folder,
    uid: doc.uid,
  }));
  const query = {
    accountKey: { $in: keys },
    $or: [
      { subject: rx },
      { 'from.name': rx },
      { 'from.address': rx },
      { folderName: rx },
      ...bodyKeys.map(item => ({ accountKey: item.accountKey, folder: item.folder, uid: item.uid })),
    ],
  };
  if (options.unreadOnly) query.seen = false;
  const limit = Math.min(Math.max(parseInt(options.count, 10) || 50, 1), 100);
  const docs = await collections.messages.find(query).sort({ date: -1 }).limit(limit).toArray();
  const total = await collections.messages.countDocuments(query);
  return {
    total,
    mails: docs.map(normalizeCacheMessage),
    cached: true,
  };
}

async function readCachedMessageBody(client, folderPath, uid) {
  const collections = cacheCollections();
  if (!collections) return null;
  const doc = await collections.bodies.findOne({
    accountKey: stableAccountKey(client.account),
    folder: folderPath,
    uid: normalizeUid(uid),
  });
  return doc ? normalizeCachedBody(doc) : null;
}

async function readSyncStatus(client, folderPath) {
  const collections = cacheCollections();
  if (!collections) return null;
  const accountKey = stableAccountKey(client.account);
  const state = await collections.syncState.findOne({ accountKey, folder: folderPath });
  return normalizeSyncStatus(state);
}

async function readAccountSyncStatus(client) {
  return readSyncStatus(client, ACCOUNT_SYNC_STATE_FOLDER);
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

function primaryUnreadFolders(folders) {
  const selectable = (Array.isArray(folders) ? folders : []).filter(isSelectableFolder);
  const inbox = selectable.find(folder => String(folder.path || '').toLowerCase() === 'inbox')
    || selectable.find(folder => String(folder.name || '').toLowerCase() === 'inbox');
  return (inbox ? [inbox] : aggregateFolders(selectable)).slice(0, VIRTUAL_FOLDER_SCAN_LIMIT);
}

async function getSelectableFolders(client) {
  await client.ensureConnected();
  const folders = await client.client.list();
  return (Array.isArray(folders) ? folders : []).filter(isSelectableFolder);
}

async function resolveFolder(client, folderPath) {
  const folders = await getSelectableFolders(client);
  return folders.find(folder => String(folder.path) === String(folderPath))
    || folders.find(folder => String(folder.path || '').toLowerCase() === String(folderPath || '').toLowerCase())
    || { path: folderPath, name: folderPath };
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

function cacheDocFromMail(client, folder, mail) {
  const account = client.account;
  const accountEmail = account?.auth?.user || '';
  return {
    accountKey: stableAccountKey(account),
    accountEmail,
    provider: account?.name || '',
    host: account?.host || '',
    port: account?.port || 993,
    uid: normalizeUid(mail.uid),
    seq: Number(mail.seq) || 0,
    folder: folder?.path || mail.folder || '',
    folderName: folder?.name || mail.folderName || folder?.path || mail.folder || '',
    date: mail.date ? new Date(mail.date) : null,
    from: mail.from || { name: '', address: '(unknown)' },
    to: Array.isArray(mail.to) ? mail.to : [],
    subject: mail.subject || '(no subject)',
    seen: !!mail.seen,
    flagged: !!mail.flagged,
    updatedAt: new Date(),
  };
}

async function upsertCachedFolder(client, folder, status = {}) {
  const collections = cacheCollections();
  if (!collections || !folder?.path) return;
  const accountKey = stableAccountKey(client.account);
  await collections.folders.updateOne(
    { accountKey, path: folder.path },
    {
      $set: {
        accountKey,
        accountEmail: client.account?.auth?.user || '',
        path: folder.path,
        name: folder.name || folder.path,
        specialUse: folder.specialUse || '',
        messages: status.messages || 0,
        unseen: status.unseen || 0,
        updatedAt: new Date(),
      },
      $setOnInsert: { createdAt: new Date() },
    },
    { upsert: true },
  );
}

async function upsertCachedMessages(client, folder, mails) {
  const collections = cacheCollections();
  if (!collections || !Array.isArray(mails) || !mails.length) return;
  const ops = mails.filter(mail => mail.uid).map(mail => {
    const doc = cacheDocFromMail(client, folder, mail);
    return {
      updateOne: {
        filter: { accountKey: doc.accountKey, folder: doc.folder, uid: doc.uid },
        update: {
          $set: doc,
          $setOnInsert: { createdAt: new Date(), bodyCached: false },
        },
        upsert: true,
      },
    };
  });
  if (ops.length) await collections.messages.bulkWrite(ops, { ordered: false });
}

async function upsertCachedMessageBody(client, folderPath, uid, detail, options = {}) {
  const collections = cacheCollections();
  if (!collections) return;
  const account = client.account;
  const accountKey = stableAccountKey(account);
  const normalizedUid = normalizeUid(uid);
  const now = new Date();
  const seen = options.seen === undefined ? undefined : !!options.seen;
  await collections.bodies.updateOne(
    { accountKey, folder: folderPath, uid: normalizedUid },
    {
      $set: {
        accountKey,
        accountEmail: account?.auth?.user || '',
        provider: account?.name || '',
        host: account?.host || '',
        port: account?.port || 993,
        folder: folderPath,
        uid: normalizedUid,
        subject: detail.subject || '(no subject)',
        from: detail.from || '',
        to: detail.to || '',
        cc: detail.cc || '',
        date: detail.date ? new Date(detail.date) : null,
        text: detail.text || '',
        html: detail.html || '',
        attachments: Array.isArray(detail.attachments) ? detail.attachments : [],
        updatedAt: now,
      },
      $setOnInsert: { createdAt: now },
    },
    { upsert: true },
  );
  const messagePatch = {
    subject: detail.subject || '(no subject)',
    bodyCached: true,
    bodyCachedAt: now,
    updatedAt: now,
  };
  if (detail.date) messagePatch.date = new Date(detail.date);
  if (seen !== undefined) messagePatch.seen = seen;
  await collections.messages.updateOne(
    { accountKey, folder: folderPath, uid: normalizedUid },
    { $set: messagePatch },
  );
}

async function deleteCachedAccount(account) {
  const collections = cacheCollections();
  if (!collections || !account) return;
  const accountKey = stableAccountKey(account);
  await Promise.all([
    collections.folders.deleteMany({ accountKey }),
    collections.messages.deleteMany({ accountKey }),
    collections.bodies.deleteMany({ accountKey }),
    collections.syncState.deleteMany({ accountKey }),
  ]);
}

async function safeDeleteCachedAccount(account, label = '') {
  try {
    await deleteCachedAccount(account);
    return true;
  } catch (err) {
    console.warn(`Delete cached account failed${label ? ` (${label})` : ''}: ${err.message}`);
    return false;
  }
}

async function updateCachedMessageSeen(client, folderPath, uid, seen = true) {
  const collections = cacheCollections();
  if (!collections) return;
  await collections.messages.updateOne(
    { accountKey: stableAccountKey(client.account), folder: folderPath, uid: normalizeUid(uid) },
    { $set: { seen: !!seen, updatedAt: new Date() } },
  );
}

async function updateCachedMessagesFlag(client, folderPath, uids, patch) {
  const collections = cacheCollections();
  const normalized = normalizeUidList(uids);
  if (!collections || !normalized.length) return;
  await collections.messages.updateMany(
    { accountKey: stableAccountKey(client.account), folder: folderPath, uid: { $in: normalized } },
    { $set: { ...patch, updatedAt: new Date() } },
  );
}

async function deleteCachedMessages(client, folderPath, uids) {
  const collections = cacheCollections();
  const normalized = normalizeUidList(uids);
  if (!collections || !normalized.length) return;
  const filter = { accountKey: stableAccountKey(client.account), folder: folderPath, uid: { $in: normalized } };
  await Promise.all([
    collections.messages.deleteMany(filter),
    collections.bodies.deleteMany(filter),
  ]);
}

async function markRemoteMessageSeen(client, folderPath, uid) {
  await client.ensureConnected();
  const lock = await client.client.getMailboxLock(folderPath);
  try {
    await client.client.messageFlagsAdd(uid, ['\\Seen'], { uid: true });
    await updateCachedMessageSeen(client, folderPath, uid, true);
  } finally {
    lock.release();
  }
}

function backgroundMarkRemoteSeen(client, folderPath, uid) {
  markRemoteMessageSeen(client, folderPath, uid).catch(err => {
    console.warn(`Background mark seen failed ${client.account?.auth?.user || ''} ${folderPath}/${uid}: ${imapApiError(err, { folder: folderPath, command: 'STORE \\Seen' })}`);
  });
}

async function readMessageDetailFromRemote(client, folderPath, uid, options = {}) {
  await client.ensureConnected();
  const lock = await client.client.getMailboxLock(folderPath);
  try {
    const source = await client.client.download(String(uid), undefined, { uid: true });
    const parsed = await simpleParser(source.content);
    const detail = messageDetailFromParsed(parsed);

    if (options.markSeen) {
      try {
        await client.client.messageFlagsAdd(String(uid), ['\\Seen'], { uid: true });
      } catch (flagErr) {
        console.warn(`Mark seen failed ${client.account?.auth?.user || ''} ${folderPath}/${uid}: ${imapApiError(flagErr, { folder: folderPath, command: 'STORE \\Seen' })}`);
      }
    }

    await upsertCachedMessageBody(client, folderPath, uid, detail, options.markSeen ? { seen: true } : {});
    return detail;
  } finally {
    lock.release();
  }
}

async function prefetchCachedMessageBodies(client, folderPath, mails, limit = IMAP_CACHE_BODY_PREFETCH) {
  const collections = cacheCollections();
  if (!collections || !limit || isVirtualFolder(folderPath)) return;
  const accountKey = stableAccountKey(client.account);
  const candidates = (Array.isArray(mails) ? mails : [])
    .filter(mail => mail?.uid)
    .sort((a, b) => new Date(b.date || 0) - new Date(a.date || 0))
    .slice(0, limit);
  if (!candidates.length) return;

  const keys = candidates.map(mail => normalizeUid(mail.uid));
  const cached = await collections.bodies
    .find({ accountKey, folder: folderPath, uid: { $in: keys } })
    .project({ uid: 1 })
    .toArray();
  const cachedSet = new Set(cached.map(doc => normalizeUid(doc.uid)));
  const missing = candidates.filter(mail => !cachedSet.has(normalizeUid(mail.uid))).slice(0, limit);

  for (const mail of missing) {
    try {
      await readMessageDetailFromRemote(client, folderPath, mail.uid, { markSeen: false });
    } catch (err) {
      console.warn(`Prefetch body failed ${client.account?.auth?.user || ''} ${folderPath}/${mail.uid}: ${imapApiError(err, { folder: folderPath, command: 'PREFETCH_BODY' })}`);
    }
  }
}

function backgroundPrefetchCachedMessageBodies(client, folderPath, mails, limit = IMAP_CACHE_BODY_PREFETCH, purpose = 'visible') {
  if (!limit || isVirtualFolder(folderPath)) return;
  const key = `${stableAccountKey(client.account)}:${folderPath}:${purpose}`;
  if (bodyPrefetchJobs.has(key)) return;
  bodyPrefetchJobs.add(key);
  setImmediate(() => {
    prefetchCachedMessageBodies(client, folderPath, mails, limit)
      .catch(err => {
        console.warn(`Background body prefetch failed ${client.account?.auth?.user || ''} ${folderPath}: ${imapApiError(err, { folder: folderPath, command: 'PREFETCH_BODY' })}`);
      })
      .finally(() => {
        bodyPrefetchJobs.delete(key);
      });
  });
}

function backgroundPrefetchVisibleMessageBodies(client, mails, fallbackFolder, limit = IMAP_CACHE_BODY_PREFETCH) {
  if (!limit) return;
  const groups = new Map();
  let remaining = limit;
  for (const mail of Array.isArray(mails) ? mails : []) {
    if (!mail?.uid || remaining <= 0) break;
    const folderPath = mail.folder || fallbackFolder;
    if (!folderPath || isVirtualFolder(folderPath)) continue;
    if (!groups.has(folderPath)) groups.set(folderPath, []);
    groups.get(folderPath).push(mail);
    remaining -= 1;
  }
  for (const [folderPath, group] of groups) {
    backgroundPrefetchCachedMessageBodies(client, folderPath, group, group.length, 'visible');
  }
}

async function markSyncState(client, folderPath, patch) {
  const collections = cacheCollections();
  if (!collections) return;
  const accountKey = stableAccountKey(client.account);
  await collections.syncState.updateOne(
    { accountKey, folder: folderPath },
    {
      $set: {
        accountKey,
        accountEmail: client.account?.auth?.user || '',
        folder: folderPath,
        updatedAt: new Date(),
        ...patch,
      },
      $setOnInsert: { createdAt: new Date() },
    },
    { upsert: true },
  );
}

async function fetchMessagesByUid(client, folder, uids) {
  if (!uids.length) return [];
  const mails = [];
  for await (const msg of client.client.fetch(uids, {
    envelope: true,
    flags: true,
    uid: true,
  }, { uid: true })) {
    mails.push(mapEnvelopeMessage(msg, folder));
  }
  mails.sort((a, b) => new Date(b.date || 0) - new Date(a.date || 0));
  return mails;
}

async function pruneRecentCacheWindow(client, folder, liveMails, windowSize) {
  const collections = cacheCollections();
  if (!collections || !Array.isArray(liveMails) || !liveMails.length) return;
  const accountKey = stableAccountKey(client.account);
  const folderPath = folder?.path || '';
  if (!folderPath) return;
  const newest = liveMails
    .map(mail => ({ seq: Number(mail.seq) || 0, uid: normalizeUid(mail.uid) }))
    .filter(item => item.seq > 0 && item.uid !== '');
  if (!newest.length) return;
  const minSeq = Math.max(1, Math.min(...newest.map(item => item.seq)));
  const liveUidSet = new Set(newest.map(item => item.uid));
  const cached = await collections.messages
    .find({ accountKey, folder: folderPath, seq: { $gte: minSeq } })
    .project({ uid: 1 })
    .limit(Math.max(windowSize, newest.length) * 2)
    .toArray();
  const staleUids = cached.map(doc => doc.uid).filter(uid => !liveUidSet.has(uid));
  if (staleUids.length) {
    await deleteCachedMessages(client, folderPath, staleUids);
  }
}

async function syncFolderToCache(client, folder, options = {}) {
  const collections = cacheCollections();
  if (!collections || !folder?.path) return { synced: false, reason: 'cache-disabled' };
  const accountKey = stableAccountKey(client.account);
  const folderPath = folder.path;
  const existingState = await collections.syncState.findOne({ accountKey, folder: folderPath });
  if (!options.force && existingState?.syncing && Date.now() - new Date(existingState.updatedAt || 0).getTime() < 2 * 60 * 1000) {
    return { synced: false, reason: 'already-syncing' };
  }

  await markSyncState(client, folderPath, { syncing: true, error: '' });
  let result = null;
  let syncedMails = [];
  const lock = await client.client.getMailboxLock(folderPath);
  try {
    const status = selectedMailboxStatus(client, folderPath);
    const mailbox = client.client.mailbox || {};
    await upsertCachedFolder(client, folder, status);

    const uidValidity = mailbox.uidValidity ? String(mailbox.uidValidity) : '';
    const previousUidValidity = existingState?.uidValidity ? String(existingState.uidValidity) : '';
    const resetNeeded = previousUidValidity && uidValidity && previousUidValidity !== uidValidity;
    if (resetNeeded) {
      await Promise.all([
        collections.messages.deleteMany({ accountKey, folder: folderPath }),
        collections.bodies.deleteMany({ accountKey, folder: folderPath }),
      ]);
    }

    const highestUid = resetNeeded ? 0 : Number(existingState?.highestUid || 0);
    let newUids = [];
    if (highestUid > 0) {
      newUids = await client.client.search({ uid: `${highestUid + 1}:*` }, { uid: true });
    }

    let newestUids = [];
    if (status.messages > 0) {
      const endSeq = status.messages;
      const startSeq = Math.max(1, endSeq - Math.max(options.window || IMAP_CACHE_SYNC_WINDOW, IMAP_CACHE_LOOKBACK) + 1);
      for await (const msg of client.client.fetch(`${startSeq}:${endSeq}`, { uid: true, flags: true })) {
        if (msg.uid) newestUids.push(Number(msg.uid));
      }
    }

    const selectedUids = Array.from(new Set([...newUids.map(Number), ...newestUids]))
      .filter(Boolean)
      .sort((a, b) => a - b)
      .slice(-Math.max(options.window || IMAP_CACHE_SYNC_WINDOW, IMAP_CACHE_LOOKBACK));
    const mails = await fetchMessagesByUid(client, folder, selectedUids);
    syncedMails = mails;
    await upsertCachedMessages(client, folder, mails);
    await pruneRecentCacheWindow(client, folder, mails, Math.max(options.window || IMAP_CACHE_SYNC_WINDOW, IMAP_CACHE_LOOKBACK));

    const nextHighestUid = Math.max(highestUid, ...selectedUids, 0);
    await markSyncState(client, folderPath, {
      syncing: false,
      syncedAt: new Date(),
      error: '',
      messages: status.messages || 0,
      unseen: status.unseen || 0,
      highestUid: nextHighestUid,
      uidValidity,
      uidNext: mailbox.uidNext || 0,
      highestModseq: mailbox.highestModseq ? String(mailbox.highestModseq) : '',
    });
    result = { synced: true, count: mails.length, total: status.messages || 0, unseen: status.unseen || 0 };
  } catch (err) {
    await markSyncState(client, folderPath, {
      syncing: false,
      error: imapApiError(err, { folder: folderPath, command: 'SYNC' }),
    });
    throw err;
  } finally {
    lock.release();
  }

  if (result?.synced && syncedMails.length) {
    backgroundPrefetchCachedMessageBodies(client, folderPath, syncedMails, syncedMails.length, 'sync');
  }
  return result;
}

function backgroundSyncFolder(client, folder, options = {}) {
  syncFolderToCache(client, folder, options).catch(err => {
    console.warn(`Background sync failed ${client.account?.auth?.user || ''} ${folder?.path || ''}: ${imapApiError(err, { folder: folder?.path, command: 'SYNC' })}`);
  });
}

async function syncVirtualFolderToCache(client, folderPath, options = {}) {
  await client.ensureConnected();
  const listedFolders = await client.client.list();
  const folders = folderPath === VIRTUAL_UNREAD_FOLDER
    ? primaryUnreadFolders(listedFolders)
    : aggregateFolders(listedFolders);
  const results = [];
  for (const folder of folders) {
    try {
      results.push(await syncFolderToCache(client, folder, options));
    } catch (err) {
      results.push({ synced: false, error: imapApiError(err, { folder: folder.path, command: 'SYNC' }) });
    }
  }
  const accountKey = stableAccountKey(client.account);
  const baseQuery = folderPath === VIRTUAL_UNREAD_FOLDER
    ? { accountKey, seen: false }
    : { accountKey };
  const messages = await cacheCollections()?.messages.countDocuments(baseQuery) || 0;
  const unseen = await cacheCollections()?.messages.countDocuments({ accountKey, seen: false }) || 0;
  await markSyncState(client, folderPath, {
    syncing: false,
    syncedAt: new Date(),
    error: '',
    messages,
    unseen,
  });
  return { synced: true, virtual: folderPath, results };
}

function backgroundSyncVirtualFolder(client, folderPath, options = {}) {
  syncVirtualFolderToCache(client, folderPath, options).catch(err => {
    console.warn(`Background virtual sync failed ${client.account?.auth?.user || ''}: ${imapApiError(err, { folder: folderPath, command: 'SYNC' })}`);
  });
}

function syncJobKey(client) {
  return stableAccountKey(client.account);
}

async function syncAccountToCache(client, options = {}) {
  const collections = cacheCollections();
  if (!collections) return { synced: false, reason: 'cache-disabled' };
  const key = syncJobKey(client);
  if (accountSyncJobs.has(key)) {
    return accountSyncJobs.get(key);
  }
  const job = (async () => {
    const accountState = await readAccountSyncStatus(client);
    const periodicSync = options.periodic === true;
    if (!options.force && !periodicSync && accountState && !accountState.stale && accountState.synced) {
      return { synced: false, reason: 'fresh', syncStatus: accountState };
    }

    await markSyncState(client, ACCOUNT_SYNC_STATE_FOLDER, { syncing: true, error: '' });
    try {
      await client.ensureConnected();
      const folders = aggregateFolders(await client.client.list());
      const results = [];
      for (const folder of folders) {
        try {
          results.push(await syncFolderToCache(client, folder, options));
        } catch (err) {
          results.push({
            folder: folder.path,
            synced: false,
            error: imapApiError(err, { folder: folder.path, command: 'SYNC' }),
          });
        }
      }
      const accountKey = stableAccountKey(client.account);
      const messages = await collections.messages.countDocuments({ accountKey });
      const unseen = await collections.messages.countDocuments({ accountKey, seen: false });
      const hasErrors = results.some(result => result.error);
      await markSyncState(client, ACCOUNT_SYNC_STATE_FOLDER, {
        syncing: false,
        syncedAt: new Date(),
        error: hasErrors ? results.filter(result => result.error).map(result => `${result.folder}: ${result.error}`).slice(0, 3).join('；') : '',
        messages,
        unseen,
        folders: folders.length,
      });
      await markSyncState(client, VIRTUAL_ALL_FOLDER, {
        syncing: false,
        syncedAt: new Date(),
        error: '',
        messages,
        unseen,
      });
      await markSyncState(client, VIRTUAL_UNREAD_FOLDER, {
        syncing: false,
        syncedAt: new Date(),
        error: '',
        messages: unseen,
        unseen,
      });
      return { synced: true, messages, unseen, folders: folders.length, results };
    } catch (err) {
      const error = imapApiError(err, { command: 'ACCOUNT_SYNC' });
      await markSyncState(client, ACCOUNT_SYNC_STATE_FOLDER, { syncing: false, error });
      throw err;
    }
  })().finally(() => {
    accountSyncJobs.delete(key);
  });
  accountSyncJobs.set(key, job);
  return job;
}

function backgroundSyncAccount(client, options = {}) {
  syncAccountToCache(client, options).catch(err => {
    console.warn(`Background account sync failed ${client.account?.auth?.user || ''}: ${imapApiError(err, { command: 'ACCOUNT_SYNC' })}`);
  });
}

async function markAccountSyncQueued(client) {
  const current = await readAccountSyncStatus(client);
  if (current?.syncing || (current?.synced && !current.stale)) return current;
  await markSyncState(client, ACCOUNT_SYNC_STATE_FOLDER, { syncing: true, error: '' });
  return readAccountSyncStatus(client);
}

async function accountSummaryWithSync(id, account, extra = {}) {
  const syncStatus = cacheDb ? await readAccountSyncStatus({ account }) : null;
  return accountSummary(id, account, { syncStatus, ...extra });
}

function accountList() {
  const list = [];
  clients.forEach((c, id) => {
    list.push(accountSummary(id, c.account));
  });
  return list;
}

async function accountListWithSync() {
  const list = [];
  for (const [id, c] of clients) {
    list.push(await accountSummaryWithSync(id, c.account));
  }
  return list;
}

async function runScheduledSync({ force = false } = {}) {
  if (!IMAP_SYNC_SCHEDULER_ENABLED || syncSchedulerRunning) return;
  syncSchedulerRunning = true;
  try {
    for (const client of clients.values()) {
      backgroundSyncAccount(client, { force, periodic: true, window: IMAP_CACHE_SYNC_WINDOW });
    }
  } finally {
    syncSchedulerRunning = false;
  }
}

function startSyncScheduler() {
  if (!IMAP_SYNC_SCHEDULER_ENABLED || syncSchedulerTimer) return;
  setTimeout(() => runScheduledSync().catch(err => {
    console.warn(`Initial IMAP sync scheduling failed: ${err.message}`);
  }), IMAP_SYNC_STARTUP_DELAY_MS);
  syncSchedulerTimer = setInterval(() => {
    runScheduledSync().catch(err => {
      console.warn(`IMAP sync scheduling failed: ${err.message}`);
    });
  }, IMAP_SYNC_CHECK_INTERVAL_MS);
}

function stopSyncScheduler() {
  if (syncSchedulerTimer) {
    clearInterval(syncSchedulerTimer);
    syncSchedulerTimer = null;
  }
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
  const listedFolders = await client.client.list();
  const folders = unreadOnly ? primaryUnreadFolders(listedFolders) : aggregateFolders(listedFolders);
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
  const pendingConnects = [];
  for (const item of data) {
    const account = deserializeAccount(item);
    if (!account) {
      console.log('  ✗ 跳过账户：未配置 IMAP_ACCOUNTS_SECRET/APP_SECRET 或密码无法解密');
      continue;
    }
    const client = createMailClient(account);
    const persistedId = parseInt(item?.id, 10);
    const id = Number.isInteger(persistedId) && persistedId > 0 ? persistedId : ++clientId;
    clientId = Math.max(clientId, id);
    clients.set(id, client);
    pendingConnects.push((async () => {
      try {
        await client.connect();
        console.log(`  ✓ ${account.auth.user} 已恢复`);
      } catch (err) {
        console.log(`  ✗ ${account.auth.user} 暂未连接，已保留账号: ${err.message}`);
      }
    })());
  }
  // 重写持久化文件，迁移旧的明文格式；连接失败的账号仍保留。
  saveAccounts();
  Promise.allSettled(pendingConnects).then(() => {
    console.log('已保存账户后台恢复任务完成');
  });
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
    backgroundSyncAccount(client, { force: true, window: IMAP_CACHE_SYNC_WINDOW });
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
    backgroundSyncAccount(client, { force: true, window: IMAP_CACHE_SYNC_WINDOW });
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
    backgroundSyncAccount(client, { force: true, window: IMAP_CACHE_SYNC_WINDOW });
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
app.get('/api/accounts', async (req, res) => {
  try {
    if (!cacheDb) {
      return res.json(accountList());
    }
    res.json(await accountListWithSync());
  } catch (err) {
    console.warn(`账户同步状态加载失败，降级返回账号列表: ${err.message}`);
    res.json(accountList());
  }
});

// 账户排序
app.post('/api/accounts/reorder', (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  reorderAccounts(req.body?.order || []);
  res.json({ ok: true, accounts: accountList() });
});

app.get('/api/accounts/:id/sync/status', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });
  try {
    const accountStatus = await readAccountSyncStatus(client);
    const folder = req.query.folder ? String(req.query.folder) : '';
    const folderStatus = folder ? await readSyncStatus(client, folder) : null;
    res.json({ ok: true, account: accountStatus, folder: folderStatus });
  } catch (err) {
    res.status(500).json({ error: err.message || '同步状态读取失败' });
  }
});

app.post('/api/accounts/:id/sync', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });
  const folder = String(req.body?.folder || req.query.folder || '').trim();
  const force = ['1', 'true', 'yes'].includes(String(req.body?.force ?? req.query.force ?? '1').toLowerCase());
  const background = ['1', 'true', 'yes'].includes(String(req.body?.background ?? req.query.background ?? '0').toLowerCase());
  try {
    if (background && !folder) {
      const account = await markAccountSyncQueued(client);
      backgroundSyncAccount(client, { force, window: IMAP_CACHE_SYNC_WINDOW });
      return res.json({ ok: true, background: true, queued: true, account });
    }

    if (background && folder) {
      if (isVirtualFolder(folder)) {
        await markSyncState(client, folder, { syncing: true, error: '' });
        backgroundSyncVirtualFolder(client, folder, { force, window: IMAP_CACHE_SYNC_WINDOW });
      } else {
        const resolvedFolder = await resolveFolder(client, folder);
        await markSyncState(client, resolvedFolder.path, { syncing: true, error: '' });
        backgroundSyncFolder(client, resolvedFolder, { force, window: IMAP_CACHE_SYNC_WINDOW });
      }
      return res.json({ ok: true, background: true, queued: true, folder: await readSyncStatus(client, folder) });
    }

    let result;
    if (folder) {
      if (isVirtualFolder(folder)) result = await syncVirtualFolderToCache(client, folder, { force, window: IMAP_CACHE_SYNC_WINDOW });
      else result = await syncFolderToCache(client, await resolveFolder(client, folder), { force, window: IMAP_CACHE_SYNC_WINDOW });
    } else {
      result = await syncAccountToCache(client, { force, window: IMAP_CACHE_SYNC_WINDOW });
    }
    res.json({ ok: true, result, account: await readAccountSyncStatus(client), folder: folder ? await readSyncStatus(client, folder) : null });
  } catch (err) {
    res.status(500).json({ error: imapApiError(err, { folder, command: folder ? 'SYNC_FOLDER' : 'ACCOUNT_SYNC' }) });
  }
});

app.post('/api/accounts/sync', async (req, res) => {
  const force = ['1', 'true', 'yes'].includes(String(req.body?.force ?? req.query.force ?? '1').toLowerCase());
  const background = ['1', 'true', 'yes'].includes(String(req.body?.background ?? req.query.background ?? '0').toLowerCase());
  const accountIds = String(req.body?.accountIds || req.query.accountIds || '')
    .split(',')
    .map(value => parseInt(value, 10))
    .filter(id => Number.isInteger(id) && clients.has(id));
  try {
    const selectedEntries = accountIds.length
      ? accountIds.map(id => [id, clients.get(id)])
      : Array.from(clients.entries());
    if (background) {
      const results = [];
      for (const [id, client] of selectedEntries) {
        const account = await markAccountSyncQueued(client);
        backgroundSyncAccount(client, { force, window: IMAP_CACHE_SYNC_WINDOW });
        results.push({ id, ok: true, queued: true, account });
      }
      return res.json({ ok: true, background: true, results, accounts: await accountListWithSync() });
    }
    const results = [];
    for (const [id, client] of selectedEntries) {
      try {
        const result = await syncAccountToCache(client, { force, window: IMAP_CACHE_SYNC_WINDOW });
        results.push({
          id,
          ok: true,
          result,
          account: await readAccountSyncStatus(client),
        });
      } catch (err) {
        results.push({
          id,
          ok: false,
          error: imapApiError(err, { command: 'ACCOUNT_SYNC' }),
          account: await readAccountSyncStatus(client),
        });
      }
    }
    res.json({ ok: true, results, accounts: await accountListWithSync() });
  } catch (err) {
    res.status(500).json({ error: err.message || '批量同步失败' });
  }
});

// 仅更新账户备注类元数据，不重新登录 IMAP。
app.patch('/api/accounts/:id/metadata', async (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  const id = parseInt(req.params.id, 10);
  const client = clients.get(id);
  if (!client) return res.status(404).json({ error: '账户不存在' });
  if (req.body?.displayName !== undefined) {
    client.account.displayName = normalizeDisplayName(req.body.displayName, client.account.auth?.user || '');
  }
  if (req.body?.sendName !== undefined) {
    client.account.sendName = normalizeSendName(req.body.sendName);
  }
  if (req.body?.group !== undefined) {
    client.account.group = normalizeAccountGroup(req.body.group);
  }
  saveAccounts();
  res.json(accountSummary(id, client.account));
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
    const oldAccountKey = stableAccountKey(client.account);
    const newAccountKey = stableAccountKey(account);
    if (oldAccountKey !== newAccountKey) {
      await safeDeleteCachedAccount(client.account, account.auth?.user || '');
    }
    try { await client.disconnect(); } catch {}
    clients.set(id, updatedClient);
    saveAccounts();
    backgroundSyncAccount(updatedClient, { force: true, window: IMAP_CACHE_SYNC_WINDOW });
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
  await safeDeleteCachedAccount(client.account, client.account?.auth?.user || '');
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
  const page = Math.max(parseInt(req.query.page, 10) || 1, 1);
  const offset = Math.max(parseInt(req.query.offset, 10) || ((page - 1) * count), 0);
  const before = req.query.before ? parseInt(req.query.before, 10) : null;
  const forceSync = ['1', 'true', 'yes'].includes(String(req.query.sync || '').toLowerCase());
  const cacheOnly = ['1', 'true', 'yes'].includes(String(req.query.cacheOnly || '').toLowerCase());

  try {
    const cached = await readCachedMessages(client, folder, count, before, offset);
    const needsInitialSync = cached && !forceSync && !cacheOnly && !before && offset === 0 && !cached.syncStatus?.synced && cached.mails.length === 0;
    const needsOlderRemotePage = cached && !forceSync && !cacheOnly && before && cached.mails.length < count
      && folder !== VIRTUAL_ALL_FOLDER && folder !== VIRTUAL_UNREAD_FOLDER
      && Number(cached.syncStatus?.messages || 0) > Number(cached.total || 0);
    if (cached && !forceSync && !needsInitialSync && !needsOlderRemotePage) {
      if (!cacheOnly && (!cached.syncStatus || cached.syncStatus.stale) && !before) {
        if (isVirtualFolder(folder)) backgroundSyncVirtualFolder(client, folder, { window: count });
        else resolveFolder(client, folder).then(resolved => backgroundSyncFolder(client, resolved, { window: count })).catch(() => {});
      }
      if (!cacheOnly) backgroundPrefetchVisibleMessageBodies(client, cached.mails, folder);
      return res.json(cached);
    }

    await client.ensureConnected();
    if (isVirtualFolder(folder)) {
      if (forceSync || needsInitialSync) {
        await syncVirtualFolderToCache(client, folder, { force: true, window: count });
        const nextCached = await readCachedMessages(client, folder, count, before, offset);
        if (nextCached && (nextCached.mails.length || folder !== VIRTUAL_UNREAD_FOLDER)) return res.json({ ...nextCached, syncedNow: true });
      }
      return res.json(await fetchVirtualFolderMessages(client, folder, count));
    }

    if (forceSync || needsInitialSync) {
      const resolvedFolder = await resolveFolder(client, folder);
      await syncFolderToCache(client, resolvedFolder, { force: true, window: count });
      const nextCached = await readCachedMessages(client, folder, count, before, offset);
      if (nextCached) return res.json({ ...nextCached, syncedNow: true });
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
      await upsertCachedMessages(client, { path: folder, name: folder }, mails);
      backgroundPrefetchVisibleMessageBodies(client, mails, folder);
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
  const forceSync = ['1', 'true', 'yes'].includes(String(req.query.sync || req.query.refresh || '').toLowerCase());
  const markSeen = !['0', 'false', 'no'].includes(String(req.query.markSeen ?? '1').toLowerCase());

  try {
    const cached = forceSync ? null : await readCachedMessageBody(client, folder, uid);
    if (cached) {
      if (markSeen) {
        updateCachedMessageSeen(client, folder, uid, true).catch(() => {});
        backgroundMarkRemoteSeen(client, folder, uid);
      }
      return res.json(cached);
    }

    const detail = await readMessageDetailFromRemote(client, folder, uid, { markSeen });
    res.json({ ...detail, cached: false });
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

app.get('/api/mails', async (req, res) => {
  const count = parseInt(req.query.count, 10) || 30;
  const page = Math.max(parseInt(req.query.page, 10) || 1, 1);
  const offset = Math.max(parseInt(req.query.offset, 10) || ((page - 1) * count), 0);
  const before = req.query.before || '';
  const unreadOnly = ['1', 'true', 'yes'].includes(String(req.query.unread || '').toLowerCase());
  const accountIds = String(req.query.accountIds || '')
    .split(',')
    .map(value => parseInt(value, 10))
    .filter(id => Number.isInteger(id) && clients.has(id));
  try {
    const selectedClients = accountIds.length ? accountIds.map(id => clients.get(id)) : Array.from(clients.values());
    const keys = selectedClients.map(client => stableAccountKey(client.account));
    const data = await readCachedMessagesByAccountKeys(keys, { count, offset, before, unreadOnly });
    const payload = data || { total: 0, unseen: 0, mails: [], hasMore: false, cached: true };
    payload.mails = attachCurrentAccountIds(payload.mails);
    res.json(payload);
  } catch (err) {
    res.status(500).json({ error: err.message || '缓存邮件读取失败' });
  }
});

app.get('/api/search', async (req, res) => {
  const keyword = String(req.query.q || '').trim();
  const accountId = req.query.accountId ? parseInt(req.query.accountId, 10) : null;
  const accountIds = String(req.query.accountIds || '')
    .split(',')
    .map(value => parseInt(value, 10))
    .filter(id => Number.isInteger(id) && clients.has(id));
  const unreadOnly = ['1', 'true', 'yes'].includes(String(req.query.unread || '').toLowerCase());
  if (!keyword) return res.json({ total: 0, mails: [], cached: true });
  try {
    const selectedClients = Number.isInteger(accountId) && clients.has(accountId)
      ? [clients.get(accountId)]
      : accountIds.length ? accountIds.map(id => clients.get(id)) : Array.from(clients.values());
    const keys = selectedClients.map(client => stableAccountKey(client.account));
    const data = await searchCachedMessages(keys, keyword, { count: parseInt(req.query.count, 10) || 50, unreadOnly });
    const payload = data || { total: 0, mails: [], cached: true };
    payload.mails = attachCurrentAccountIds(payload.mails);
    res.json(payload);
  } catch (err) {
    res.status(500).json({ error: err.message || '缓存搜索失败' });
  }
});

// 搜索邮件
app.get('/api/accounts/:id/search', async (req, res) => {
  const client = clients.get(parseInt(req.params.id, 10));
  if (!client) return res.status(404).json({ error: '账户不存在' });

  const folder = req.query.folder || 'INBOX';
  const keyword = req.query.q || '';
  const field = req.query.field || 'subject';
  const count = Math.min(200, Math.max(1, parseInt(req.query.count, 10) || 50));
  const offset = Math.max(0, parseInt(req.query.offset, 10) || ((Math.max(1, parseInt(req.query.page, 10) || 1) - 1) * count));
  if (!keyword) return res.status(400).json({ error: '请输入搜索关键词' });

  try {
    const cached = await searchCachedMessages([stableAccountKey(client.account)], keyword, { count: offset + count });
    if (cached && cached.mails.length) {
      return res.json({
        mails: attachCurrentAccountIds(cached.mails).slice(offset, offset + count),
        total: cached.total || cached.mails.length,
        offset,
        count,
      });
    }

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
      if (uids.length === 0) return res.json({ mails: [], total: 0, offset, count });

      const mails = [];
      for await (const msg of client.client.fetch(uids.slice(offset, offset + count), {
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
      res.json({ mails, total: uids.length, offset, count });
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
      await deleteCachedMessages(client, folder, uid);
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
  const to = normalizeAddressList(req.body?.to);
  const cc = normalizeAddressList(req.body?.cc);
  const bcc = normalizeAddressList(req.body?.bcc);
  const subject = String(req.body?.subject || '').trim();
  const text = String(req.body?.text || '').trim();
  const html = String(req.body?.html || '').trim();
  const requestedFromName = String(req.body?.fromName || '').trim();
  const attachments = normalizeSendAttachments(req.body?.attachments);
  if (!to.length) return res.status(400).json({ error: '请填写收件人' });
  if (!subject) return res.status(400).json({ error: '请填写主题' });
  if (!text && !html) return res.status(400).json({ error: '请填写邮件正文' });

  const account = client.account;
  const fromEmail = account.auth?.user || '';
  const fromName = normalizeSendName(requestedFromName)
    || normalizeSendName(account.sendName)
    || normalizeDisplayName(fromEmail.split('@')[0], fromEmail);
  const mailOptions = {
    from: fromName && fromName !== fromEmail ? `${fromName} <${fromEmail}>` : fromEmail,
    to,
    cc,
    bcc,
    subject,
    text,
    html,
    replyTo: fromEmail,
    attachments,
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
      const patch = {};
      if (safeFlags.includes('\\Seen')) {
        if (action === 'add') patch.seen = true;
        else if (action === 'remove') patch.seen = false;
      }
      if (safeFlags.includes('\\Flagged')) {
        if (action === 'add') patch.flagged = true;
        else if (action === 'remove') patch.flagged = false;
      }
      if (action === 'set') {
        patch.seen = safeFlags.includes('\\Seen');
        patch.flagged = safeFlags.includes('\\Flagged');
      }
      if (Object.keys(patch).length) {
        await updateCachedMessagesFlag(client, folder, uid, patch);
      }
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
      await deleteCachedMessages(client, folder, uid);
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
          await deleteCachedMessages(client, folder, uids);
          break;
        case 'read':
          await client.client.messageFlagsAdd(uidRange, ['\\Seen'], { uid: true });
          await updateCachedMessagesFlag(client, folder, uids, { seen: true });
          break;
        case 'unread':
          await client.client.messageFlagsRemove(uidRange, ['\\Seen'], { uid: true });
          await updateCachedMessagesFlag(client, folder, uids, { seen: false });
          break;
        case 'flag':
          await client.client.messageFlagsAdd(uidRange, ['\\Flagged'], { uid: true });
          await updateCachedMessagesFlag(client, folder, uids, { flagged: true });
          break;
        case 'unflag':
          await client.client.messageFlagsRemove(uidRange, ['\\Flagged'], { uid: true });
          await updateCachedMessagesFlag(client, folder, uids, { flagged: false });
          break;
        case 'move':
          if (!destination) return res.status(400).json({ error: '移动操作需要 destination 参数' });
          await client.client.messageMove(uidRange, destination, { uid: true });
          await deleteCachedMessages(client, folder, uids);
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
initCacheDb().catch(err => {
  console.warn(`IMAP cache init failed, running without persistent mail cache: ${err.message}`);
}).then(() => {
  app.listen(PORT, () => {
    console.log(`IMAP Mail Client 已启动: http://localhost:${PORT}`);
    restoreAccounts().catch(err => {
      console.warn(`IMAP account restore failed: ${err.message}`);
    });
    startSyncScheduler();
  });
});

async function shutdown() {
  try {
    for (const client of clients.values()) {
      try { await client.disconnect(); } catch {}
    }
    stopSyncScheduler();
    await closeCacheDb();
  } finally {
    process.exit(0);
  }
}

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

app.use((err, req, res, next) => {
  console.error(`IMAP API 未处理错误 ${req.method} ${req.originalUrl}:`, err);
  if (res.headersSent) {
    return next(err);
  }
  return res.status(500).json({ error: err.message || 'IMAP 服务内部错误' });
});
