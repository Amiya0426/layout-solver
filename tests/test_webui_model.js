#!/usr/bin/env node
/* 前端纯逻辑测试：ui/model.js（不碰 DOM，所以能直接 node 跑）。
 *
 * 跑法：node tests/test_webui_model.js
 * 或者：python tests/test_webui_model.py   （没装 node 就自动跳过）
 *
 * 重点是三件容易算错的事：
 *   1. 端口 id -> 入口/出口（决定连接表怎么排、校验怎么判）；
 *   2. 同名模块从 1 开始编号（相同模块名 精炼炉 -> 精炼炉1、精炼炉2…）；
 *   3. 连接预览用的几何与校验（锚点压在正确的边上、连线从框外出发、错接要报出来）。
 */
"use strict";

const path = require("path");
const Model = require(path.join(__dirname, "..", "ui", "model.js"));

let failed = [];
let passed = 0;

function check(name, cond, detail) {
  if (cond) {
    passed += 1;
    console.log("PASS  " + name + (detail ? "  " + detail : ""));
  } else {
    failed.push(name);
    console.log("FAIL  " + name + (detail ? "  " + detail : ""));
  }
}

function eq(name, got, want) {
  const g = JSON.stringify(got), w = JSON.stringify(want);
  check(name, g === w, g === w ? "" : `得到 ${g}，期望 ${w}`);
}

/* ---------- 测试用的设备（照 设备尺寸.xlsx 里真实的 精炼炉 / 储液罐 写） ---------- */
const 精炼炉 = {
  name: "精炼炉", w: 3, h: 3,
  ports: [
    { id: "SI", dir: "N", cells: [[0, 0], [0, 1], [0, 2]] },
    { id: "SO", dir: "S", cells: [[2, 0], [2, 1], [2, 2]] },
  ],
};
const 储液罐 = {
  name: "储液罐", w: 3, h: 3,
  ports: [
    { id: "FI", dir: "W", cells: [[1, 0]] },
    { id: "FO", dir: "E", cells: [[1, 2]] },
  ],
};
const 协议核心 = {
  name: "协议核心", w: 9, h: 9,
  ports: [
    { id: "SI-N", dir: "N", cells: [[0, 1], [0, 2], [0, 3]] },
    { id: "SI-S", dir: "S", cells: [[8, 1], [8, 2], [8, 3]] },
    { id: "SO-W", dir: "W", cells: [[1, 0], [2, 0], [3, 0]] },
    { id: "SO-E", dir: "E", cells: [[1, 8], [2, 8], [3, 8]] },
  ],
};

/* ---------------- 1) 端口分类 ---------------- */
eq("SI 是固体入口", Model.portInfo("SI").flow, "in");
eq("SO 是固体出口", Model.portInfo("SO").flow, "out");
eq("FI 是液体入口 / 标签正确", [Model.portInfo("FI").flow, Model.portInfo("FI").label],
   ["in", "液体入口"]);
eq("FO 是液体出口", Model.portInfo("FO").flow, "out");
eq("GI 是气体入口", Model.portInfo("GI").flow, "in");
eq("GO 是气体出口", Model.portInfo("GO").flow, "out");
check("带方向后缀的 FI-N 仍然认成液体入口",
      Model.portInfo("FI-N").flow === "in" && Model.portInfo("FI-N").label === "液体入口");
check("老配置的 IN/OUT 也认",
      Model.portInfo("IN").flow === "in" && Model.portInfo("OUT").flow === "out"
      && Model.portInfo("IN1").flow === "in" && Model.portInfo("OUT2").flow === "out");
eq("认不出来的端口不算入口也不算出口", Model.portInfo("P1").flow, null);
check("入口和出口颜色不同", Model.portInfo("SI").color !== Model.portInfo("SO").color);

eq("findPort 精确命中", Model.findPort({ ports: 精炼炉.ports }, "SO", "OUT").id, "SO");
eq("findPort 兜底（老配置只写 OUT/IN）",
   Model.findPort({ ports: 精炼炉.ports }, undefined, "SO").id, "SO");
eq("findPort 找不到就是 null", Model.findPort({ ports: 精炼炉.ports }, "XX", "YY"), null);
eq("findPort 模块不存在就是 null", Model.findPort(null, "SO", "OUT"), null);

/* ---------------- 2) 同名模块从 1 开始编号 ---------------- */
function addPreset(cfg, preset) {
  const id = Model.nextModuleId(cfg, preset.name);
  cfg.movable.push({
    id, preset: preset.name, w: preset.w, h: preset.h, rotatable: true,
    ports: preset.ports.map(p => ({ id: p.id, dir: p.dir, cells: p.cells.map(c => c.slice()) })),
  });
  return id;
}

let cfg = { rows: 12, cols: 22, fixed: [], movable: [], nets: [] };
eq("第一个同名模块从 1 开始", addPreset(cfg, 精炼炉), "精炼炉1");
eq("第二个是 2", addPreset(cfg, 精炼炉), "精炼炉2");
eq("第三个是 3", addPreset(cfg, 精炼炉), "精炼炉3");
eq("换一个模块名重新从 1 开始", addPreset(cfg, 储液罐), "储液罐1");
eq("再换一个也从 1 开始", addPreset(cfg, 协议核心), "协议核心1");
eq("预览提示的下一个编号跟着走", Model.nextModuleId(cfg, "精炼炉"), "精炼炉4");
eq("没加过的名字下一个还是 1", Model.nextModuleId(cfg, "研磨机"), "研磨机1");

// 中间缺号要补上：删掉 精炼炉2 后，下一个应该是 精炼炉2
cfg.movable = cfg.movable.filter(m => m.id !== "精炼炉2");
eq("中间缺号要补上（删了 2 再加回 2）", Model.nextModuleId(cfg, "精炼炉"), "精炼炉2");

// 手工建的同名前缀模块也算数（它们没有 preset 字段）
cfg = { rows: 9, cols: 9, fixed: [], movable: [
  { id: "研磨机1", w: 6, h: 4, ports: [] },
  { id: "研磨机3", w: 6, h: 4, ports: [] },
], nets: [] };
eq("没有 preset 字段的老模块也参与编号（1、3 已用 -> 2）",
   Model.nextModuleId(cfg, "研磨机"), "研磨机2");
eq("名字里有数字也不会串（研磨机 vs 机床）",
   Model.nextModuleId(cfg, "机床"), "机床1");

eq("idBase 拆得出前缀与序号", Model.idBase("精炼炉12"), { base: "精炼炉", num: 12 });
eq("idBase 对没有序号的名字给 null", Model.idBase("FX"), { base: "FX", num: null });

/* 模块 ID 必须唯一（求解器用 id 当字典键，重名会互相覆盖） */
cfg = { fixed: [], movable: [{ id: "A", w: 1, h: 1, ports: [] },
                             { id: "A2", w: 1, h: 1, ports: [] }], nets: [] };
eq("不重名就原样返回", Model.uniqueModuleId(cfg, "B"), "B");
eq("重名就往后找空号", Model.uniqueModuleId(cfg, "A"), "A1");
eq("带序号的重名从下一个开始", Model.uniqueModuleId(cfg, "A2"), "A3");
eq("重名且带序号时跳过占用的号", Model.uniqueModuleId({ movable: [{ id: "A1" }, { id: "A2" }] }, "A1"), "A3");

/* ---------------- 3) 连接校验 ---------------- */
function mod(id, preset) {
  return { id, preset: preset.name, w: preset.w, h: preset.h,
           ports: preset.ports.map(p => ({ id: p.id, dir: p.dir, cells: p.cells })) };
}
function baseCfg() {
  return { rows: 12, cols: 22, fixed: [],
           movable: [mod("精炼炉1", 精炼炉), mod("储液罐1", 储液罐)], nets: [] };
}

let c = baseCfg();
c.nets = [{ from: "精炼炉1", from_port: "SO", to: "储液罐1", to_port: "FI" }];
let rep = Model.netReport(c);
eq("出口 -> 入口：没有任何告警", rep.issues, []);
eq("模块都参与了连接", rep.idle.length, 0);
eq("没用到的**出口**会被列出来（入口不算）",
   rep.unusedOut.map(x => x.mod.id + "." + x.port.id), ["储液罐1.FO"]);

c = baseCfg();
c.nets = [{ from: "精炼炉1", from_port: "SI", to: "储液罐1", to_port: "FI" }];
rep = Model.netReport(c);
eq("出口端用了入口端口 -> 告警",
   rep.issues.filter(i => i.level === "warn" && i.text.includes("出口端用了入口端口")).length, 1);

c = baseCfg();
c.nets = [{ from: "精炼炉1", from_port: "SO", to: "储液罐1", to_port: "FO" }];
rep = Model.netReport(c);
eq("入口端用了出口端口 -> 告警",
   rep.issues.filter(i => i.level === "warn" && i.text.includes("入口端用了出口端口")).length, 1);

c = baseCfg();
c.nets = [{ from: "精炼炉1", from_port: "SO", to: "储液罐1", to_port: "FI" },
          { from: "精炼炉1", from_port: "SO", to: "储液罐1", to_port: "FI" }];
rep = Model.netReport(c);
eq("完全重复的连接 -> 告警",
   rep.issues.filter(i => i.text.includes("完全重复")).length, 1);

c = baseCfg();
c.nets = [{ from: "不存在的模块", from_port: "SO", to: "储液罐1", to_port: "FI" }];
rep = Model.netReport(c);
check("找不到模块 -> error",
      rep.issues.some(i => i.level === "error" && i.text.includes("找不到模块")));

c = baseCfg();
c.nets = [{ from: "精炼炉1", from_port: "GO", to: "储液罐1", to_port: "FI" }];
rep = Model.netReport(c);
check("模块没有这个端口 -> error",
      rep.issues.some(i => i.level === "error" && i.text.includes("没有端口 GO")));

c = baseCfg();
c.nets = [{ from: "精炼炉1", from_port: "SO", to: "精炼炉1", to_port: "SI" }];
rep = Model.netReport(c);
check("自环 -> 告警", rep.issues.some(i => i.text.includes("自环")));
eq("自环时储液罐被列为没参与连接", rep.idle.map(x => x.mod.id), ["储液罐1"]);

c = baseCfg();
c.movable[0].ports.push({ id: "SO-X", dir: "S", cells: [[9, 9]] });
c.nets = [{ from: "精炼炉1", from_port: "SO-X", to: "储液罐1", to_port: "FI" }];
rep = Model.netReport(c);
check("端口格子越界 -> 告警", rep.issues.some(i => i.text.includes("超出")));

c = baseCfg();
c.movable[0].ports.push({ id: "SO-E", dir: "S", cells: [] });
c.nets = [{ from: "精炼炉1", from_port: "SO-E", to: "储液罐1", to_port: "FI" }];
rep = Model.netReport(c);
check("空端口 -> error", rep.issues.some(i => i.level === "error" && i.text.includes("一个格子都没有")));

/* ---------------- 4) 连接预览的几何 ---------------- */
const box = { x: 100, y: 50, w: 62, h: 44 };   // 3×3 模块，最小 62×44
const m3 = { w: 3, h: 3 };
let a = Model.portAnchor(box, m3, 精炼炉.ports[0]);   // SI 在北面，三格取中
eq("北面端口锚点贴在框的上边、取三格中点",
   [a.x, a.y, a.dx, a.dy], [131, 50, 0, -1]);
a = Model.portAnchor(box, m3, 精炼炉.ports[1]);       // SO 在南面
eq("南面端口锚点贴在框的下边", [a.x, a.y, a.dx, a.dy], [131, 94, 0, 1]);
a = Model.portAnchor(box, m3, 储液罐.ports[0]);       // FI 在西面 (1,0)
eq("西面端口锚点贴在框的左边", [a.x, a.y, a.dx, a.dy], [100, 72, -1, 0]);
a = Model.portAnchor(box, m3, 储液罐.ports[1]);       // FO 在东面 (1,2)
eq("东面端口锚点贴在框的右边", [a.x, a.y, a.dx, a.dy], [162, 72, 1, 0]);

// 9×9 大框：比例要跟着放大
const bigBox = { x: 0, y: 0, w: 126, h: 126 };
a = Model.portAnchor(bigBox, { w: 9, h: 9 }, 协议核心.ports[0]);   // SI-N 三格 1..3
eq("9×9 框里北面三格取中点 -> 列 2.5", [a.x, a.y], [126 / 9 * 2.5, 0]);
a = Model.portAnchor(bigBox, { w: 9, h: 9 }, 协议核心.ports[2]);   // SO-W 行 1..3
eq("9×9 框里西面三格取中点 -> 行 2.5", [a.x, a.y], [0, 126 / 9 * 2.5]);

const tick = Model.portTick(box, m3, 精炼炉.ports[0], [0, 1]);
eq("北面格子刻度画在上边、以格中心为中线", [tick.y1, tick.y2], [50, 50]);
check("刻度是一小段（10px）", Math.abs(tick.x2 - tick.x1) === 10
      && Math.abs(tick.x1 - (131)) === 5, JSON.stringify(tick));

const g = Model.linkGeometry(
  Model.portAnchor(box, m3, 精炼炉.ports[1]),   // 南面出发
  Model.portAnchor({ x: 400, y: 50, w: 62, h: 44 }, m3, 储液罐.ports[0]), 0);
eq("连线从框外一小段处出发（南面 -> y 再往下 16）", [g.p0.x, g.p0.y], [131, 110]);
check("路径是三次贝塞尔", /^M [\d.-]+ [\d.-]+ C /.test(Model.linkPath(g)), Model.linkPath(g));
check("同一条线重复时要错开",
      JSON.stringify(Model.linkGeometry(
        Model.portAnchor(box, m3, 精炼炉.ports[1]),
        Model.portAnchor({ x: 400, y: 50, w: 62, h: 44 }, m3, 储液罐.ports[0]), 0)) !==
      JSON.stringify(Model.linkGeometry(
        Model.portAnchor(box, m3, 精炼炉.ports[1]),
        Model.portAnchor({ x: 400, y: 50, w: 62, h: 44 }, m3, 储液罐.ports[0]), 1)));

eq("模块名太长会截断", Model.shortLabel("精炼炉（液体模式）1", 62).endsWith("…"), true);
eq("短名字不截断", Model.shortLabel("精炼炉1", 62), "精炼炉1");
eq("连线颜色循环", Model.netColor(0) === Model.netColor(12), true);

/* 控制点必须被夹进画布：否则“出口朝东、入口朝北”的长连线会甩到画布外被裁掉。
   （实际布局里模块框离画布边至少 26px，而伸出的那一小段是 16px，所以锚点总在画布内。） */
const W = 320, H = 300;
const outsideA = { x: 284, y: 184, dx: 1, dy: 0 };      // 东面出发 -> 伸出到 (300,184)
const outsideZ = { x: 57, y: 26, dx: 0, dy: -1 };       // 北面进入 -> 伸出到 (57,10)
const free = Model.linkGeometry(outsideA, outsideZ, 0);
check("不夹的话控制点确实在画布外（复现问题）",
      free.c1.x > W || free.c2.y < 0,
      JSON.stringify({ c1: free.c1, c2: free.c2 }));
const held = Model.linkGeometry(outsideA, outsideZ, 0, { width: W, height: H });
check("给了画布尺寸后控制点都被夹进画布",
      ["c1", "c2"].every(k => held[k].x >= 0 && held[k].x <= W
                            && held[k].y >= 0 && held[k].y <= H),
      JSON.stringify({ c1: held.c1, c2: held.c2 }));
check("夹住控制点后曲线整体落在画布里（贝塞尔的凸包性质）",
      [held.p0, held.c1, held.c2, held.p1].every(p =>
        p.x >= 0 && p.x <= W && p.y >= 0 && p.y <= H),
      JSON.stringify({ p0: held.p0, c1: held.c1, c2: held.c2, p1: held.p1 }));
check("夹过以后仍然从端口外侧出发、朝端口里进去",
      held.p0.x > outsideA.x && held.p1.y < outsideZ.y,
      JSON.stringify({ p0: held.p0, p1: held.p1 }));

/* ---------------- 5) 框图布局 ---------------- */
const specs = [mod("精炼炉1", 精炼炉), mod("储液罐1", 储液罐), mod("协议核心1", 协议核心),
               mod("研磨机1", { name: "研磨机", w: 6, h: 4, ports: [] })];
const L = Model.layoutBoxes(specs);
eq("4 个模块 -> 2×2 摆放", [L.boxes.length, L.boxes.map(b => b.id)],
   [4, ["精炼炉1", "储液罐1", "协议核心1", "研磨机1"]]);
let overlap = [];
for (let i = 0; i < L.boxes.length; i++) {
  for (let j = i + 1; j < L.boxes.length; j++) {
    const A = L.boxes[i], B = L.boxes[j];
    if (A.x < B.x + B.w && B.x < A.x + A.w && A.y < B.y + B.h && B.y < A.y + A.h) {
      overlap.push(A.id + "/" + B.id);
    }
  }
}
eq("框之间不重叠", overlap, []);
check("所有框都在画布内",
      L.boxes.every(b => b.x >= 0 && b.y >= 0 && b.x + b.w <= L.width && b.y + b.h <= L.height),
      `画布 ${L.width}×${L.height}`);
check("框的大小与 w×h 成比例（9×9 比 3×3 大）",
      L.boxes[2].w > L.boxes[0].w && L.boxes[2].h > L.boxes[0].h);
check("布局是确定性的",
      JSON.stringify(Model.layoutBoxes(specs)) === JSON.stringify(L));
eq("没有模块时是空画布", Model.layoutBoxes([]), { boxes: [], width: 0, height: 0 });

/* 摆上框之后，每条连接的锚点都要落在自己那条边上 */
const c2 = baseCfg();
c2.nets = [{ from: "精炼炉1", from_port: "SO", to: "储液罐1", to_port: "FI" }];
const L2 = Model.layoutBoxes(Model.allModules(c2).map(x => ({
  id: x.mod.id, kind: x.kind, mod: x.mod, w: x.mod.w, h: x.mod.h,
})));
const bA = L2.boxes[0], bB = L2.boxes[1];
const aA = Model.portAnchor(bA, bA.mod, Model.findPort(bA.mod, "SO", "OUT"));
const aB = Model.portAnchor(bB, bB.mod, Model.findPort(bB.mod, "FI", "IN"));
check("SO 的锚点在 精炼炉1 框的下边", Math.abs(aA.y - (bA.y + bA.h)) < 1e-9);
check("FI 的锚点在 储液罐1 框的左边", Math.abs(aB.x - bB.x) < 1e-9);

console.log();
if (failed.length) {
  console.log(`失败 ${failed.length} 项: ${JSON.stringify(failed)}`);
  process.exit(1);
}
console.log(`全部通过（${passed} 项）`);
