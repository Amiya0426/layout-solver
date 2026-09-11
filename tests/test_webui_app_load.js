#!/usr/bin/env node
/* 用一个极小的 DOM 桩把 ui/app.js 真跑一遍。
 *
 * 为什么需要它：这台机器上的 Chrome/Edge 起不了无头实例（会转发到已有会话），
 * 但「渲染代码半路抛异常 / 调用了已经不存在的函数」这类问题必须查出来。
 * 于是用桩件把 index.html 那套脚本按顺序加载，喂一份真实的预设缓存，
 * 真的走一遍「按预设添加模块 -> 设置连接 -> 画连接预览」，再把生成的 SVG 字符串拿出来断言。
 *
 * 跑法：node tests/test_webui_app_load.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const UI = path.join(ROOT, "ui");
const presetCache = JSON.parse(
  fs.readFileSync(path.join(ROOT, "data", "device_presets.json"), "utf8"));

let failed = [];
let passed = 0;
function check(name, cond, detail) {
  if (cond) { passed += 1; console.log("PASS  " + name + (detail ? "  " + detail : "")); }
  else { failed.push(name); console.log("FAIL  " + name + (detail ? "  " + detail : "")); }
}
function eq(name, got, want) {
  const g = JSON.stringify(got);
  const ok = g === JSON.stringify(want);
  check(name, ok, ok ? "" : `得到 ${g}，期望 ${JSON.stringify(want)}`);
}

/* ---------------- 极小 DOM 桩 ---------------- */
function makeEl(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    children: [], style: {}, dataset: {}, options: [],
    value: "", textContent: "", className: "", title: "",
    checked: false, disabled: false, files: [],
    _html: "",
    classList: {
      add() {}, remove() {}, toggle() {}, contains() { return false; },
    },
    appendChild(c) { this.children.push(c); return c; },
    // 记录 HTML 片段，方便测试断言“生成了什么”
    insertAdjacentHTML(pos, html) { this.children.push({ __html: html }); },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
    removeEventListener() {},
    scrollIntoView() {},
    click() {},
    setAttribute() {}, getAttribute() { return null; },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return this._html; },
    // 真 innerHTML 赋值会清空原有子节点，桩件也得这样，否则测试会看到累积的旧内容
    set(v) { this._html = String(v); this.children.length = 0; },
  });
  return el;
}

function dump(el) {
  let s = el._html || "";
  el.children.forEach(c => { s += c.__html || dump(c); });
  return s;
}

function makeDocument() {
  const els = Object.create(null);
  const doc = {
    getElementById(id) {
      if (!els[id]) els[id] = makeEl("div");
      return els[id];
    },
    createElement: makeEl,
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
  };
  doc.__els = els;
  return doc;
}

const document = makeDocument();
const sandbox = {
  document,
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  alert() {}, confirm() { return true; },
  fetch(url) {
    // 只喂预设接口，其它接口给空壳，够让 init() 跑完
    const body = String(url).includes("/api/presets")
      ? presetCache
      : (String(url).includes("/api/problems") ? { problems: [] } : {});
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    });
  },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);

/* ---------------- 按 index.html 的顺序加载脚本 ---------------- */
vm.runInContext(fs.readFileSync(path.join(UI, "model.js"), "utf8"), ctx, { filename: "model.js" });
check("model.js 暴露了全局 Model", typeof vm.runInContext("typeof Model", ctx) === "string"
      && vm.runInContext("typeof Model", ctx) === "object");
vm.runInContext(fs.readFileSync(path.join(UI, "app.js"), "utf8"), ctx, { filename: "app.js" });
check("app.js 加载没有抛异常（DOM 桩下）", true);

/* ---------------- 在同一个上下文里跑真实流程 ---------------- */
(async function () {
  const out = await vm.runInContext(`(async function () {
    const out = {};
    // 桩件没有真正的 DOM 树，innerHTML 之外的节点要自己摊平
    const dumpEl = (el) => (el.innerHTML || "")
      + (el.children || []).map(c => c.__html || dumpEl(c)).join("");
    await new Promise(r => setTimeout(r, 60));   // 等 app.js 自己的 init() 跑完
    await loadPresets();                          // 用真实缓存喂预设
    // 换成一份干净的画布，避免受 configs/ 里第一个题目的内容影响
    cfg = { rows: 10, cols: 20, fixed: [], movable: [], nets: [] };
    renderAll();

    /* --- 预设下拉 --- */
    out.presetOptions = document.getElementById("presetSelect").children.length - 1;
    out.presetSource = document.getElementById("presetSource").textContent;

    /* --- 选中预设 -> 小图 --- */
    const sel = document.getElementById("presetSelect");
    sel.value = "精炼炉";
    renderPresetPreview();
    const pv = document.getElementById("presetPreview").innerHTML;
    out.presetCells = (pv.match(/preset-cell/g) || []).length;
    out.presetHead = pv.slice(0, 220);

    /* --- 按预设添加：同名从 1 开始编号 --- */
    document.getElementById("newModKind").value = "movable";
    document.getElementById("newModRot").checked = true;
    addModuleFromPreset();
    sel.value = "精炼炉"; addModuleFromPreset();
    sel.value = "精炼炉"; addModuleFromPreset();
    sel.value = "储液罐"; addModuleFromPreset();
    out.ids = allModules().map(x => x.mod.id);
    out.ports = allModules()[0].mod.ports.map(p => p.id + ":" + p.dir + "×" + p.cells.length);
    out.listHtml = dumpEl(document.getElementById("moduleList"));

    /* --- 连接表 + 连接预览 --- */
    cfg.nets = [
      { from: "精炼炉1", from_port: "SO", to: "储液罐1", to_port: "FI" },
      { from: "储液罐1", from_port: "FO", to: "精炼炉1", to_port: "SI" },
    ];
    renderNets();
    renderLinkPreview();
    const svg = document.getElementById("linkPreview").innerHTML;
    out.svgLength = svg.length;
    out.groups = (svg.match(/class="link-group"/g) || []).length;
    out.paths = (svg.match(/<path d="M /g) || []).length;
    out.arrows = (svg.match(/marker-end="url\\(#netarrow/g) || []).length;
    out.indexLabels = (svg.match(/class="link-index"/g) || []).length;
    out.rects = (svg.match(/<rect /g) || []).length;
    out.ticks = (svg.match(/<line /g) || []).length;
    out.titles = (svg.match(/<title>([^<]*)<\\/title>/g) || []).map(s => s.replace(/<[^>]*>/g, ""));
    out.summary = document.getElementById("linkSummary").textContent;
    out.issues = document.getElementById("linkIssues").innerHTML;
    out.netListHtml = document.getElementById("netList").children.map(r =>
      (r.__html || "") + (r.children || []).map(c => c.__html || "").join("")).join("|");
    out.netRowCount = document.getElementById("netList").children.length;

    /* --- 错接要报出来 --- */
    cfg.nets = [{ from: "没有这个模块", from_port: "SO", to: "储液罐1", to_port: "FI" }];
    renderLinkPreview();
    out.badIssues = document.getElementById("linkIssues").innerHTML;
    out.badGroups = (document.getElementById("linkPreview").innerHTML.match(/class="link-group"/g) || []).length;
    out.badPaths = (document.getElementById("linkPreview").innerHTML.match(/<path d="M /g) || []).length;

    /* --- 出错也不能把整页带崩：还原成合法连接再画一次 --- */
    cfg.nets = [{ from: "精炼炉1", from_port: "SO", to: "储液罐1", to_port: "FI" }];
    renderAll();
    out.afterRenderAll = (document.getElementById("linkPreview").innerHTML.match(/class="link-group"/g) || []).length;

    /* --- 全部接好：校验通过 --- */
    cfg.nets = [
      { from: "精炼炉1", from_port: "SO", to: "储液罐1", to_port: "FI" },
      { from: "精炼炉2", from_port: "SO", to: "储液罐1", to_port: "FI" },
      { from: "精炼炉3", from_port: "SO", to: "储液罐1", to_port: "FI" },
      { from: "储液罐1", from_port: "FO", to: "精炼炉1", to_port: "SI" },
      { from: "储液罐1", from_port: "FO", to: "精炼炉2", to_port: "SI" },
      { from: "储液罐1", from_port: "FO", to: "精炼炉3", to_port: "SI" },
    ];
    renderNets();
    renderLinkPreview();
    out.okIssues = document.getElementById("linkIssues").innerHTML;
    out.okGroups = (document.getElementById("linkPreview").innerHTML.match(/class="link-group"/g) || []).length;
    out.okSummary = document.getElementById("linkSummary").textContent;
    out.okSvg = document.getElementById("linkPreview").innerHTML;   // 交给 Python 侧做 XML 校验
    return out;
  })()`, ctx);

  console.log("渲染结果摘要：");
  console.log("    ids=" + JSON.stringify(out.ids) + "  端口=" + JSON.stringify(out.ports));
  console.log("    svg=" + out.svgLength + " 字节, group=" + out.groups + ", path=" + out.paths
              + ", rect=" + out.rects + ", 刻度=" + out.ticks);
  console.log("    titles=" + JSON.stringify(out.titles));
  console.log();

  eq("预设下拉拿到 46 个设备", out.presetOptions, 46);
  check("预设来源显示设备尺寸表", String(out.presetSource).includes("设备尺寸.xlsx"), out.presetSource);
  eq("预设小图画了 3×3=9 个格子", out.presetCells, 9);
  check("预设小图提示下一个编号是 精炼炉1", String(out.presetHead).includes("精炼炉1"), out.presetHead);
  eq("同名模块从 1 开始编号（精炼炉1/2/3 + 储液罐1）",
     out.ids, ["精炼炉1", "精炼炉2", "精炼炉3", "储液罐1"]);
  eq("预设端口按表格落位（北 SI 3 格 / 南 SO 3 格）", out.ports, ["SI:N×3", "SO:S×3"]);
  check("模块列表显示了这些 ID", ["精炼炉1", "精炼炉2", "储液罐1"].every(id => out.listHtml.includes(id)),
        out.listHtml.slice(0, 120));

  eq("连接预览画出 2 条连线", out.groups, 2);
  eq("两条连线都是带路径的", out.paths, 2);
  eq("连线末端都带箭头", out.arrows, 2);
  eq("连线上有编号 1/2", out.indexLabels, 2);
  eq("每个模块一个框", out.rects, 4);
  eq("端口格子都画了刻度（精炼炉 3+3 各一个 ×3 台、储液罐 1+1）", out.ticks, 20);
  check("tooltip 写明 出口 -> 入口 与端口含义",
        out.titles.some(t => t.includes("精炼炉1·SO") && t.includes("液体入口")), JSON.stringify(out.titles));
  check("连接摘要说明条数/模块数/出口未接", String(out.summary).includes("2 条连接")
        && String(out.summary).includes("出口未接"), out.summary);
  check("合法连接没有 error/warn", !String(out.issues).includes("issue error")
        && !String(out.issues).includes("issue warn"), out.issues.slice(0, 160));
  check("没参与连接的模块会被提示", String(out.issues).includes("精炼炉2")
        && String(out.issues).includes("没有参与任何连接"), out.issues.slice(0, 200));
  eq("连接表 2 行", out.netRowCount, 2);
  check("出口端下拉把出口排最前（optgroup 出口）",
        String(out.netListHtml).includes('<optgroup label="出口">'), out.netListHtml.slice(0, 200));
  check("端口选项带中文含义", String(out.netListHtml).includes("SO · 固体出口"),
        String(out.netListHtml).slice(0, 300));

  check("模块不存在时报 error", String(out.badIssues).includes("issue error")
        && String(out.badIssues).includes("找不到模块"), out.badIssues.slice(0, 160));
  eq("坏的连接不会被画出来", [out.badGroups, out.badPaths], [0, 0]);
  eq("renderAll() 之后预览恢复正常", out.afterRenderAll, 1);
  eq("全部接好时 6 条连线都画出来", out.okGroups, 6);
  check("全部接好时显示“检查通过”", String(out.okIssues).includes("issue ok"),
        out.okIssues.slice(0, 200));
  check("全部接好时摘要里没有“出口未接”", !String(out.okSummary).includes("出口未接"),
        out.okSummary);

  console.log();
  // 把最后那张 SVG 原样吐出来：Python 侧（tests/test_webui_frontend.py）会用
  // ElementTree 真解析一遍 —— 标记写错的话浏览器只会白屏，这里必须先炸。
  console.log("---SVG-BEGIN---");
  console.log(out.okSvg);
  console.log("---SVG-END---");
  if (failed.length) {
    console.log(`失败 ${failed.length} 项: ${JSON.stringify(failed)}`);
    process.exit(1);
  }
  console.log(`全部通过（${passed} 项）`);
})().catch(e => {
  console.log("FAIL  流程抛异常: " + (e && e.stack || e));
  process.exit(1);
});
