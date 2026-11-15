'use strict';

/**
 * 连接自检：只测「能不能连到 Minecraft 的 host:port」，不启动 bot、不占端口。
 *
 * 用法：
 *   node check.js
 *   node check.js 54321          （临时指定端口）
 *   node check.js 127.0.0.1 25565
 */

const net = require('net');
const { loadConfig } = require('./config_loader');

const cfg = loadConfig();
const host = process.argv[2] || cfg.mcHost;
const port = Number(process.argv[3] || cfg.mcPort);

console.log('============================================================');
console.log(` 检查 ${host}:${port} 是否能连上……`);
console.log('============================================================');

const socket = net.connect({ host, port });
let done = false;

function finish(ok, detail) {
  if (done) return;
  done = true;
  socket.destroy();
  if (ok) {
    console.log('✅ 端口能连上！Minecraft 侧没问题，可以启动桥接：');
    console.log('     node server.js');
    console.log('   如果桥接仍报错，多半是 config.json 里的端口和这里不一致。');
    process.exit(0);
  }
  console.error(`❌ 连不上：${detail}`);
  console.error('');
  console.error('按顺序检查：');
  console.error('  1) 世界还开着吗？单人世界退到主菜单 / 关闭游戏，端口就没了；');
  console.error('  2) 端口抄对了吗？聊天栏那行 "Local game hosted on port XXXXX" 每次重开都会变；');
  console.error('  3) config.json 的 mcPort 和上面这个端口一致吗？');
  console.error('  4) 如果是专用服：server.properties 的 server-port 是多少？服开完了吗（控制台有 Done）？');
  console.error('  5) 只在自己电脑上测试的话，mcHost 用 127.0.0.1，不要填局域网 IP。');
  console.error('');
  console.error('在 PowerShell 里查谁在监听：');
  console.error('  Get-NetTCPConnection -State Listen | Where-Object {$_.OwningProcess -in (Get-Process java -ErrorAction SilentlyContinue).Id} | Select-Object LocalAddress,LocalPort');
  console.error('');
  console.error('用原版客户端验证：多人游戏 → 直接连接 → 输入 127.0.0.1:' + port + '，能进就说明端口对。');
  process.exit(1);
}

socket.setTimeout(3000);
socket.once('connect', () => finish(true));
socket.once('timeout', () => finish(false, '连接超时（3 秒）'));
socket.once('error', (err) => finish(false, `${err.code || ''} ${err.message}`));
