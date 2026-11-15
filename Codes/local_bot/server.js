'use strict';

/**
 * 本地 mineflayer 桥接：把「一个真实的 Mineflayer bot」包装成和 NMO 赛事 API 同形状的 HTTP 服务。
 *
 * 用途：在没有比赛服账号 / 想在自己的局域网世界调试时，让 Codes/ 里的 Agent 连这个本地服务。
 *
 *   局域网世界（Open to LAN） ← mineflayer 连接
 *            ↑
 *   node server.js（本文件，HTTP :8099）
 *            ↑
 *   Codes/main.py（BACKEND=my_server，MY_SERVER_BASE=http://127.0.0.1:8099）
 *
 * 说明：这是「测试用」桥接，覆盖核心动作；advancements（进度树）暂不支持，会返回 ready=false，
 *       Agent 仍能跑技能，只是不按进度计分（局域网测试不需要计分）。
 */

const fs = require('fs');
const path = require('path');
const express = require('express');
const mineflayer = require('mineflayer');
const { Vec3 } = require('vec3');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');
const { loadConfig } = require('./config_loader');

let collectBlockPlugin = null;
try {
  collectBlockPlugin = require('mineflayer-collectblock').plugin;
} catch (e) {
  console.warn('[bridge] 没装 mineflayer-collectblock，掉落物可能捡不到（不影响其他动作）');
}

// ---------------------------------------------------------------------------
// 配置（和 check.js 共用：默认值 < config.json < 环境变量）
// ---------------------------------------------------------------------------
const CFG = loadConfig();

// ---------------------------------------------------------------------------
// 全局状态
// ---------------------------------------------------------------------------
let bot = null;
let ready = false;
let busy = null;
let seq = 0;
let lastDeath = null;
let reconnecting = false;
const eventLog = [];

const HOSTILE = new Set([
  'zombie', 'husk', 'drowned', 'skeleton', 'stray', 'bogged', 'creeper', 'spider',
  'cave_spider', 'witch', 'slime', 'magma_cube', 'blaze', 'ghast', 'piglin', 'piglin_brute',
  'zombified_piglin', 'hoglin', 'zoglin', 'wither_skeleton', 'wither', 'enderman',
  'endermite', 'silverfish', 'vex', 'vindicator', 'evoker', 'pillager', 'ravager',
  'guardian', 'elder_guardian', 'shulker', 'phantom', 'warden', 'breeze', 'bogged',
]);

const ANIMAL = new Set([
  'cow', 'pig', 'sheep', 'chicken', 'rabbit', 'horse', 'donkey', 'mule', 'llama',
  'wolf', 'cat', 'ocelot', 'fox', 'bee', 'turtle', 'axolotl', 'goat', 'frog', 'tadpole',
  'camel', 'sniffer', 'armadillo', 'panda', 'polar_bear', 'mooshroom', 'strider',
]);

const TRANSPARENT = new Set(['air', 'cave_air', 'void_air', 'water', 'lava', 'short_grass',
  'tall_grass', 'fern', 'large_fern', 'dead_bush', 'torch', 'wall_torch', 'vine']);

const FACES = [
  [0, 1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1],
];

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const round = (v, n = 1) => Number((Number(v) || 0).toFixed(n));
const num = (v, def) => (v === undefined || v === null || v === '' ? def : Number(v));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, Number(v) || 0));

function E(code, message, extra) {
  const err = new Error(message || code);
  err.code = code;
  err.extra = extra || {};
  return err;
}

function toVec(value) {
  if (Array.isArray(value)) return new Vec3(Number(value[0]), Number(value[1]), Number(value[2]));
  if (value && typeof value === 'object') {
    return new Vec3(Number(value.x), Number(value.y), Number(value.z));
  }
  throw E('bad_args', '坐标格式不对');
}

function vec(p) {
  return [round(p.x), round(p.y), round(p.z)];
}

function withTimeout(promise, ms, code) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(E(code || 'timeout', `超过 ${ms}ms`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function pushEvent(type, data) {
  seq += 1;
  eventLog.push({ seq, t: Date.now(), type, ...(data || {}) });
  if (eventLog.length > 500) eventLog.splice(0, eventLog.length - 500);
}

function itemInfo(item) {
  if (!item) return null;
  const info = { name: item.name, count: item.count, slot: item.slot };
  if (item.maxDurability) {
    info.durability = [item.maxDurability - (item.durabilityUsed || 0), item.maxDurability];
  }
  return info;
}

function slotInfo(index) {
  if (!bot || !bot.inventory || !bot.inventory.slots) return null;
  return itemInfo(bot.inventory.slots[index]);
}

function findStack(name) {
  return bot.inventory.items().find((it) => it.name === name) || null;
}

function itemByName(name) {
  const def = bot.registry.itemsByName[name];
  if (!def) throw E('bad_args', `不认识的物品：${name}`);
  return def;
}

function blockByName(name) {
  const def = bot.registry.blocksByName[name];
  if (!def) throw E('bad_args', `不认识的方块：${name}`);
  return def;
}

function counts() {
  const out = {};
  for (const it of bot.inventory.items()) out[it.name] = (out[it.name] || 0) + it.count;
  return out;
}

function diffCounts(before, after) {
  const gained = {};
  for (const [name, n] of Object.entries(after)) {
    const d = n - (before[name] || 0);
    if (d > 0) gained[name] = d;
  }
  return gained;
}

function safeProps(block) {
  try {
    return block.getProperties ? block.getProperties() : {};
  } catch (e) {
    return {};
  }
}

function isTransparent(name) {
  return TRANSPARENT.has(name) || String(name).endsWith('_leaves');
}

function isExposed(pos) {
  for (const d of FACES) {
    const n = bot.blockAt(pos.offset(d[0], d[1], d[2]), false);
    if (!n || n.boundingBox === 'empty' || isTransparent(n.name)) return true;
  }
  return false;
}

function findBlockNear(name, maxDistance) {
  const def = blockByName(name);
  const found = bot.findBlocks({
    point: bot.entity.position,
    matching: [def.id],
    maxDistance: maxDistance || 8,
    count: 1,
  });
  return found.length ? bot.blockAt(found[0]) : null;
}

function armorPoints() {
  // 简化：按护甲栏里的物品估算（不追求和原版完全一致）
  let points = 0;
  const table = {
    leather: 1, chainmail: 2, iron: 3, golden: 2, diamond: 4, netherite: 5,
  };
  for (const slot of [5, 6, 7, 8]) {
    const it = slotInfo(slot);
    if (!it) continue;
    const tier = Object.keys(table).find((k) => it.name.startsWith(k));
    if (tier) points += table[tier];
  }
  return points;
}

function effectsList() {
  const out = [];
  const effects = (bot.entity && bot.entity.effects) || {};
  for (const id of Object.keys(effects)) {
    const e = effects[id] || {};
    let name = String(id);
    try {
      const def = bot.registry.effects && bot.registry.effects[id];
      if (def && def.name) name = def.name;
    } catch (err) { /* ignore */ }
    out.push({
      name,
      level: (e.amplifier || 0) + 1,
      duration_s: round((e.duration || 0) / 20, 1),
    });
  }
  return out;
}

function nearestHostile(radius) {
  const r = radius || 24;
  let best = null;
  for (const id of Object.keys(bot.entities)) {
    const e = bot.entities[id];
    if (!e || e === bot.entity || e.type !== 'mob' || !HOSTILE.has(e.name)) continue;
    const d = bot.entity.position.distanceTo(e.position);
    if (d > r) continue;
    if (!best || d < best.dist) best = { id: e.id, name: e.name, dist: round(d), pos: vec(e.position) };
  }
  return best;
}

function nearestHostileEntity(radius) {
  const info = nearestHostile(radius);
  return info ? bot.entities[info.id] : null;
}

function nearestThreat(radius) {
  const r = radius || 16;
  let best = null;
  for (const id of Object.keys(bot.entities)) {
    const e = bot.entities[id];
    if (!e || e === bot.entity) continue;
    const isPlayer = e.type === 'player';
    const isHostile = e.type === 'mob' && HOSTILE.has(e.name);
    if (!isPlayer && !isHostile) continue;
    const d = bot.entity.position.distanceTo(e.position);
    if (d > r) continue;
    if (!best || d < best.dist) {
      best = {
        id: e.id,
        name: e.name || (isPlayer ? 'player' : 'mob'),
        type: isPlayer ? 'player' : 'hostile',
        dist: round(d),
        pos: vec(e.position),
      };
    }
  }
  return best;
}

function dimName(raw) {
  return String(raw || 'overworld').replace('minecraft:', '');
}

// ---------------------------------------------------------------------------
// 观察
// ---------------------------------------------------------------------------
function statusPayload() {
  const p = bot.entity ? bot.entity.position : new Vec3(0, 64, 0);
  const feet = bot.blockAt(p, false);
  const head = bot.blockAt(p.offset(0, 1, 0), false);
  const hostile = nearestHostile(24);
  return {
    username: bot.username,
    pos: vec(p),
    yaw: bot.entity ? round(bot.entity.yaw, 3) : 0,
    pitch: bot.entity ? round(bot.entity.pitch, 3) : 0,
    dimension: dimName(bot.game.dimension),
    gamemode: bot.game.gameMode || 'survival',
    health: bot.health,
    food: bot.food,
    saturation: bot.foodSaturation,
    xp_level: bot.experience ? bot.experience.level : 0,
    xp_progress: bot.experience ? round(bot.experience.progress, 3) : 0,
    oxygen: 300,
    armor_points: armorPoints(),
    held: itemInfo(bot.heldItem),
    offhand: slotInfo(45),
    effects: effectsList(),
    on_fire: !!(bot.entity && bot.entity.onFire),
    is_sleeping: !!(bot.isSleeping),
    in_water: !!(feet && feet.name === 'water'),
    submerged: !!(head && head.name === 'water'),
    in_lava: !!(feet && feet.name === 'lava'),
    in_vehicle: bot.vehicle ? { id: bot.vehicle.id, name: bot.vehicle.name || 'vehicle' } : null,
    sneaking: !!(bot.controlState && bot.controlState.sneak),
    blocking: !!(bot.usingHeldItem),
    using_item: bot.usingHeldItem
      ? { name: bot.heldItem ? bot.heldItem.name : '', elapsed_ms: 0 }
      : null,
    time_of_day: bot.time ? bot.time.timeOfDay : 0,
    is_day: bot.time ? !!bot.time.isDay : true,
    raining: !!(bot.isRaining),
    thundering: !!(bot.thunderState),
    biome: feet && feet.biome ? feet.biome.name : '',
    on_ground: !!(bot.entity && bot.entity.onGround),
    alive: bot.health > 0,
    ready: !!bot.entity,
    busy,
    ...(hostile ? { nearest_hostile: hostile } : {}),
    ...(lastDeath ? { last_death: lastDeath } : {}),
  };
}

function inventoryPayload() {
  const items = bot.inventory.items().map(itemInfo);
  const armor = {
    head: slotInfo(5),
    chest: slotInfo(6),
    legs: slotInfo(7),
    feet: slotInfo(8),
  };
  return {
    items,
    armor,
    offhand: slotInfo(45),
    held: itemInfo(bot.heldItem),
    empty_slots: Math.max(0, 36 - items.length),
  };
}

function summarizeBlocks(point, radius) {
  const r = Math.min(radius, 24); // 控制耗时
  const base = point.floored();
  const counts = {};
  let scanned = 0;
  for (let dx = -r; dx <= r; dx += 1) {
    for (let dy = -r; dy <= r; dy += 1) {
      for (let dz = -r; dz <= r; dz += 1) {
        if (dx * dx + dy * dy + dz * dz > r * r) continue;
        const p = base.offset(dx, dy, dz);
        const b = bot.blockAt(p, false);
        if (!b || b.boundingBox === 'empty') continue;
        scanned += 1;
        if (!isExposed(p)) continue;
        const d = point.distanceTo(p);
        const entry = counts[b.name] || (counts[b.name] = { count: 0, nearest: vec(p), dist: round(d) });
        entry.count += 1;
        if (d < entry.dist) { entry.nearest = vec(p); entry.dist = round(d); }
      }
    }
  }
  return { radius: r, scanned, blocks: counts };
}

function findBlocks(body) {
  const radius = clamp(num(body.radius, 16), 1, 64);
  const limit = clamp(num(body.limit, 20), 1, 200);
  const point = bot.entity.position;

  if (!Array.isArray(body.types) || body.types.length === 0) {
    return summarizeBlocks(point, radius);
  }
  const ids = body.types
    .map((name) => {
      const def = bot.registry.blocksByName[name];
      return def ? def.id : null;
    })
    .filter((x) => x !== null);
  if (!ids.length) return { total: 0, blocks: [] };

  const positions = bot.findBlocks({
    point,
    matching: ids,
    maxDistance: radius,
    count: Math.max(limit * 8, 80),
  });
  const blocks = positions
    .map((p) => {
      const b = bot.blockAt(p, false);
      if (!b || b.boundingBox === 'empty') return null;
      if (!isExposed(p)) return null;
      return { name: b.name, pos: vec(p), dist: round(point.distanceTo(p)), properties: safeProps(b) };
    })
    .filter(Boolean)
    .sort((a, b) => a.dist - b.dist)
    .slice(0, limit);
  return { total: blocks.length, blocks };
}

function getBlock(body) {
  const p = toVec(body.pos || body);
  const b = bot.blockAt(p, false);
  return {
    name: b ? b.name : 'air',
    pos: vec(p),
    properties: b ? safeProps(b) : {},
    diggable: !!(b && b.diggable),
    hardness: b ? b.hardness : 0,
    harvest_tool_ok: true,
  };
}

function entityKind(e) {
  if (!e) return 'other';
  if (e.type === 'player') return 'player';
  if (e.type === 'object') return 'item';
  if (e.name === 'item') return 'item';
  if (HOSTILE.has(e.name)) return 'hostile';
  if (ANIMAL.has(e.name)) return 'animal';
  return 'other';
}

function getEntities(body) {
  const radius = clamp(num(body.radius, 32), 1, 64);
  const types = Array.isArray(body.types) ? body.types : null;
  const out = [];
  for (const id of Object.keys(bot.entities)) {
    const e = bot.entities[id];
    if (!e || e === bot.entity) continue;
    const d = bot.entity.position.distanceTo(e.position);
    if (d > radius) continue;
    const kind = entityKind(e);
    if (types && !types.includes(kind) && !types.includes(e.name)) continue;
    out.push({ id: e.id, type: kind, name: e.name || 'entity', pos: vec(e.position), dist: round(d) });
  }
  out.sort((a, b) => a.dist - b.dist);
  return { entities: out.slice(0, 40) };
}

// ---------------------------------------------------------------------------
// 动作
// ---------------------------------------------------------------------------
async function moveTo(body) {
  const range = Math.max(0, num(body.range, 1));
  let goal;
  if (body.entity_id !== undefined && body.entity_id !== null) {
    const e = bot.entities[body.entity_id];
    if (!e) throw E('no_such_entity', '实体不存在');
    goal = new goals.GoalNear(e.position.x, e.position.y, e.position.z, Math.max(1, range));
  } else {
    const x = num(body.x, body.pos ? body.pos[0] : null);
    const z = num(body.z, body.pos ? body.pos[2] : null);
    const y = num(body.y, body.pos && body.pos[1] !== undefined ? body.pos[1] : bot.entity.position.y);
    if (x === null || z === null) throw E('bad_args', '缺坐标');
    goal = new goals.GoalNear(x, y, z, range);
  }
  await withTimeout(bot.pathfinder.goto(goal), 90000, 'timeout');
  return { pos: vec(bot.entity.position) };
}

async function dig(body) {
  const p = toVec(body.pos || body);
  const block = bot.blockAt(p, false);
  if (!block || block.boundingBox === 'empty') throw E('nothing_to_dig', '这里没有方块');

  try {
    await withTimeout(
      bot.pathfinder.goto(new goals.GoalNear(p.x, p.y, p.z, 3)),
      20000,
      'path_not_found',
    );
  } catch (e) {
    if (e.code === 'path_not_found') throw e;
  }

  const before = counts();
  if (body.collect !== false && bot.collectBlock) {
    await withTimeout(bot.collectBlock.collect(block, { ignoreNoPath: true }), 30000, 'timeout');
  } else {
    await withTimeout(bot.dig(block), 30000, 'timeout');
  }
  await sleep(250);
  return {
    dug: block.name,
    dig_ms: 0,
    tool: bot.heldItem ? bot.heldItem.name : 'hand',
    gained: diffCounts(before, counts()),
  };
}

function autoPlaceTarget() {
  const base = bot.entity.position.floored();
  const candidates = [
    new Vec3(0, 0, 1), new Vec3(1, 0, 0), new Vec3(0, 0, -1), new Vec3(-1, 0, 0),
    new Vec3(0, -1, 0), new Vec3(0, 0, 2),
  ];
  for (const off of candidates) {
    const p = base.offset(off.x, off.y, off.z);
    const b = bot.blockAt(p, false);
    if (b && b.boundingBox === 'empty') return p;
  }
  return base.offset(1, 0, 0);
}

async function place(body) {
  const name = body.item;
  if (!name) throw E('bad_args', '缺 item');
  const stack = findStack(name);
  if (!stack) throw E('missing_item', `背包里没有 ${name}`);
  await bot.equip(stack, 'hand');

  const target = body.pos ? toVec(body.pos) : autoPlaceTarget();
  for (const f of FACES) {
    const refPos = target.offset(-f[0], -f[1], -f[2]);
    const ref = bot.blockAt(refPos, false);
    if (!ref || ref.boundingBox !== 'block') continue;
    try {
      await withTimeout(bot.placeBlock(ref, new Vec3(f[0], f[1], f[2])), 8000, 'place_failed');
      return { placed: name, pos: vec(target), sneaked: false };
    } catch (e) { /* 换一个面 */ }
  }
  throw E('place_failed', '找不到可放置的面');
}

async function equipByName(name, slot) {
  const stack = findStack(name);
  if (!stack) throw E('missing_item', `背包里没有 ${name}`);
  const map = {
    hand: 'hand', 'off-hand': 'off-hand', offhand: 'off-hand',
    head: 'head', torso: 'torso', legs: 'legs', feet: 'feet',
  };
  await bot.equip(stack, map[slot] || 'hand');
}

async function interact(body) {
  if (body.entity_id !== undefined && body.entity_id !== null) {
    const e = bot.entities[body.entity_id];
    if (!e) throw E('no_such_entity', '实体不存在');
    if (body.item) await equipByName(body.item, 'hand');
    await withTimeout(bot.activateEntity(e), 8000, 'cannot_interact');
    return { entity: { id: e.id, name: e.name }, in_vehicle: bot.vehicle ? { id: bot.vehicle.id } : null };
  }
  const p = toVec(body.pos || body);
  const block = bot.blockAt(p, false);
  if (!block || block.boundingBox === 'empty') throw E('no_such_block', '这里没有方块');
  try {
    await withTimeout(bot.pathfinder.goto(new goals.GoalNear(p.x, p.y, p.z, 3)), 15000, 'timeout');
  } catch (e) { /* 尽量靠近，够不到就交给 activateBlock 报错 */ }
  if (body.item) await equipByName(body.item, 'hand');
  await withTimeout(bot.activateBlock(block), 8000, 'cannot_interact');
  return { block: block.name, changed: true };
}

const FOODS = new Set([
  'bread', 'apple', 'golden_apple', 'carrot', 'golden_carrot', 'potato', 'baked_potato',
  'beetroot', 'melon_slice', 'sweet_berries', 'cookie', 'pumpkin_pie', 'beef', 'cooked_beef',
  'porkchop', 'cooked_porkchop', 'chicken', 'cooked_chicken', 'mutton', 'cooked_mutton',
  'rabbit', 'cooked_rabbit', 'cod', 'cooked_cod', 'salmon', 'cooked_salmon', 'tropical_fish',
]);

async function useItem(body) {
  const name = body.item;
  if (!name) throw E('bad_args', '缺 item');
  const stack = findStack(name);
  if (!stack) throw E('missing_item', `背包里没有 ${name}`);
  await bot.equip(stack, body.source === 'off-hand' ? 'off-hand' : 'hand');

  if (FOODS.has(name)) {
    await withTimeout(bot.consume(), Math.max(3000, num(body.timeout, 10000)), 'timeout');
    return { used: name, food: bot.food, health: bot.health };
  }
  if (body.target && body.target.pos) {
    await bot.lookAt(toVec(body.target.pos), true);
  }
  await bot.activateItem();
  await sleep(clamp(num(body.ms, 1000), 0, 10000));
  await bot.deactivateItem();
  return { used: name };
}

async function attack(body) {
  let entity = null;
  if (body.entity_id !== undefined && body.entity_id !== null) {
    entity = bot.entities[body.entity_id];
  } else {
    entity = nearestHostileEntity(24);
  }
  if (!entity) throw E('no_such_entity', '附近没有目标');

  const timeout = clamp(num(body.timeout, 20000), 1000, 60000);
  const started = Date.now();
  let hits = 0;
  while (Date.now() - started < timeout) {
    if (!entity.isValid) break;
    const d = bot.entity.position.distanceTo(entity.position);
    if (d > 3.5) {
      try {
        await withTimeout(
          bot.pathfinder.goto(new goals.GoalNear(entity.position.x, entity.position.y, entity.position.z, 2)),
          4000,
          'timeout',
        );
      } catch (e) { /* 继续尝试 */ }
    }
    try {
      await bot.lookAt(entity.position.offset(0, (entity.height || 1.8) * 0.8, 0), true);
      bot.attack(entity);
      hits += 1;
    } catch (e) { /* 目标可能已消失 */ }
    await sleep(600);
  }
  return { killed: !entity.isValid, hits, ms: Date.now() - started };
}

async function shoot(body) {
  const weapon = body.weapon || 'bow';
  const stack = findStack(weapon);
  if (!stack) throw E('missing_item', `背包里没有 ${weapon}`);
  await bot.equip(stack, 'hand');
  if (body.entity_id !== undefined) {
    const target = bot.entities[body.entity_id];
    if (!target) throw E('no_such_entity', '目标不存在');
    await bot.lookAt(target.position.offset(0, (target.height || 1.8) * 0.8, 0), true);
  } else if (body.pos) {
    await bot.lookAt(toVec(body.pos), true);
  }
  await bot.activateItem();
  await sleep(clamp(num(body.charge_ms, 1100), 0, 5000));
  await bot.deactivateItem();
  return { fired: true, weapon };
}

function itemNameById(id) {
  const def = bot.registry.items[id];
  return def ? def.name : String(id);
}

function recipeIngredientNeeds(recipe, times) {
  const needs = {};
  for (const d of recipe.delta || []) {
    if (d.count >= 0) continue;                 // 正数是产出
    const name = itemNameById(d.id);
    needs[name] = (needs[name] || 0) + (-d.count * times);
  }
  return needs;
}

function recipeMissing(recipe, times) {
  const missing = {};
  for (const d of recipe.delta || []) {
    if (d.count >= 0) continue;
    const need = -d.count * times;
    const have = bot.inventory.count(d.id, d.metadata || null);
    if (have < need) {
      const name = itemNameById(d.id);
      missing[name] = (missing[name] || 0) + (need - have);
    }
  }
  return missing;
}

async function craft(body) {
  const name = body.item;
  if (!name) throw E('bad_args', '缺 item');
  const def = itemByName(name);
  const count = clamp(num(body.count, 1), 1, 64);
  const table = findBlockNear('crafting_table', 4);

  // true = 把「需要工作台」的配方也一起返回；选哪条由下面的库存匹配决定
  const all = bot.recipesAll(def.id, null, true);
  if (!all.length) throw E('no_recipe', `没有 ${name} 的配方`);

  // 关键修复：遍历所有配方，按「当前库存 + 请求数量」算缺料，而不是拿第一条。
  // 木板/木棍等同一种物品会有多条配方（橡木/白桦/……），必须选背包真能做的那条。
  const candidates = all.map((recipe) => {
    const perCraft = (recipe.result && recipe.result.count) || 1;
    const times = Math.max(1, Math.ceil(count / perCraft));
    const missing = recipeMissing(recipe, times);
    const score = Object.values(missing).reduce((a, b) => a + b, 0);
    return {
      recipe,
      perCraft,
      times,
      missing,
      score,
      requiresTable: !!recipe.requiresTable,
      needsPerCraft: recipeIngredientNeeds(recipe, 1),
    };
  });

  const ready = candidates.filter((c) => c.score === 0);
  let chosen;
  if (ready.length) {
    // 优先不用工作台的 2x2；其次才是要工作台的
    chosen = ready.find((c) => !c.requiresTable) || ready[0];
  } else {
    // 都做不了：选缺口最小的，报给 Agent 去补料
    chosen = candidates.slice().sort((a, b) =>
      (a.score - b.score) || ((a.requiresTable ? 1 : 0) - (b.requiresTable ? 1 : 0)))[0];
  }

  if (chosen.score > 0) {
    throw E('missing_ingredients', `材料不够合成 ${name}`, {
      // 兼容旧字段：单次合成的完整用料
      example_recipe_needs: chosen.needsPerCraft,
      // 新增：按本次请求数量算出的「还差多少」，Agent 直接补这个数
      missing_total: chosen.missing,
      inventory: counts(),
      recipe_result_count: chosen.perCraft,
      alternatives: all.length,
    });
  }

  if (chosen.requiresTable && !table) {
    throw E('need_crafting_table', `合成 ${name} 需要工作台`);
  }

  const before = counts();
  await withTimeout(
    bot.craft(chosen.recipe, chosen.times, chosen.requiresTable ? table : null),
    30000,
    'timeout',
  );
  await sleep(200);
  const gained = diffCounts(before, counts());
  return {
    crafted: name,
    gained: Object.values(gained).reduce((a, b) => a + b, 0) || (chosen.perCraft * chosen.times),
    used_table: chosen.requiresTable,
    recipe_result_count: chosen.perCraft,
    inventory: counts(),
  };
}

async function equipAction(body) {
  const name = body.item;
  if (name) {
    await equipByName(name, body.slot);
    return { equipped: { name }, held: null };
  }
  // 不指定物品：挑最好的工具
  const tiers = ['netherite', 'diamond', 'iron', 'stone', 'golden', 'wooden'];
  const kinds = ['pickaxe', 'axe', 'sword', 'shovel'];
  for (const kind of kinds) {
    for (const tier of tiers) {
      const candidate = `${tier}_${kind}`;
      if (findStack(candidate)) {
        await equipByName(candidate, body.slot || 'hand');
        return { held: { name: candidate } };
      }
    }
  }
  throw E('missing_item', '没有可装备的工具');
}

async function posture(body) {
  const sneak = !!body.sneak;
  bot.setControlState('sneak', sneak);
  if (body.shield) {
    const shield = findStack('shield');
    if (!shield) throw E('missing_item', '没有盾牌');
    await bot.equip(shield, 'off-hand');
    if (!bot.usingHeldItem) await bot.activateItem(true);
  } else if (bot.usingHeldItem) {
    await bot.deactivateItem();
  }
  return { sneaking: sneak, blocking: !!body.shield && !!bot.usingHeldItem, offhand: slotInfo(45) };
}

async function openWindow(target) {
  if (!target) throw E('bad_args', '缺 target');
  if (target.entity_id !== undefined && target.entity_id !== null) {
    const e = bot.entities[target.entity_id];
    if (!e) throw E('no_such_entity', '实体不存在');
    return withTimeout(bot.openVillager(e), 15000, 'cannot_interact');
  }
  let block = null;
  if (target.pos) {
    block = bot.blockAt(toVec(target.pos), false);
  } else if (target.block) {
    block = findBlockNear(target.block, 8);
  }
  if (!block) throw E('no_such_block', '找不到容器');
  try {
    await withTimeout(
      bot.pathfinder.goto(new goals.GoalNear(block.position.x, block.position.y, block.position.z, 3)),
      15000,
      'timeout',
    );
  } catch (e) { /* 尽量靠近 */ }
  const name = String(block.name);
  if (name.includes('furnace')) return withTimeout(bot.openFurnace(block), 15000, 'cannot_interact');
  return withTimeout(bot.openContainer(block), 15000, 'cannot_interact');
}

function windowSections(win) {
  const slots = win.slots || [];
  const info = (i) => (slots[i] ? { name: slots[i].name, count: slots[i].count } : null);
  const type = String(win.type || '');
  const out = { all: [] };
  for (let i = 0; i < slots.length; i += 1) {
    if (slots[i]) out.all.push({ slot: i, name: slots[i].name, count: slots[i].count });
  }
  if (type.includes('furnace')) {
    out.input = info(0) ? [info(0)] : [];
    out.fuel = info(1) ? [info(1)] : [];
    out.output = info(2) ? [info(2)] : [];
  } else {
    const invStart = Number.isInteger(win.inventoryStart) ? win.inventoryStart : Math.max(0, slots.length - 36);
    out.container = [];
    for (let i = 0; i < invStart; i += 1) {
      if (slots[i]) out.container.push({ slot: i, name: slots[i].name, count: slots[i].count });
    }
  }
  return out;
}

async function windowView(body) {
  const win = await openWindow(body.target || body);
  const result = { window: String(win.type || ''), target: 'window', sections: windowSections(win) };
  try { await win.close(); } catch (e) { try { bot.closeWindow(win); } catch (err) { /* ignore */ } }
  return result;
}

async function windowAct(body) {
  const win = await openWindow(body.target || body);
  const type = String(win.type || '');
  const done = [];
  try {
    for (const op of body.ops || []) {
      if (op.op === 'put') {
        const stack = findStack(op.item);
        if (!stack) throw E('missing_item', `没有 ${op.item}`);
        const n = clamp(num(op.count, 1), 1, 64);
        if (type.includes('furnace')) {
          if (op.to === 'fuel') await win.putFuel(stack.type, null, n);
          else await win.putInput(stack.type, null, n);
        } else {
          await win.deposit(stack.type, null, n);
        }
      } else if (op.op === 'take') {
        if (type.includes('furnace') && (!op.from || op.from === 'output')) {
          await win.takeOutput();
        } else if (op.item) {
          const stack = findStack(op.item);
          if (!stack) throw E('missing_item', `没有 ${op.item}`);
          await win.withdraw(stack.type, null, clamp(num(op.count, 1), 1, 64));
        } else {
          throw E('bad_args', 'take 需要 from=output 或 item');
        }
      } else if (op.op === 'select') {
        if (!win.selectTrade) throw E('cannot_interact', '这个窗口不支持 select');
        await win.selectTrade(num(op.option, 0), num(op.times, 1));
      } else if (op.op === 'text') {
        if (!win.rename) throw E('cannot_interact', '这个窗口不支持 text');
        await win.rename(String(op.value || ''));
      }
      done.push(op.op);
    }
  } catch (e) {
    try { await win.close(); } catch (err) { /* ignore */ }
    throw E(e.code || 'window_failed', e.message, { done });
  }
  try { await win.close(); } catch (e) { try { bot.closeWindow(win); } catch (err) { /* ignore */ } }
  return { window: type, done };
}

async function stopAll() {
  try { bot.pathfinder.stop(); } catch (e) { /* ignore */ }
  try { bot.clearControlStates(); } catch (e) { /* ignore */ }
  try { if (bot.usingHeldItem) await bot.deactivateItem(); } catch (e) { /* ignore */ }
  return { stopped: true };
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------
const app = express();
app.use(express.json({ limit: '2mb' }));

function fail(res, code, message, extra, t0) {
  const payload = { ok: false, ms: Date.now() - (t0 || Date.now()), error: { code, message, ...(extra || {}) } };
  res.status(code === 'unauthorized' ? 401 : 200).json(payload);
}

app.use((req, res, next) => {
  if (req.path === '/health') return next();
  if (CFG.apiToken && req.headers.authorization !== `Bearer ${CFG.apiToken}`) {
    return fail(res, 'unauthorized', 'token 不对（检查 MY_SERVER_TOKEN 和 config.json）');
  }
  return next();
});

function requireBot() {
  if (!bot || !bot.entity) throw E('not_connected', 'bot 还没连上服务器');
}

async function runAction(name, res, fn, timeoutMs) {
  if (busy) return fail(res, 'busy', `正在执行 ${busy}`);
  if (!bot || !bot.entity) return fail(res, 'not_connected', 'bot 还没连上服务器');
  busy = name;
  const t0 = Date.now();
  try {
    const result = await withTimeout(fn(), timeoutMs || 120000, 'timeout');
    res.json({ ok: true, ms: Date.now() - t0, result: result || {} });
  } catch (err) {
    const code = err.code || 'internal';
    res.json({
      ok: false,
      ms: Date.now() - t0,
      error: { code, message: String(err.message || err), ...(err.extra || {}) },
    });
  } finally {
    busy = null;
  }
}

app.get('/health', (req, res) => {
  res.json({ ok: true, ready: !!ready, username: bot ? bot.username : null, host: CFG.mcHost, port: CFG.mcPort });
});

const api = express.Router();

api.get('/status', (req, res) => {
  if (!bot || !bot.entity) return fail(res, 'not_connected', 'bot 还没连上服务器');
  return res.json({ ok: true, ms: 0, result: statusPayload() });
});

api.get('/inventory', (req, res) => {
  if (!bot || !bot.entity) return fail(res, 'not_connected', 'bot 还没连上服务器');
  return res.json({ ok: true, ms: 0, result: inventoryPayload() });
});

api.post('/find_blocks', (req, res) => {
  const t0 = Date.now();
  try {
    if (!bot || !bot.entity) throw E('not_connected', 'bot 还没连上服务器');
    res.json({ ok: true, ms: Date.now() - t0, result: findBlocks(req.body || {}) });
  } catch (e) { fail(res, e.code || 'internal', e.message, e.extra, t0); }
});

api.post('/get_block', (req, res) => {
  const t0 = Date.now();
  try {
    if (!bot || !bot.entity) throw E('not_connected', 'bot 还没连上服务器');
    res.json({ ok: true, ms: Date.now() - t0, result: getBlock(req.body || {}) });
  } catch (e) { fail(res, e.code || 'internal', e.message, e.extra, t0); }
});

api.post('/get_entities', (req, res) => {
  const t0 = Date.now();
  try {
    if (!bot || !bot.entity) throw E('not_connected', 'bot 还没连上服务器');
    res.json({ ok: true, ms: Date.now() - t0, result: getEntities(req.body || {}) });
  } catch (e) { fail(res, e.code || 'internal', e.message, e.extra, t0); }
});

api.post('/events', (req, res) => {
  const since = Number((req.body || {}).since || 0);
  const limit = clamp(num((req.body || {}).limit, 50), 1, 500);
  const events = eventLog.filter((e) => e.seq > since).slice(0, limit);
  res.json({
    ok: true,
    ms: 0,
    result: {
      since,
      last_seq: events.length ? events[events.length - 1].seq : seq,
      total: events.length,
      events,
    },
  });
});

api.post('/advancements', (req, res) => {
  // 本地桥接不解析进度树：明确告诉 Agent「还没准备好」，它会跳过进度逻辑继续跑技能
  res.json({ ok: true, ms: 0, result: { ready: false, advancements: [], note: 'local bridge: advancements not implemented' } });
});

api.post('/move_to', (req, res) => { requireBotSafe(res, 'move_to', () => moveTo(req.body || {})); });
api.post('/dig', (req, res) => { requireBotSafe(res, 'dig', () => dig(req.body || {})); });
api.post('/place', (req, res) => { requireBotSafe(res, 'place', () => place(req.body || {})); });
api.post('/interact', (req, res) => { requireBotSafe(res, 'interact', () => interact(req.body || {})); });
api.post('/use_item', (req, res) => { requireBotSafe(res, 'use_item', () => useItem(req.body || {})); });
api.post('/attack', (req, res) => { requireBotSafe(res, 'attack', () => attack(req.body || {})); });
api.post('/shoot', (req, res) => { requireBotSafe(res, 'shoot', () => shoot(req.body || {})); });
api.post('/craft', (req, res) => { requireBotSafe(res, 'craft', () => craft(req.body || {})); });
api.post('/equip', (req, res) => { requireBotSafe(res, 'equip', () => equipAction(req.body || {})); });
api.post('/posture', (req, res) => { requireBotSafe(res, 'posture', () => posture(req.body || {})); });
api.post('/window/view', (req, res) => { requireBotSafe(res, 'window_view', () => windowView(req.body || {})); });
api.post('/window/act', (req, res) => { requireBotSafe(res, 'window_act', () => windowAct(req.body || {})); });
api.post('/dismount', (req, res) => {
  requireBotSafe(res, 'dismount', async () => {
    if (!bot.vehicle) throw E('not_in_vehicle', '不在载具上');
    await bot.dismount();
    return { dismounted: true, pos: vec(bot.entity.position) };
  });
});
api.post('/drop', (req, res) => {
  requireBotSafe(res, 'drop', async () => {
    const stack = findStack(req.body.item);
    if (!stack) throw E('missing_item', `没有 ${req.body.item}`);
    await bot.toss(stack.type, null, clamp(num(req.body.count, 1), 1, 64));
    return { inventory: counts() };
  });
});
api.post('/chat', (req, res) => {
  requireBotSafe(res, 'chat', async () => {
    const msg = String(req.body.message || '');
    if (!msg || msg.length > 100) throw E('bad_args', '消息为空或超过 100 字符');
    bot.chat(msg);
    return { sent: true };
  });
});
api.post('/stop', (req, res) => { requireBotSafe(res, 'stop', () => stopAll()); });

function requireBotSafe(res, name, fn) {
  if (!bot || !bot.entity) return fail(res, 'not_connected', 'bot 还没连上服务器');
  return runAction(name, res, fn);
}

app.use('/api/v1', api);

app.use((req, res) => fail(res, 'unknown_api', `没有这个接口：${req.method} ${req.path}`));

app.use((err, req, res, next) => {
  console.error('[bridge] express error', err);
  fail(res, 'internal', String(err.message || err));
});

// ---------------------------------------------------------------------------
// 连接 Minecraft
// ---------------------------------------------------------------------------
let reconnectTimer = null;

function scheduleReconnect() {
  ready = false;
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    createBot();
  }, 5000);
  console.warn('[bridge] 5 秒后自动重连……（先把 Minecraft 那边修好也行，桥接会一直重试）');
}

function createBot() {
  console.log(`[bridge] 连接 ${CFG.mcHost}:${CFG.mcPort}（用户名 ${CFG.mcUsername}，离线模式）……`);
  bot = mineflayer.createBot({
    host: CFG.mcHost,
    port: CFG.mcPort,
    username: CFG.mcUsername,
    auth: 'offline',
    version: CFG.mcVersion || false,
    viewDistance: 'normal',
  });
  bot.loadPlugin(pathfinder);
  if (collectBlockPlugin) bot.loadPlugin(collectBlockPlugin);

  bot.once('spawn', () => {
    ready = true;
    reconnecting = false;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    try {
      bot.pathfinder.setMovements(new Movements(bot));
    } catch (e) { /* ignore */ }
    pushEvent('spawn', { pos: vec(bot.entity.position) });
    console.log(`[bridge] bot 已上线：${bot.username} @ ${vec(bot.entity.position)}`);
  });

  bot.on('end', (reason) => {
    ready = false;
    pushEvent('disconnected', { reason: String(reason || '') });
    console.warn('[bridge] 连接结束：', reason);
    scheduleReconnect();
  });

  bot.on('kicked', (reason) => {
    pushEvent('kicked', { reason: typeof reason === 'string' ? reason : JSON.stringify(reason) });
    console.warn('[bridge] 被踢：', reason);
  });

  bot.on('error', (err) => {
    console.error('[bridge] bot 错误：', err.message);
    if (err.code === 'ECONNREFUSED') {
      console.error('[bridge] 这个端口没有程序在监听。检查三件事：');
      console.error('[bridge]   1) Minecraft 世界还开着吗（退到主菜单就没了）？');
      console.error('[bridge]   2) config.json 的 mcPort 和游戏里显示的端口一致吗？');
      console.error('[bridge]   3) 专用服的 server.properties / 控制台端口对吗？');
      console.error('[bridge] 可先运行：node check.js');
    }
    scheduleReconnect();
  });

  bot.on('death', () => {
    lastDeath = { pos: bot.entity ? vec(bot.entity.position) : null, t: Date.now() };
    pushEvent('death', lastDeath);
  });

  bot.on('entityHurt', (entity) => {
    if (entity === bot.entity) {
      pushEvent('hurt', {
        health: bot.health,
        damage: null,
        likely_attacker: nearestThreat(16),
      });
    }
  });

  bot.on('playerCollect', (collector, collected) => {
    if (collector === bot.entity && collected) {
      pushEvent('pickup', { item: collected.name });
    }
  });

  bot.on('chat', (username, message) => {
    if (username !== bot.username) pushEvent('chat', { text: `${username}: ${message}` });
  });
}

function preflight() {
  return new Promise((resolve) => {
    const net = require('net');
    const socket = net.connect({ host: CFG.mcHost, port: CFG.mcPort });
    let settled = false;
    const done = (ok) => {
      if (settled) return;
      settled = true;
      try { socket.destroy(); } catch (e) { /* ignore */ }
      resolve(ok);
    };
    socket.setTimeout(2500);
    socket.once('connect', () => done(true));
    socket.once('timeout', () => done(false));
    socket.once('error', () => done(false));
  });
}

(async () => {
  const ok = await preflight();
  if (!ok) {
    console.warn('============================================================');
    console.warn(`[bridge] ⚠️ 现在连不上 ${CFG.mcHost}:${CFG.mcPort}`);
    console.warn('[bridge] 常见原因：世界没开 / 端口不对 / 专用服没启动。');
    console.warn('[bridge] 先运行：node check.js   （详见 Workflow/局域网测试指南.md）');
    console.warn('[bridge] 桥接会每 5 秒自动重试，Minecraft 那边弄好就会自己连上。');
    console.warn('============================================================');
  }
  createBot();
})();

app.listen(CFG.apiPort, '127.0.0.1', () => {
  console.log('============================================================');
  console.log(' 本地 Bot 桥接已启动（只监听 127.0.0.1，外网连不到）');
  console.log(`   接口地址：http://127.0.0.1:${CFG.apiPort}/api/v1`);
  console.log(`   鉴权 token：${CFG.apiToken}`);
  console.log(`   连 Minecraft：${CFG.mcHost}:${CFG.mcPort}（用户名 ${CFG.mcUsername}）`);
  console.log(' 快速检查：浏览器打开 http://127.0.0.1:' + CFG.apiPort + '/health');
  console.log('============================================================');
});
