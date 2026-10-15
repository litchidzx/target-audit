/* 靶点尽调工作台。原生 JS，无依赖，数据来自同目录的 data.js。 */
(function () {
  var D = window.AUDIT_DATA;
  var state = { q: "", cancer: null, rule: null, selected: null };
  var DIMS = ["dependency", "selectivity", "clinical", "normal_expression",
              "genetic_constraint", "on_target_toxicity", "druggability"];
  var MARK = { favorable: "＋", unfavorable: "－", neutral: "·", unknown: "?" };
  var POL = { favorable: "支持推进", unfavorable: "提示风险", neutral: "中性", unknown: "未知" };

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function matches(t) {
    if (state.cancer && t.cancer_zh !== state.cancer) return false;
    if (state.rule && !t.conflicts.some(function (c) { return c.rule_id === state.rule; })) return false;
    if (state.q) {
      var hay = t.gene + " " + t.cancer + " " + t.cancer_zh + " " + t.verdict + " " +
        t.conflicts.map(function (c) { return c.rule_id + c.type_zh; }).join(" ");
      if (hay.toLowerCase().indexOf(state.q.toLowerCase()) === -1) return false;
    }
    return true;
  }

  function renderChips() {
    var cs = ['<span class="chip' + (state.cancer ? "" : " on") + '" data-c="">全部癌种</span>'];
    D.cancers.forEach(function (c) {
      cs.push('<span class="chip' + (state.cancer === c ? " on" : "") + '" data-c="' + esc(c) + '">' + esc(c) + "</span>");
    });
    $("cancers").innerHTML = cs.join("");

    var rs = ['<span class="chip' + (state.rule ? "" : " on") + '" data-r="">全部规则</span>'];
    D.rules.forEach(function (r) {
      rs.push('<span class="chip' + (state.rule === r.id ? " on" : "") + '" data-r="' + esc(r.id) +
        '" title="' + esc(r.label) + '">' + esc(r.id) + " " + esc(r.label) + "</span>");
    });
    $("rules").innerHTML = rs.join("");
  }

  function renderList() {
    var hits = D.targets.filter(matches);
    $("shown").textContent = hits.length;
    if (!hits.length) {
      $("list").innerHTML = '<div class="empty">没有匹配的靶点。试试清掉筛选条件。</div>';
      return;
    }
    $("list").innerHTML = hits.map(function (t) {
      var key = t.gene + "|" + t.cancer;
      var rs = t.conflicts.length
        ? t.conflicts.map(function (c) { return "<span>" + esc(c.rule_id) + "</span>"; }).join("")
        : '<span class="none">未见矛盾</span>';
      return '<button class="item' + (state.selected === key ? " on" : "") + '" data-k="' + esc(key) + '">' +
        '<div><span class="g">' + esc(t.gene) + '</span><span class="c">' + esc(t.cancer_zh) + "</span></div>" +
        '<div class="v">' + esc(t.verdict) + "</div>" +
        '<div class="rs">' + rs + "</div></button>";
    }).join("");
  }

  function conflictHTML(c) {
    var causes = c.causes.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("");
    var ex = "";
    if (c.explanation) {
      var m = c.explanation_meta || {};
      var origin = m.source !== "llm"
        ? "规则层候选原因（模型解释不可用或未通过校验）"
        : (m.provider === "stub" ? "确定性兜底（未调用模型）" : "模型 " + (m.model || m.provider || "未知"));
      ex = '<div class="explain">' + esc(c.explanation) +
        '<span class="src">解释来源：' + esc(origin) + " · 提示词指纹 " + esc(m.prompt_hash || "—") + "</span></div>";
    }
    return '<div class="conflict"><h4>[' + esc(c.rule_id) + "] " + esc(c.type_zh) + "</h4>" +
      '<div class="meta"><span>' + esc(c.headline) + "</span></div>" +
      '<div class="meta"><span>涉及维度：' + esc(c.dimension_labels.join(" × ")) + "</span>" +
      "<span>严重度 " + c.severity.toFixed(2) + '<span class="track"><i style="width:' +
      Math.round(c.severity * 100) + '%"></i></span></span></div>' +
      '<div class="exp">候选原因</div><ul>' + causes + "</ul>" +
      (c.experiment ? '<div class="exp">建议的区分实验：' + esc(c.experiment) + "</div>" : "") +
      ex + "</div>";
  }

  function evidenceHTML(t) {
    var out = "";
    ["tumor", "clinical", "normal", "feasibility"].forEach(function (g) {
      out += '<div class="group"><h4>' + esc(D.groups[g]) + "</h4>";
      DIMS.forEach(function (d) {
        var rs = t.evidence.filter(function (e) { return e.dimension === d; });
        if (!rs.length) {
          var miss = t.missing.filter(function (m) { return m.dimension === d; })[0];
          out += '<div class="ev"><div class="mark unknown">?</div><div class="body">' +
            '<p class="name">' + esc(miss ? miss.label : d) + ' <span class="badge unknown">证据不足</span></p>' +
            '<p class="text">' + esc(miss ? miss.hint : "") + "</p></div></div>";
        }
        rs.forEach(function (r) {
          var pct = r.percentile == null ? "分位 —" : "分位 " + r.percentile.toFixed(2);
          var src = r.url
            ? '<a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc(r.source) + "</a>"
            : esc(r.source);
          out += '<div class="ev"><div class="mark ' + esc(r.polarity) + '">' + esc(MARK[r.polarity]) + "</div>" +
            '<div class="body"><p class="name">' + esc(r.label) +
            ' <span class="badge ' + esc(r.polarity) + '">' + esc(POL[r.polarity]) + "</span></p>" +
            '<p class="nums">' + esc(pct) + " · 强度 " + r.strength.toFixed(2) +
            " · 置信度 " + r.confidence.toFixed(2) + " · " + src + "</p>" +
            '<p class="text">' + esc(r.statement) + "</p></div></div>";
        });
      });
      out += "</div>";
    });
    return out;
  }

  function heroHTML() {
    return '<div class="panel"><div class="hero">' +
      "<h1>在投入之前，先看清靶点的风险</h1>" +
      "<p>抗肿瘤药物从临床前到获批的整体失败率长期超过 90%，相当一部分并非倒在化合物优化，" +
      "而是倒在靶点选错。靶点尽调对候选靶点做一次自动化尽调：从七个公开数据源汇集证据，" +
      "把互相矛盾的地方自动揪出来并解释成因。</p>" +
      '<div class="sec">怎么用</div>' +
      "<ol>" +
      "<li>左侧列表挑一个靶点，或在搜索框直接输入基因名，例如 <code>KRAS</code>。</li>" +
      "<li>用癌种和规则标签筛，例如只看 <code>R1</code> 治疗窗狭窄命中的靶点。鼠标悬停标签可看规则全名。</li>" +
      "<li>右侧是完整审计卡：<b>先看冲突，再看证据矩阵</b>。每条证据都带原始来源链接，可点开核对。</li>" +
      "</ol>" +
      '<div class="sec">这个页面是什么，不是什么</div>' +
      "<p><b>它是</b>一个可以离线打开的审计结果集：所有结果在构建时由命令行工具算好并固化，" +
      "所以打开即用、不需要安装、不依赖网络。页面上每个数字和命令行跑出来的完全一致，" +
      "因为两者用的是同一个引擎。</p>" +
      "<p><b>它不是</b>在线计算服务：这里不能审计任意基因，那需要实时访问六个外部数据源。" +
      "要跑新的靶点，在本地执行 <code>python run.py audit 基因名 --cancer 癌种</code>，" +
      "详见仓库 README。</p>" +
      "</div></div>";
  }

  function dimensionStrip(t) {
    var cells = D.dimensions.map(function (d) {
      var rs = t.evidence.filter(function (e) { return e.dimension === d.value; });
      var pol = rs.length ? rs[0].polarity : "unknown";
      return '<span class="dim ' + esc(pol) + '" title="' + esc(d.label) + '">' +
        "<i></i>" + esc(d.short) + "</span>";
    });
    return '<div class="dimstrip">' + cells.join("") + "</div>";
  }

  function renderDetail() {
    if (!state.selected) { $("detail").innerHTML = heroHTML(); return; }
    var parts = state.selected.split("|");
    var t = D.targets.filter(function (x) { return x.gene === parts[0] && x.cancer === parts[1]; })[0];
    if (!t) { $("detail").innerHTML = heroHTML(); return; }
    var conflicts = t.conflicts.length
      ? t.conflicts.map(conflictHTML).join("")
      : '<p class="empty">未检出明显矛盾。</p>';
    var missing = t.missing.length
      ? '<div class="missing"><b>缺失证据</b><ul>' +
        t.missing.map(function (m) { return "<li>" + esc(m.label) + "（" + esc(m.hint) + "）</li>"; }).join("") +
        "</ul></div>"
      : "";
    $("detail").innerHTML = '<div class="panel">' +
      '<div class="verdict-panel"><p class="verdict">' + esc(t.verdict) + "</p>" +
      '<p class="headline">' + esc(t.headline) + "</p>" +
      '<div class="kv"><div><span>靶点</span><b>' + esc(t.gene) + "</b></div>" +
      "<div><span>癌种</span><b>" + esc(t.cancer_zh) + "（" + esc(t.cancer) + "）</b></div>" +
      "<div><span>治疗窗</span><b>" + esc(t.therapeutic_window) + "</b></div>" +
      "<div><span>证据覆盖</span><b>" + t.coverage.covered + "/" + t.coverage.total + " 个维度</b></div>" +
      "<div><span>检出冲突</span><b>" + t.conflicts.length + " 条</b></div></div>" +
      dimensionStrip(t) + "</div>" +
      '<div class="sec">冲突</div>' + conflicts +
      '<div class="sec">证据矩阵</div>' + evidenceHTML(t) + missing +
      '<footer class="btm">生成时间 ' + esc(t.generated_at) +
      " · 每个数值都可点回原始数据源</footer></div>";
    $("detail").scrollTop = 0;
  }

  document.addEventListener("click", function (e) {
    var chip = e.target.closest(".chip");
    if (chip) {
      if (chip.hasAttribute("data-c")) state.cancer = chip.getAttribute("data-c") || null;
      if (chip.hasAttribute("data-r")) state.rule = chip.getAttribute("data-r") || null;
      renderChips();
      renderList();
      return;
    }
    var item = e.target.closest(".item");
    if (item) {
      state.selected = item.getAttribute("data-k");
      renderList();
      renderDetail();
    }
  });

  $("q").addEventListener("input", function () {
    state.q = this.value.trim();
    renderList();
  });

  $("stat-targets").textContent = D.counts.targets;
  $("stat-cancers").textContent = D.counts.cancers;
  $("stat-rules").textContent = D.counts.rules_fired + "/" + D.counts.rules_total;
  renderChips();
  renderList();
  renderDetail();
})();
