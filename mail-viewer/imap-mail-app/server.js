const express = require('express');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { simpleParser } = require('mailparser');
const MailClient = require('./client');
const { fromPreset, PRESETS, autoDetect } = require('./config');
const { prepareHtmlForRender } = require('./sanitize');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// 存储已连接的客户端
const clients = new Map();
let clientId = 0;

// 持久化文件路径
const ACCOUNTS_FILE = process.env.ACCOUNTS_FILE || path.join(__dirname, 'accounts.json');
const ACCOUNTS_SECRET = process.env.IMAP_ACCOUNTS_SECRET || process.env.SECRET_KEY || '';
const PUBLIC_BASE_URL = (process.env.PUBLIC_BASE_URL || '').replace(/\/+$/, '');
const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID || '';
const GOOGLE_CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET || '';
const GOOGLE_REDIRECT_URI = process.env.GOOGLE_REDIRECT_URI || (PUBLIC_BASE_URL ? `${PUBLIC_BASE_URL}/api/oauth/gmail/callback` : '');
const GOOGLE_SCOPE = 'https://mail.google.com/';
const oauthStates = new Map();
if (!ACCOUNTS_SECRET) {
  console.warn('WARNING: IMAP_ACCOUNTS_SECRET is not configured; external IMAP accounts cannot be persisted.');
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
  return Boolean(GOOGLE_CLIENT_ID && GOOGLE_CLIENT_SECRET && GOOGLE_REDIRECT_URI);
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
  const token = await exchangeGoogleToken({
    client_id: GOOGLE_CLIENT_ID,
    client_secret: GOOGLE_CLIENT_SECRET,
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
  res.status(500).json({ error: '未配置 IMAP_ACCOUNTS_SECRET，无法持久化保存外部邮箱账号' });
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
      console.log('  ✗ 跳过账户：未配置 IMAP_ACCOUNTS_SECRET 或密码无法解密');
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
  res.json({
    enabled: hasGmailOAuthConfig(),
    redirectUri: GOOGLE_REDIRECT_URI,
    scope: GOOGLE_SCOPE,
  });
});

app.post('/api/oauth/gmail/start', (req, res) => {
  if (!requirePersistenceSecret(res)) return;
  if (!hasGmailOAuthConfig()) {
    return res.status(400).json({ error: 'Gmail OAuth 未配置，请设置 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI' });
  }
  const state = crypto.randomBytes(24).toString('hex');
  oauthStates.set(state, { createdAt: Date.now() });
  const params = new URLSearchParams({
    client_id: GOOGLE_CLIENT_ID,
    redirect_uri: GOOGLE_REDIRECT_URI,
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
    return res.status(500).send('IMAP_ACCOUNTS_SECRET is not configured; external IMAP accounts cannot be persisted.');
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
    const token = await exchangeGoogleToken({
      client_id: GOOGLE_CLIENT_ID,
      client_secret: GOOGLE_CLIENT_SECRET,
      code,
      grant_type: 'authorization_code',
      redirect_uri: GOOGLE_REDIRECT_URI,
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
  const { preset, host, port, email, password } = req.body;
  if (!email || !password) {
    return res.status(400).json({ error: '请填写邮箱和密码' });
  }

  let account;
  if (preset && preset !== 'custom') {
    try {
      account = fromPreset(preset, email, password);
    } catch (e) {
      return res.status(400).json({ error: e.message });
    }
  } else {
    if (!host) return res.status(400).json({ error: '自定义配置需要填写服务器地址' });
    account = {
      name: email.split('@')[1] || 'custom',
      host,
      port: parseInt(port, 10) || 993,
      secure: true,
      auth: { user: email, pass: password },
    };
  }

  // 检查是否已连接同一邮箱
  for (const [existingId, existing] of clients) {
    if (existing.account.auth.user === email) {
      return res.json({ id: existingId, name: existing.account.name, email, exists: true });
    }
  }

  const client = createMailClient(account);
  try {
    await client.connect();
    const id = ++clientId;
    clients.set(id, client);
    saveAccounts();
    res.json({ id, name: account.name, email: account.auth.user });
  } catch (err) {
    res.status(500).json({ error: `连接失败: ${err.message}` });
  }
});

// 已连接账户列表
app.get('/api/accounts', (req, res) => {
  const list = [];
  clients.forEach((c, id) => {
    list.push({ id, name: c.account.name, email: c.account.auth.user });
  });
  res.json(list);
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
        results.push({ id: existingId, email, ok: true, exists: true });
        alreadyExists = true;
        break;
      }
    }
    if (alreadyExists) continue;

    try {
      const account = autoDetect(email, password);
      const client = createMailClient(account);
      await client.connect();
      const id = ++clientId;
      clients.set(id, client);
      results.push({ id, email, ok: true });
    } catch (err) {
      results.push({ email, ok: false, error: err.message });
    }
  }

  if (results.some(r => r.ok)) saveAccounts();
  res.json(results);
});

const PORT = process.env.PORT || 3939;

restoreAccounts().then(() => {
  app.listen(PORT, () => {
    console.log(`IMAP Mail Client 已启动: http://localhost:${PORT}`);
  });
});
