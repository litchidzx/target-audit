/* 工作台的无浏览器冒烟测试。
 *
 * 界面逻辑没法用 Python 单测覆盖，但也不能完全靠肉眼 —— 这里用一个极简的
 * DOM 桩把 app.js 跑起来，检查首屏渲染是否正常、数据字段是否齐全。
 *
 * 用法：node tests/web_smoke.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

class El {
  constructor(id) {
    this.id = id;
    this.innerHTML = "";
    this.textContent = "";
    this.style = {};
    this.attrs = {};
  }
  hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this.attrs, n); }
  getAttribute(n) { return this.hasAttribute(n) ? this.attrs[n] : null; }
  setAttribute(n, v) { this.attrs[n] = v; }
  addEventListener() {}
}

const ids = ["stat-targets", "stat-cancers", "stat-rules", "cancers", "rules", "list", "detail", "shown", "q"];
const els = Object.fromEntries(ids.map((i) => [i, new El(i)]));

globalThis.window = {};
globalThis.document = {
  getElementById: (id) => els[id] || null,
  addEventListener: () => {},
};

const fail = [];
const check = (condition, message) => { if (!condition) fail.push(message); };

// 先加载数据，再加载界面逻辑（顺序与 index.html 一致）
new Function(readFileSync(join(root, "docs/assets/data.js"), "utf8")).call(globalThis);
check(!!globalThis.window.AUDIT_DATA, "data.js 未定义 window.AUDIT_DATA");

if (globalThis.window.AUDIT_DATA) {
  try {
    new Function(readFileSync(join(root, "docs/assets/app.js"), "utf8"))();
  } catch (err) {
    fail.push("app.js 执行抛错：" + err.message);
  }
}

const D = globalThis.window.AUDIT_DATA;
if (D) {
  check(D.counts.targets === D.targets.length, "counts.targets 与实际条数不一致");
  check(D.targets.length > 0, "数据集为空");
  check(els["stat-targets"].textContent === D.counts.targets, "顶部统计未正确渲染");
  check(els["list"].innerHTML.includes("class=\"item"), "左侧列表未渲染出条目");
  check(els["detail"].innerHTML.includes("怎么用"), "首屏未显示使用说明");
  check(els["cancers"].innerHTML.includes("chip"), "癌种标签未渲染");
  check(els["rules"].innerHTML.includes("chip"), "规则标签未渲染");

  // 数据结构完整性：每个靶点都要有判定、证据与冲突字段
  for (const t of D.targets) {
    check(!!t.gene && !!t.cancer, "存在缺少基因或癌种的靶点");
    check(typeof t.verdict === "string" && t.verdict.length > 0, `${t.gene}/${t.cancer} 缺少判断`);
    check(Array.isArray(t.evidence), `${t.gene}/${t.cancer} 缺少 evidence`);
    check(Array.isArray(t.conflicts), `${t.gene}/${t.cancer} 缺少 conflicts`);
    check(Array.isArray(t.missing), `${t.gene}/${t.cancer} 缺少 missing`);
    for (const c of t.conflicts) {
      check(/^R[1-7]$/.test(c.rule_id), `${t.gene}/${t.cancer} 规则编号异常：${c.rule_id}`);
      check(c.severity > 0 && c.severity <= 1, `${t.gene}/${t.cancer} 严重度越界：${c.severity}`);
      check(c.causes.length > 0, `${t.gene}/${t.cancer} 冲突缺少候选原因`);
    }
    for (const e of t.evidence) {
      check(typeof e.statement === "string" && e.statement.length > 0, `${t.gene}/${t.cancer} 证据缺描述`);
      check(["favorable", "unfavorable", "neutral", "unknown"].includes(e.polarity),
        `${t.gene}/${t.cancer} 极性取值异常：${e.polarity}`);
    }
  }
}

if (fail.length) {
  console.error("冒烟测试失败：");
  for (const f of fail) console.error("  - " + f);
  process.exit(1);
}
console.log(`冒烟测试通过：${D.counts.targets} 个靶点，${D.counts.cancers} 个癌种，` +
  `${D.counts.rules_fired}/${D.counts.rules_total} 条规则触发过`);
