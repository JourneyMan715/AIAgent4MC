'use strict';

/**
 * 配置读取：默认值 < config.json < 环境变量
 * 被 server.js 和 check.js 共用。
 */

const fs = require('fs');
const path = require('path');

const DEFAULTS = {
  mcHost: '127.0.0.1',
  mcPort: 25565,
  mcUsername: 'TestBot',
  mcVersion: '',
  apiPort: 8099,
  apiToken: 'dev',
};

function loadConfig() {
  const cfg = { ...DEFAULTS };
  const file = path.join(__dirname, 'config.json');
  if (fs.existsSync(file)) {
    try {
      Object.assign(cfg, JSON.parse(fs.readFileSync(file, 'utf8')));
    } catch (e) {
      console.error('[bridge] config.json 解析失败：', e.message);
    }
  }
  const envMap = {
    MC_HOST: 'mcHost', MC_PORT: 'mcPort', MC_USERNAME: 'mcUsername',
    MC_VERSION: 'mcVersion', API_PORT: 'apiPort', API_TOKEN: 'apiToken',
  };
  for (const [env, key] of Object.entries(envMap)) {
    if (process.env[env] !== undefined && process.env[env] !== '') {
      cfg[key] = process.env[env];
    }
  }
  cfg.mcPort = Number(cfg.mcPort) || 25565;
  cfg.apiPort = Number(cfg.apiPort) || 8099;
  return cfg;
}

module.exports = { loadConfig, DEFAULTS };
