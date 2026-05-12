require('dotenv').config();

function provider({ imap, smtp, domains = [], aliases = [] }) {
  return {
    ...imap,
    smtp: smtp || null,
    domains,
    aliases,
  };
}

// Common IMAP/SMTP presets. Receiving mail always requires IMAP; SMTP is checked
// only to give useful diagnostics and to prepare for future send support.
const PRESETS = {
  gmail: provider({
    imap: { host: 'imap.gmail.com', port: 993, secure: true },
    smtp: { host: 'smtp.gmail.com', port: 465, secure: true },
    domains: ['gmail.com', 'googlemail.com'],
  }),
  outlook: provider({
    imap: { host: 'outlook.office365.com', port: 993, secure: true },
    smtp: { host: 'smtp-mail.outlook.com', port: 587, secure: false, requireTLS: true },
    domains: ['outlook.com', 'hotmail.com', 'live.com', 'msn.com'],
    aliases: ['hotmail', 'live'],
  }),
  office365: provider({
    imap: { host: 'outlook.office365.com', port: 993, secure: true },
    smtp: { host: 'smtp.office365.com', port: 587, secure: false, requireTLS: true },
  }),
  qq: provider({
    imap: { host: 'imap.qq.com', port: 993, secure: true },
    smtp: { host: 'smtp.qq.com', port: 465, secure: true },
    domains: ['qq.com', 'foxmail.com'],
    aliases: ['foxmail'],
  }),
  '163': provider({
    imap: { host: 'imap.163.com', port: 993, secure: true },
    smtp: { host: 'smtp.163.com', port: 465, secure: true },
    domains: ['163.com'],
  }),
  '126': provider({
    imap: { host: 'imap.126.com', port: 993, secure: true },
    smtp: { host: 'smtp.126.com', port: 465, secure: true },
    domains: ['126.com'],
  }),
  yeah: provider({
    imap: { host: 'imap.yeah.net', port: 993, secure: true },
    smtp: { host: 'smtp.yeah.net', port: 465, secure: true },
    domains: ['yeah.net'],
  }),
  aliyun: provider({
    imap: { host: 'imap.aliyun.com', port: 993, secure: true },
    smtp: { host: 'smtp.aliyun.com', port: 465, secure: true },
    domains: ['aliyun.com'],
    aliases: ['alibaba'],
  }),
  '139': provider({
    imap: { host: 'imap.139.com', port: 993, secure: true },
    smtp: { host: 'smtp.139.com', port: 465, secure: true },
    domains: ['139.com'],
  }),
  sohu: provider({
    imap: { host: 'imap.sohu.com', port: 993, secure: true },
    smtp: { host: 'smtp.sohu.com', port: 465, secure: true },
    domains: ['sohu.com'],
  }),
  sina: provider({
    imap: { host: 'imap.sina.com', port: 993, secure: true },
    smtp: { host: 'smtp.sina.com', port: 465, secure: true },
    domains: ['sina.com', 'sina.cn'],
  }),
  tom: provider({
    imap: { host: 'imap.tom.com', port: 993, secure: true },
    smtp: { host: 'smtp.tom.com', port: 465, secure: true },
    domains: ['tom.com'],
  }),
  yahoo: provider({
    imap: { host: 'imap.mail.yahoo.com', port: 993, secure: true },
    smtp: { host: 'smtp.mail.yahoo.com', port: 465, secure: true },
    domains: ['yahoo.com', 'yahoo.co.jp', 'ymail.com', 'rocketmail.com'],
  }),
  icloud: provider({
    imap: { host: 'imap.mail.me.com', port: 993, secure: true },
    smtp: { host: 'smtp.mail.me.com', port: 587, secure: false, requireTLS: true },
    domains: ['icloud.com', 'me.com', 'mac.com'],
    aliases: ['apple'],
  }),
  aol: provider({
    imap: { host: 'imap.aol.com', port: 993, secure: true },
    smtp: { host: 'smtp.aol.com', port: 465, secure: true },
    domains: ['aol.com'],
  }),
  gmx: provider({
    imap: { host: 'imap.gmx.com', port: 993, secure: true },
    smtp: { host: 'mail.gmx.com', port: 587, secure: false, requireTLS: true },
    domains: ['gmx.com', 'gmx.net', 'gmx.de', 'caramail.com'],
    aliases: ['caramail'],
  }),
  mailcom: provider({
    imap: { host: 'imap.mail.com', port: 993, secure: true },
    smtp: { host: 'smtp.mail.com', port: 587, secure: false, requireTLS: true },
    domains: ['mail.com', 'email.com', 'usa.com'],
    aliases: ['mail.com'],
  }),
  zoho: provider({
    imap: { host: 'imap.zoho.com', port: 993, secure: true },
    smtp: { host: 'smtp.zoho.com', port: 465, secure: true },
    domains: ['zoho.com', 'zohomail.com'],
  }),
  proton: provider({
    imap: { host: '127.0.0.1', port: 1143, secure: false },
    smtp: { host: '127.0.0.1', port: 1025, secure: false },
    domains: ['proton.me', 'protonmail.com', 'pm.me'],
  }),
  yandex: provider({
    imap: { host: 'imap.yandex.com', port: 993, secure: true },
    smtp: { host: 'smtp.yandex.com', port: 465, secure: true },
    domains: ['yandex.com', 'yandex.ru'],
  }),
  fastmail: provider({
    imap: { host: 'imap.fastmail.com', port: 993, secure: true },
    smtp: { host: 'smtp.fastmail.com', port: 465, secure: true },
    domains: ['fastmail.com', 'fastmail.fm'],
  }),
  runbox: provider({
    imap: { host: 'mail.runbox.com', port: 993, secure: true },
    smtp: { host: 'mail.runbox.com', port: 465, secure: true },
    domains: ['runbox.com'],
  }),
  mailru: provider({
    imap: { host: 'imap.mail.ru', port: 993, secure: true },
    smtp: { host: 'smtp.mail.ru', port: 465, secure: true },
    domains: ['mail.ru', 'bk.ru', 'inbox.ru', 'list.ru'],
  }),
  naver: provider({
    imap: { host: 'imap.naver.com', port: 993, secure: true },
    smtp: { host: 'smtp.naver.com', port: 465, secure: true },
    domains: ['naver.com'],
  }),
  daum: provider({
    imap: { host: 'imap.daum.net', port: 993, secure: true },
    smtp: { host: 'smtp.daum.net', port: 465, secure: true },
    domains: ['daum.net', 'hanmail.net'],
  }),
  naverworks: provider({
    imap: { host: 'imap.worksmobile.com', port: 993, secure: true },
    smtp: { host: 'smtp.worksmobile.com', port: 465, secure: true },
  }),
};

const DOMAIN_MAP = Object.entries(PRESETS).reduce((acc, [key, conf]) => {
  for (const domain of conf.domains || []) acc[domain] = key;
  for (const alias of conf.aliases || []) acc[alias] = key;
  return acc;
}, {});

/**
 * 从 .env 解析账户配置
 * 格式: 名称|IMAP服务器|端口|邮箱地址|密码
 */
function parseAccounts() {
  const raw = process.env.ACCOUNTS;
  if (!raw) return [];

  return raw.split(',').map(entry => {
    const [name, host, port, user, pass] = entry.trim().split('|');
    return {
      name: name.trim(),
      host: host.trim(),
      port: parseInt(port.trim(), 10),
      secure: true,
      auth: {
        user: user.trim(),
        pass: pass.trim(),
      },
    };
  });
}

function presetKey(input) {
  const key = String(input || '').trim().toLowerCase();
  return DOMAIN_MAP[key] || key;
}

function normalizeSmtp(smtp) {
  if (!smtp) return null;
  return {
    host: smtp.host,
    port: smtp.port || 465,
    secure: smtp.secure !== false,
    requireTLS: !!smtp.requireTLS,
  };
}

/**
 * 用预设快速创建账户配置
 */
function fromPreset(preset, email, password) {
  const key = presetKey(preset);
  const conf = PRESETS[key];
  if (!conf) {
    throw new Error(`未知预设: ${preset}，可用: ${Object.keys(PRESETS).join(', ')}`);
  }
  return {
    name: key,
    host: conf.host,
    port: conf.port,
    secure: conf.secure !== false,
    smtp: normalizeSmtp(conf.smtp),
    auth: { user: email, pass: password },
  };
}

/**
 * 根据邮箱域名自动匹配 IMAP 配置
 */
function autoDetect(email, password) {
  const domain = email.split('@')[1]?.toLowerCase();
  if (!domain) throw new Error(`无效邮箱: ${email}`);

  const presetKeyForDomain = DOMAIN_MAP[domain];
  if (presetKeyForDomain) {
    return fromPreset(presetKeyForDomain, email, password);
  }

  // 未知域名，优先尝试通用 IMAP/SMTP 地址。
  return {
    name: domain,
    host: `imap.${domain}`,
    port: 993,
    secure: true,
    smtp: { host: `smtp.${domain}`, port: 465, secure: true },
    auth: { user: email, pass: password },
  };
}

module.exports = { PRESETS, DOMAIN_MAP, parseAccounts, fromPreset, autoDetect };
