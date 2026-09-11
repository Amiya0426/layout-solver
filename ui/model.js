/* 前端纯逻辑层：不碰 DOM，只用普通对象算东西。
 *
 * 抽出来的理由有两个：
 *   1. 端口分类、同名编号、连接校验、连线几何这些才是容易算错的地方，
 *      放在这里就能用 `node tests/test_webui_model.js` 直接跑，不用起浏览器；
 *   2. app.js 只负责把结果画到页面上，两边职责清楚。
 *
 * 浏览器里 <script src="/static/model.js"> 引入，挂到全局 Model；
 * Node 里 require() 也能拿到同一个对象（见文件末尾）。
 */
var Model = (function () {
  "use strict";

  /* 设备尺寸表里的标记 -> 类型。预设端口直接用标记做 id：SI/SO/FI/FO/GI/GO；
     老配置里的 IN/OUT 也认，认不出来的一律按“其它端口”处理。 */
  var MARKER_INFO = {
    si: { label: "固体入口", flow: "in",  color: "#1d4ed8" },
    so: { label: "固体出口", flow: "out", color: "#60a5fa" },
    fi: { label: "液体入口", flow: "in",  color: "#0e7490" },
    fo: { label: "液体出口", flow: "out", color: "#22d3ee" },
    gi: { label: "气体入口", flow: "in",  color: "#6d28d9" },
    go: { label: "气体出口", flow: "out", color: "#c084fc" }
  };

  var NET_COLORS = ["#e11d48", "#2563eb", "#16a34a", "#f59e0b", "#7c3aed", "#0891b2",
                    "#db2777", "#65a30d", "#ea580c", "#4f46e5", "#0d9488", "#a16207"];

  function netColor(i) { return NET_COLORS[i % NET_COLORS.length]; }

  /* "FI-N" -> 液体入口；"IN"/"IN1" -> 入口；"OUT" -> 出口；其它 -> 端口 */
  function portInfo(portId) {
    var raw = String(portId == null ? "" : portId);
    var id = raw.toUpperCase();
    var mark = id.split("-")[0].toLowerCase();
    if (MARKER_INFO[mark]) {
      return { id: raw, mark: mark, label: MARKER_INFO[mark].label,
               flow: MARKER_INFO[mark].flow, color: MARKER_INFO[mark].color };
    }
    if (id.indexOf("IN") === 0) {
      return { id: raw, mark: null, label: "入口", flow: "in", color: "#475569" };
    }
    if (id.indexOf("OUT") === 0) {
      return { id: raw, mark: null, label: "出口", flow: "out", color: "#94a3b8" };
    }
    return { id: raw, mark: null, label: "端口", flow: null, color: "#94a3b8" };
  }

  function findPort(mod, pid, fallback) {
    if (!mod) return null;
    var ports = mod.ports || [];
    for (var i = 0; i < ports.length; i++) if (ports[i].id === pid) return ports[i];
    for (var j = 0; j < ports.length; j++) if (ports[j].id === fallback) return ports[j];
    return null;
  }

  function allModules(cfg) {
    var out = [];
    (cfg.fixed || []).forEach(function (m, i) { out.push({ kind: "fixed", index: i, mod: m }); });
    (cfg.movable || []).forEach(function (m, i) { out.push({ kind: "movable", index: i, mod: m }); });
    return out;
  }

  function moduleById(cfg, id) {
    var found = null;
    allModules(cfg).forEach(function (x) { if (x.mod.id === id) found = x.mod; });
    return found;
  }

  /* ---------------- 同名模块从 1 开始编号 ---------------- */
  function idBase(id) {
    var m = /^(.*?)(\d+)$/.exec(String(id == null ? "" : id));
    return m ? { base: m[1], num: +m[2] } : { base: String(id == null ? "" : id), num: null };
  }

  /* 「精炼炉」-> 精炼炉1；已经有 精炼炉1、精炼炉3 时 -> 精炼炉2 */
  function nextModuleId(cfg, name) {
    var taken = {}, used = {};
    allModules(cfg).forEach(function (x) {
      taken[x.mod.id] = true;
      var b = idBase(x.mod.id);
      if (b.num === null) return;
      if (x.mod.preset === name || b.base === name) used[b.num] = true;
    });
    var n = 1;
    while (used[n] || taken[name + n]) n += 1;
    return name + n;
  }

  /* 模块 ID 必须唯一：求解器拿 id 当字典键，重名会互相覆盖 */
  function uniqueModuleId(cfg, id) {
    var taken = {};
    allModules(cfg).forEach(function (x) { taken[x.mod.id] = true; });
    if (!taken[id]) return id;
    var b = idBase(id);
    var base = b.num === null ? id : b.base;
    var n = b.num === null ? 1 : b.num + 1;
    while (taken[base + n]) n += 1;
    return base + n;
  }

  /* ---------------- 连接校验 ---------------- */
  function cellIssues(mod, port, tag, modId) {
    var out = [];
    if (!mod) return out;
    (port.cells || []).forEach(function (c) {
      if (!(c[0] >= 0 && c[0] < mod.h && c[1] >= 0 && c[1] < mod.w)) {
        out.push({ level: "warn", net: null,
                   text: tag + "：「" + modId + "·" + port.id + "」的格子 (" + c[0] + "," + c[1] +
                         ") 超出了 " + mod.w + "×" + mod.h });
      }
    });
    return out;
  }

  /* 逐条检查「出口 -> 入口」：模块/端口在不在、方向对不对、有没有重复… */
  function netReport(cfg) {
    var mods = allModules(cfg);
    var nets = cfg.nets || [];
    var issues = [], seen = {}, connected = {}, outUsed = {}, outPorts = [];
    mods.forEach(function (x) {
      (x.mod.ports || []).forEach(function (p) {
        if (portInfo(p.id).flow === "out") outPorts.push({ mod: x.mod, port: p });
      });
    });

    nets.forEach(function (n, i) {
      var tag = "连接 " + (i + 1);
      var fmod = moduleById(cfg, n.from), tmod = moduleById(cfg, n.to);
      if (!fmod) issues.push({ level: "error", net: i, text: tag + "：找不到模块「" + n.from + "」" });
      if (!tmod) issues.push({ level: "error", net: i, text: tag + "：找不到模块「" + n.to + "」" });
      var fp = findPort(fmod, n.from_port, "OUT");
      var tp = findPort(tmod, n.to_port, "IN");
      if (fmod && !fp) issues.push({ level: "error", net: i, text: tag + "：「" + n.from + "」没有端口 " + (n.from_port || "?") });
      if (tmod && !tp) issues.push({ level: "error", net: i, text: tag + "：「" + n.to + "」没有端口 " + (n.to_port || "?") });
      if (fmod && tmod && n.from === n.to) issues.push({ level: "warn", net: i, text: tag + "：首尾是同一个模块（自环）" });

      if (fp) {
        var fi = portInfo(fp.id);
        connected[n.from] = true;
        if (fi.flow === "in") {
          issues.push({ level: "warn", net: i, text: tag + "：出口端用了入口端口「" + n.from + "·" + fp.id + "」（" + fi.label + "）" });
        } else {
          outUsed[n.from + "\u0000" + fp.id] = true;
        }
        if (!(fp.cells || []).length) issues.push({ level: "error", net: i, text: tag + "：「" + n.from + "·" + fp.id + "」一个格子都没有" });
        issues = issues.concat(cellIssues(fmod, fp, tag, n.from));
      }
      if (tp) {
        var ti = portInfo(tp.id);
        connected[n.to] = true;
        if (ti.flow === "out") {
          issues.push({ level: "warn", net: i, text: tag + "：入口端用了出口端口「" + n.to + "·" + tp.id + "」（" + ti.label + "）" });
        }
        if (!(tp.cells || []).length) issues.push({ level: "error", net: i, text: tag + "：「" + n.to + "·" + tp.id + "」一个格子都没有" });
        issues = issues.concat(cellIssues(tmod, tp, tag, n.to));
      }
      var key = [n.from, n.from_port, n.to, n.to_port].join("\u0000");
      if (seen[key] !== undefined) {
        issues.push({ level: "warn", net: i, text: tag + "：与连接 " + (seen[key] + 1) + " 完全重复" });
      } else {
        seen[key] = i;
      }
    });

    var idle = mods.filter(function (x) { return !connected[x.mod.id]; });
    var unusedOut = outPorts.filter(function (x) { return !outUsed[x.mod.id + "\u0000" + x.port.id]; });
    return { issues: issues, idle: idle, unusedOut: unusedOut,
             netCount: nets.length, modCount: mods.length };
  }

  /* ---------------- 画图几何 ---------------- */
  /* 模块框网格：每个框的大小与 w×h 成比例，按列数摆放，彼此不重叠。
     返回 {boxes:[{id,kind,index,mod,x,y,w,h}], width, height} */
  function layoutBoxes(mods, opt) {
    opt = opt || {};
    var CELL = opt.cell || 14, MINW = opt.minW || 62, MINH = opt.minH || 44;
    var GAPX = opt.gapX || 118, GAPY = opt.gapY || 92, PAD = opt.pad || 26;
    var MAXCOLS = opt.maxCols || 4;
    if (!mods.length) return { boxes: [], width: 0, height: 0 };
    var sizes = mods.map(function (x) {
      return { w: Math.max(x.w * CELL, MINW), h: Math.max(x.h * CELL, MINH) };
    });
    var maxW = Math.max.apply(null, sizes.map(function (s) { return s.w; }));
    var maxH = Math.max.apply(null, sizes.map(function (s) { return s.h; }));
    var nCols = Math.max(1, Math.min(MAXCOLS, Math.ceil(Math.sqrt(mods.length))));
    var nRows = Math.ceil(mods.length / nCols);
    var cellW = maxW + GAPX, cellH = maxH + GAPY;
    var boxes = mods.map(function (x, i) {
      var col = i % nCols, row = Math.floor(i / nCols), s = sizes[i];
      return {
        id: x.id, kind: x.kind, index: i, mod: x.mod,
        x: PAD + col * cellW + (maxW - s.w) / 2,
        y: PAD + row * cellH + (maxH - s.h) / 2,
        w: s.w, h: s.h
      };
    });
    return {
      boxes: boxes,
      width: PAD * 2 + nCols * cellW - GAPX,
      height: PAD * 2 + nRows * cellH - GAPY
    };
  }

  /* 端口锚点：多格端口取这些格的平均位置，落在模块框对应的那条边上 */
  function portAnchor(box, mod, port) {
    var cw = box.w / Math.max(1, mod.w), ch = box.h / Math.max(1, mod.h);
    var cells = (port.cells && port.cells.length) ? port.cells : [[0, 0]];
    var avg = function (k) {
      return cells.reduce(function (s, c) { return s + (c[k] || 0); }, 0) / cells.length;
    };
    var dir = port.dir || "S";
    if (dir === "N" || dir === "S") {
      return { x: box.x + (avg(1) + 0.5) * cw, y: dir === "N" ? box.y : box.y + box.h,
               dx: 0, dy: dir === "N" ? -1 : 1, dir: dir };
    }
    return { x: dir === "W" ? box.x : box.x + box.w, y: box.y + (avg(0) + 0.5) * ch,
             dx: dir === "W" ? -1 : 1, dy: 0, dir: dir };
  }

  /* 端口格在框边上的那一小段刻度 */
  function portTick(box, mod, port, cell) {
    var cw = box.w / Math.max(1, mod.w), ch = box.h / Math.max(1, mod.h);
    var half = 5, dir = port.dir || "S";
    if (dir === "N" || dir === "S") {
      var x = box.x + ((cell[1] || 0) + 0.5) * cw;
      var y = dir === "N" ? box.y : box.y + box.h;
      return { x1: x - half, y1: y, x2: x + half, y2: y };
    }
    var yy = box.y + ((cell[0] || 0) + 0.5) * ch;
    var xx = dir === "W" ? box.x : box.x + box.w;
    return { x1: xx, y1: yy - half, x2: xx, y2: yy + half };
  }

  function shortLabel(text, boxW) {
    var s = String(text == null ? "" : text);
    var max = Math.max(3, Math.floor(boxW / 7));
    return s.length > max ? s.slice(0, max - 1) + "…" : s;
  }

  /* 一条连线（出口 -> 入口）的路径：两端各沿端口方向伸出一小段，再三次贝塞尔弯过去。
     bounds 给定时把控制点夹进画布内 —— 三次贝塞尔一定落在控制点的凸包里，
     所以夹住控制点就等于保证整条曲线不会跑出画布被裁掉（出口朝东、入口朝北时很容易跑出去）。 */
  function linkGeometry(a, z, dup, bounds) {
    var stub = 16;
    var p0 = { x: a.x + a.dx * stub, y: a.y + a.dy * stub };
    var p1 = { x: z.x + z.dx * stub, y: z.y + z.dy * stub };
    var dx = p1.x - p0.x, dy = p1.y - p0.y;
    var dist = Math.sqrt(dx * dx + dy * dy) || 1;
    var bend = Math.max(36, dist * 0.42);
    var off = (dup || 0) * 16;
    var nx = -dy / dist, ny = dx / dist;
    var c1 = { x: p0.x + a.dx * bend + nx * off, y: p0.y + a.dy * bend + ny * off };
    var c2 = { x: p1.x + z.dx * bend + nx * off, y: p1.y + z.dy * bend + ny * off };
    if (bounds) {
      var m = bounds.margin == null ? 3 : bounds.margin;
      var clamp = function (c) {
        return {
          x: Math.min(Math.max(c.x, m), Math.max(m, bounds.width - m)),
          y: Math.min(Math.max(c.y, m), Math.max(m, bounds.height - m))
        };
      };
      c1 = clamp(c1);
      c2 = clamp(c2);
    }
    return {
      p0: p0, c1: c1, c2: c2, p1: p1,
      mid: { x: (p0.x + 3 * c1.x + 3 * c2.x + p1.x) / 8,
             y: (p0.y + 3 * c1.y + 3 * c2.y + p1.y) / 8 }
    };
  }

  function r1(v) { return Math.round(v * 10) / 10; }

  function linkPath(g) {
    return "M " + r1(g.p0.x) + " " + r1(g.p0.y)
      + " C " + r1(g.c1.x) + " " + r1(g.c1.y) + ", "
      + r1(g.c2.x) + " " + r1(g.c2.y) + ", "
      + r1(g.p1.x) + " " + r1(g.p1.y);
  }

  return {
    MARKER_INFO: MARKER_INFO,
    NET_COLORS: NET_COLORS,
    netColor: netColor,
    portInfo: portInfo,
    findPort: findPort,
    allModules: allModules,
    moduleById: moduleById,
    idBase: idBase,
    nextModuleId: nextModuleId,
    uniqueModuleId: uniqueModuleId,
    netReport: netReport,
    layoutBoxes: layoutBoxes,
    portAnchor: portAnchor,
    portTick: portTick,
    shortLabel: shortLabel,
    linkGeometry: linkGeometry,
    linkPath: linkPath,
    r1: r1
  };
})();

/* Node 里 require() 用；浏览器里 module 未定义，静默跳过。 */
if (typeof module !== "undefined" && module.exports) module.exports = Model;
