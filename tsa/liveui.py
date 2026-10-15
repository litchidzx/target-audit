"""实时审计页面的服务端渲染。

与 webexport.py 的区别：
  * webexport 面向"结果集"，数据在构建时固化，前端做交互
  * 本模块面向"单次查询"，服务端跑完引擎直接渲染 HTML

两者共用同一套 CSS 与视觉语言，所以看起来是同一个产品。

产物是纯静态 HTML 字符串，不依赖模板引擎，也没有第三方依赖。
"""

from __future__ import annotations

from html import escape as esc

from .dimensions import DIMENSION_META, label, short_label
from .paths import asset_path
from .schema import CAUSE_LABEL_ZH, AuditCard, EvidenceRecord, Group, Polarity

ASSETS = asset_path("tsa", "webassets")

GROUP_TITLE = {
    Group.TUMOR: "肿瘤侧 · 有多重要、有多独特",
    Group.CLINICAL: "临床侧 · 真实患者中是什么样",
    Group.NORMAL: "正常侧 · 抑制它会不会伤到正常组织",
    Group.FEASIBILITY: "可做性 · 做不做得出来",
}

MARK = {
    Polarity.FAVORABLE: "＋",
    Polarity.UNFAVORABLE: "－",
    Polarity.NEUTRAL: "·",
    Polarity.UNKNOWN: "?",
}
POL_TEXT = {
    Polarity.FAVORABLE: "支持推进",
    Polarity.UNFAVORABLE: "提示风险",
    Polarity.NEUTRAL: "中性",
    Polarity.UNKNOWN: "未知",
}

PAGE_CSS = """
/* 只放表单页与结果页特有的部分；基础样式来自 webassets/style.css */
.bar{background:#fff;border-bottom:1px solid var(--line);padding:13px 22px;display:flex;
     align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;
     position:sticky;top:0;z-index:20}
.bar .brand{font-weight:700;font-size:16px;letter-spacing:-.01em}
.bar .brand small{display:block;font-weight:400;color:var(--muted);font-size:11px;letter-spacing:.1em}
.bar a{font-size:13.5px;font-weight:500}
.shell{max-width:940px;margin:0 auto;padding:30px 22px 72px}
.query{background:#fff;border:1px solid var(--line);border-radius:16px;padding:26px 28px;
       margin:0 0 24px;box-shadow:var(--shadow)}
.query h1{font-size:24px;margin:0 0 8px;letter-spacing:-.02em}
.query p.hint{margin:0 0 20px;color:var(--muted);font-size:14px;max-width:640px}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.field{display:flex;flex-direction:column;gap:6px}
.field label{font-size:12.5px;color:var(--muted);font-weight:600}
.field input,.field select{
  padding:11px 14px;border:1px solid var(--line);border-radius:10px;font-size:14.5px;
  font-family:inherit;background:var(--bg);min-width:190px;color:var(--ink);
  transition:border-color .15s, box-shadow .15s, background .15s;
}
.field input:focus,.field select:focus{
  outline:0;border-color:var(--accent);background:#fff;box-shadow:0 0 0 3px var(--accent-soft);
}
.go{padding:11px 24px;border:0;border-radius:10px;background:var(--accent);color:#fff;
    font-size:14.5px;font-weight:600;font-family:inherit;cursor:pointer;
    transition:filter .15s, transform .1s;box-shadow:0 2px 6px rgba(31,79,209,.24)}
.go:hover{filter:brightness(1.08)}
.go:active{transform:translateY(1px)}
.go:disabled{opacity:.65;cursor:default;filter:none;transform:none}
.samples{margin:18px 0 0;font-size:13.5px;color:var(--muted);
         border-top:1px solid var(--line);padding-top:14px}
.samples a{margin-right:14px;white-space:nowrap;font-weight:500}
.notice{background:var(--accent-soft);border-radius:12px;padding:14px 18px;margin:0 0 20px;
        font-size:13.5px;color:#26344d;white-space:pre-line}
.notice.warn{background:var(--unfav-bg);color:#6b241b}
.spin{display:inline-block;width:14px;height:14px;border:2px solid rgba(31,79,209,.25);
      border-top-color:var(--accent);border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px}
@keyframes sp{to{transform:rotate(360deg)}}
.busy{display:none;align-items:center;gap:10px;color:var(--muted);font-size:13.5px;margin-top:16px}
.busy.on{display:flex}
@media (max-width:640px){
  .shell{padding:20px 16px 48px}
  .query{padding:20px}
  .field input,.field select{min-width:100%}
  .row{flex-direction:column;align-items:stretch}
}
"""


def full_css() -> str:
    """基础样式表 + 页面特有样式。

    必须合并。早先只返回了 PAGE_CSS，结果结果页的类名（.ev、.conflict、
    .verdict-panel 等）全都没有定义，页面以裸 HTML 呈现。
    """
    base = (ASSETS / "style.css").read_text(encoding="utf-8")
    return base + "\n" + PAGE_CSS


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="/style.css">
</head>
<body>
<div class="bar">
  <div class="brand">靶点尽调 · 实时审计<small>TARGET SELECTIVITY AUDIT</small></div>
  <a href="/">换一个靶点</a>
</div>
<div class="shell">{body}</div>
</body>
</html>
"""


def render_form(
    cancers: list[tuple[str, str]],
    *,
    gene: str = "",
    cancer: str = "",
    notice: str = "",
    notice_kind: str = "",
    samples: list[tuple[str, str]] | None = None,
) -> str:
    options = "".join(
        f'<option value="{esc(code)}"{" selected" if code == cancer else ""}>{esc(zh)}（{esc(code)}）</option>'
        for code, zh in cancers
    )
    sample_html = ""
    if samples:
        links = "".join(
            f'<a href="/audit?gene={esc(g)}&cancer={esc(c)}">{esc(g)} / {esc(zh)}</a>'
            for g, c, zh in samples  # type: ignore[misc]
        )
        sample_html = f'<p class="samples">直接试一个：{links}</p>'

    notice_html = ""
    if notice:
        kind = f" {esc(notice_kind)}" if notice_kind else ""
        notice_html = f'<div class="notice{kind}">{notice}</div>'

    body = f"""
{notice_html}
<div class="query">
  <h1>审计一个候选靶点</h1>
  <p class="hint">输入基因符号与目标癌种，系统会实时访问七个公开数据源，检出证据矛盾并给出治疗窗判断。
     首次查询一个基因约需 20 到 30 秒，之后走缓存会快很多。</p>
  <form method="get" action="/audit" id="form">
    <div class="row">
      <div class="field">
        <label for="gene">基因符号</label>
        <input id="gene" name="gene" value="{esc(gene)}" placeholder="例如 KRAS" required
               autocapitalize="characters" autocomplete="off" spellcheck="false">
      </div>
      <div class="field">
        <label for="cancer">目标癌种</label>
        <select id="cancer" name="cancer">{options}</select>
      </div>
      <button class="go" type="submit" id="submit">开始审计</button>
    </div>
    <div class="busy" id="busy"><span class="spin"></span>正在访问数据源，请稍候……</div>
  </form>
  {sample_html}
</div>
<script>
document.getElementById("form").addEventListener("submit", function () {{
  document.getElementById("busy").classList.add("on");
  var b = document.getElementById("submit");
  b.disabled = true;
  b.textContent = "审计中…";
}});
</script>
"""
    return _shell("靶点尽调 · 实时审计", body)


def _evidence_html(record: EvidenceRecord) -> str:
    pct = f"分位 {record.percentile:.2f}" if record.percentile is not None else "分位 —"
    src = (
        f'<a href="{esc(record.url)}" target="_blank" rel="noopener">{esc(record.source)}</a>'
        if record.url
        else esc(record.source)
    )
    cls = record.polarity.value
    return f"""<div class="ev">
  <div class="mark {esc(cls)}">{esc(MARK[record.polarity])}</div>
  <div class="body">
    <p class="name">{esc(label(record.dimension))}
      <span class="badge {esc(cls)}">{esc(POL_TEXT[record.polarity])}</span></p>
    <p class="nums">{esc(pct)} · 强度 {record.strength:.2f} · 置信度 {record.confidence:.2f} · {src}</p>
    <p class="text">{esc(record.statement)}</p>
  </div>
</div>"""


def _dimension_strip(card: AuditCard) -> str:
    """七维方向总览。一眼看到哪些维度支持、哪些反对、哪些没数据。"""
    by_dim = card.by_dimension()
    cells = []
    for dim in DIMENSION_META:
        records = by_dim[dim]
        ranked = sorted(
            records,
            key=lambda r: (r.polarity is not Polarity.UNKNOWN, r.strength),
            reverse=True,
        )
        polarity = ranked[0].polarity if ranked else Polarity.UNKNOWN
        cells.append(
            f'<span class="dim {esc(polarity.value)}" title="{esc(label(dim))}">'
            f'<i></i>{esc(short_label(dim))}</span>'
        )
    return f'<div class="dimstrip">{"".join(cells)}</div>'


def render_result(card: AuditCard) -> str:
    by_dim = card.by_dimension()
    groups = []
    for group in (Group.TUMOR, Group.CLINICAL, Group.NORMAL, Group.FEASIBILITY):
        items = []
        for dim in [d for d, m in DIMENSION_META.items() if m.group is group]:
            records = by_dim[dim]
            if not records:
                items.append(
                    f'<div class="ev"><div class="mark unknown">?</div><div class="body">'
                    f'<p class="name">{esc(label(dim))} <span class="badge unknown">证据不足</span></p>'
                    f'<p class="text">{esc(DIMENSION_META[dim].source_hint)}</p></div></div>'
                )
            for record in records:
                items.append(_evidence_html(record))
        groups.append(f'<div class="group"><h4>{esc(GROUP_TITLE[group])}</h4>{"".join(items)}</div>')

    if card.conflicts:
        blocks = []
        for c in card.conflicts:
            dims = list(dict.fromkeys(label(d) for d in c.dimensions))
            if len(dims) == 1:
                metrics = " / ".join(dict.fromkeys(r.metric for r in c.evidence))
                dim_text = f"{dims[0]}（{metrics}）"
            else:
                dim_text = " × ".join(dims)
            causes = "".join(f"<li>{esc(CAUSE_LABEL_ZH[x])}</li>" for x in c.cause_candidates)
            explain = ""
            if c.explanation:
                meta = c.metadata or {}
                origin = (
                    "规则层候选原因（模型解释不可用或未通过校验）"
                    if meta.get("source") != "llm"
                    else (
                        "确定性兜底（未调用模型）"
                        if meta.get("provider") == "stub"
                        else f"模型 {meta.get('model') or meta.get('provider') or '未知'}"
                    )
                )
                explain = (
                    f'<div class="explain">{esc(c.explanation)}'
                    f'<span class="src">解释来源：{esc(origin)}</span></div>'
                )
            blocks.append(f"""<div class="conflict">
  <h4>[{esc(c.rule_id)}] {esc(c.headline)}</h4>
  <div class="meta"><span>涉及维度：{esc(dim_text)}</span>
    <span>严重度 {c.severity:.2f}<span class="track"><i style="width:{int(c.severity * 100)}%"></i></span></span></div>
  <div class="exp">候选原因</div><ul>{causes}</ul>
  {f'<div class="exp">建议的区分实验：{esc(c.suggested_experiment)}</div>' if c.suggested_experiment else ''}
  {explain}
</div>""")
        conflict_html = "".join(blocks)
    else:
        conflict_html = '<p class="empty">未检出明显矛盾。</p>'

    missing = ""
    if card.missing_dimensions:
        lis = "".join(
            f"<li>{esc(label(d))}（{esc(DIMENSION_META[d].source_hint)}）</li>"
            for d in card.missing_dimensions
        )
        missing = f'<div class="missing"><b>缺失证据</b><ul>{lis}</ul></div>'

    total = len(list(DIMENSION_META))
    covered = total - len(card.missing_dimensions)
    body = f"""
<div class="verdict-panel">
  <p class="verdict">{esc(card.verdict)}</p>
  <p class="headline">{esc(card.headline)}</p>
  <div class="kv">
    <div><span>靶点</span><b>{esc(card.gene)}</b></div>
    <div><span>癌种</span><b>{esc(card.cancer)}</b></div>
    <div><span>治疗窗</span><b>{esc(card.therapeutic_window)}</b></div>
    <div><span>证据覆盖</span><b>{covered}/{total} 个维度</b></div>
    <div><span>检出冲突</span><b>{len(card.conflicts)} 条</b></div>
  </div>
  {_dimension_strip(card)}
</div>
<div class="sec">冲突</div>
{conflict_html}
<div class="sec">证据矩阵</div>
{"".join(groups)}
{missing}
<footer class="btm">生成时间 {esc(card.generated_at)} · 每个数值都可点回原始数据源</footer>
"""
    return _shell(f"{card.gene} / {card.cancer} · 靶点尽调", body)


def render_error(message: str, *, hint: str = "") -> str:
    body = f"""
<div class="query">
  <h1>这次审计没有完成</h1>
  <p class="hint">{esc(message)}</p>
  {f'<p class="hint">{esc(hint)}</p>' if hint else ''}
  <p><a href="/">返回重新输入</a></p>
</div>
"""
    return _shell("审计未完成 · 靶点尽调", body)
