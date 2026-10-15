"""把审计卡渲染成静态 HTML。

生成的是纯静态站点，不需要后端，可以直接托管在 GitHub Pages 之类的
静态服务上。这样"能点开看的成果链接"和"能现场演示的界面"是同一个东西。

设计取舍：没有做前后端分离的交互式工作台，而是把审计结果预先算好、
渲染成静态页面。原因是演示场景下网络和外部接口都不可控，
预渲染能保证现场一定打得开，也不会因为某个 API 抽风而演示失败。
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Iterable

from .dimensions import DIMENSION_META, label
from .schema import (
    AuditCard,
    CAUSE_LABEL_ZH,
    Conflict,
    EvidenceRecord,
    Group,
    Polarity,
)

CSS = """
:root{
  --ink:#12161c; --muted:#5b6675; --line:#e4e9f0; --bg:#f6f8fb; --card:#fff;
  --fav:#177a4a; --fav-bg:#e8f6ee;
  --unfav:#bb3a2c; --unfav-bg:#fdeceb;
  --neutral:#7b8698; --neutral-bg:#f1f3f7;
  --unknown:#aab2bf; --unknown-bg:#f5f6f8;
  --accent:#1f4fd1; --accent-soft:#eef2fe;
  --radius:12px;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei","Hiragino Sans GB",sans-serif;
  line-height:1.7; -webkit-font-smoothing:antialiased;
}
a{color:var(--accent); text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1080px; margin:0 auto; padding:0 24px}
header.site{
  background:#fff; border-bottom:1px solid var(--line); padding:16px 0; position:sticky; top:0; z-index:10;
}
header.site .wrap{display:flex; align-items:center; justify-content:space-between; gap:16px}
.brand{font-weight:700; font-size:16px; letter-spacing:.01em}
.brand small{display:block; font-weight:400; color:var(--muted); font-size:12px; letter-spacing:.06em}
.hero{padding:64px 0 40px}
.hero h1{font-size:38px; line-height:1.25; margin:0 0 16px; letter-spacing:-.01em}
.hero p.lead{font-size:17px; color:var(--muted); max-width:720px; margin:0 0 28px}
.stats{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin:0 0 8px}
.stat{background:#fff; border:1px solid var(--line); border-radius:var(--radius); padding:16px 18px}
.stat b{display:block; font-size:24px; line-height:1.2}
.stat span{color:var(--muted); font-size:13px}
.section-title{font-size:13px; letter-spacing:.08em; color:var(--muted); text-transform:uppercase; margin:40px 0 14px; font-weight:600}
.grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:16px}
.card{
  background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  padding:20px; display:flex; flex-direction:column; gap:12px; transition:box-shadow .15s, transform .15s;
}
.card:hover{box-shadow:0 6px 20px rgba(20,30,50,.08); transform:translateY(-1px)}
.card h3{margin:0; font-size:19px; display:flex; align-items:baseline; gap:8px}
.card h3 .cancer{font-size:12px; font-weight:600; color:var(--accent); background:var(--accent-soft); padding:3px 8px; border-radius:99px}
.card .verdict{font-weight:600; font-size:14px}
.card .headline{color:var(--muted); font-size:13.5px; margin:0}
.tags{display:flex; flex-wrap:wrap; gap:6px; margin-top:auto}
.tag{font-size:11.5px; padding:3px 8px; border-radius:6px; background:var(--neutral-bg); color:var(--muted); font-weight:600; letter-spacing:.02em}
.tag.win{background:var(--accent-soft); color:var(--accent)}

.badge{display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:700; padding:4px 10px; border-radius:99px}
.badge.fav{background:var(--fav-bg); color:var(--fav)}
.badge.unfav{background:var(--unfav-bg); color:var(--unfav)}
.badge.neutral{background:var(--neutral-bg); color:var(--neutral)}
.badge.unknown{background:var(--unknown-bg); color:var(--unknown)}

.verdict-panel{background:#fff; border:1px solid var(--line); border-radius:var(--radius); padding:24px; margin:28px 0}
.verdict-panel .verdict{font-size:22px; font-weight:700; margin:0 0 8px}
.verdict-panel .headline{margin:0 0 16px; color:var(--ink); font-size:15px}
.kv{display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:14px; border-top:1px solid var(--line); padding-top:16px}
.kv div span{display:block; color:var(--muted); font-size:12.5px}
.kv div b{font-size:14.5px; font-weight:600}

.conflict{background:#fff; border:1px solid var(--line); border-left:3px solid var(--unfav); border-radius:var(--radius); padding:20px; margin:0 0 14px}
.conflict h4{margin:0 0 6px; font-size:15.5px}
.conflict .meta{display:flex; flex-wrap:wrap; gap:16px; color:var(--muted); font-size:13px; margin:0 0 12px}
.sev{display:inline-block; height:5px; border-radius:3px; background:var(--unfav); vertical-align:middle}
.sev-track{display:inline-block; width:80px; height:5px; border-radius:3px; background:var(--line); vertical-align:middle; margin-left:8px}
.conflict ul{margin:6px 0 12px; padding-left:20px; color:var(--muted); font-size:14px}
.explain{background:var(--accent-soft); border-radius:8px; padding:12px 14px; font-size:14px; margin:10px 0 0}
.explain .src{display:block; color:var(--muted); font-size:12px; margin-top:6px}

.group{margin:26px 0 0}
.group h4{font-size:14px; margin:0 0 10px; color:var(--muted); font-weight:600}
.ev{background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:0 0 10px; display:flex; gap:14px}
.ev .mark{font-size:18px; font-weight:700; width:22px; text-align:center; flex:0 0 22px; line-height:1.5}
.ev .mark.fav{color:var(--fav)} .ev .mark.unfav{color:var(--unfav)}
.ev .mark.neutral{color:var(--neutral)} .ev .mark.unknown{color:var(--unknown)}
.ev .body{flex:1; min-width:0}
.ev .name{font-weight:600; font-size:14.5px; margin:0 0 2px}
.ev .nums{color:var(--muted); font-size:12.5px; margin:0 0 6px}
.ev .text{font-size:13.5px; color:#2c3440; margin:0}
.ev .src-link{font-size:12.5px}

.missing{background:#fff; border:1px dashed var(--line); border-radius:var(--radius); padding:18px 20px; margin:28px 0}
.missing ul{margin:8px 0 0; padding-left:20px; color:var(--muted); font-size:14px}
footer.site{border-top:1px solid var(--line); margin-top:56px; padding:28px 0 48px; color:var(--muted); font-size:13px; background:#fff}
.filter{display:flex; gap:10px; flex-wrap:wrap; margin:0 0 18px}
.filter input{flex:1; min-width:200px; padding:10px 14px; border:1px solid var(--line); border-radius:9px; font-size:14px; font-family:inherit; background:#fff}
.filter input:focus{outline:2px solid var(--accent-soft); border-color:var(--accent)}
.empty{color:var(--muted); padding:24px 0}
@media (max-width:640px){
  .hero{padding:40px 0 28px} .hero h1{font-size:28px}
  .grid{grid-template-columns:1fr}
}
"""


def esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def band(value: float) -> str:
    """把分位落在一个 0-1 的区间里，用于展示强度条。"""
    return f"{max(0.0, min(1.0, float(value))) * 100:.0f}%"


POLARITY_TEXT = {
    Polarity.FAVORABLE: "支持推进",
    Polarity.UNFAVORABLE: "提示风险",
    Polarity.NEUTRAL: "中性",
    Polarity.UNKNOWN: "未知",
}
POLARITY_MARK = {
    Polarity.FAVORABLE: "＋",
    Polarity.UNFAVORABLE: "－",
    Polarity.NEUTRAL: "·",
    Polarity.UNKNOWN: "?",
}
GROUP_TITLE = {
    Group.TUMOR: "肿瘤侧 · 有多重要、有多独特",
    Group.CLINICAL: "临床侧 · 真实患者中是什么样",
    Group.NORMAL: "正常侧 · 抑制它会不会伤到正常组织",
    Group.FEASIBILITY: "可做性 · 做不做得出来",
}


def slug(card: AuditCard) -> str:
    raw = f"{card.gene}-{card.cancer}".upper()
    return re.sub(r"[^A-Z0-9]+", "-", raw).strip("-").lower()


def _page(title: str, body: str, *, nav_href: str = "index.html") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="site"><div class="wrap">
  <div class="brand">靶点尽调<small>TARGET SELECTIVITY AUDIT</small></div>
  <nav><a href="{esc(nav_href)}">全部靶点</a></nav>
</div></header>
{body}
<footer class="site"><div class="wrap">
  本系统为面向科研的靶点证据整理工具，不输出诊断、治疗或用药建议，不构成任何医疗决策依据。
  所有结论均可通过页面上的来源链接回溯至原始数据源。
</div></footer>
</body>
</html>
"""


def _evidence_block(record: EvidenceRecord) -> str:
    cls = record.polarity.value
    pct = f"分位 {record.percentile:.2f}" if record.percentile is not None else "分位 —"
    src = (
        f'<a class="src-link" href="{esc(record.url)}" target="_blank" rel="noopener">{esc(record.source)}</a>'
        if record.url
        else esc(record.source)
    )
    return f"""<div class="ev">
  <div class="mark {esc(cls)}">{esc(POLARITY_MARK[record.polarity])}</div>
  <div class="body">
    <p class="name">{esc(label(record.dimension))} <span class="badge {esc(cls)}">{esc(POLARITY_TEXT[record.polarity])}</span></p>
    <p class="nums">{esc(pct)} · 强度 {record.strength:.2f} · 置信度 {record.confidence:.2f} · {src}</p>
    <p class="text">{esc(record.statement)}</p>
  </div>
</div>"""


def _conflict_block(conflict: Conflict) -> str:
    labels = list(dict.fromkeys(label(d) for d in conflict.dimensions))
    if len(labels) == 1:
        metrics = " / ".join(dict.fromkeys(r.metric for r in conflict.evidence))
        dims = f"{labels[0]}（{metrics}）"
    else:
        dims = " × ".join(labels)

    causes = "".join(f"<li>{esc(CAUSE_LABEL_ZH[c])}</li>" for c in conflict.cause_candidates)
    explanation = ""
    if conflict.explanation:
        meta = conflict.metadata or {}
        provider = meta.get("provider", "")
        if meta.get("source") != "llm":
            origin = "规则层候选原因（模型解释不可用或未通过校验）"
        elif provider == "stub":
            origin = "确定性兜底（未调用模型）"
        else:
            origin = f"模型 {meta.get('model') or provider or '未知'}"
        explanation = (
            f'<div class="explain">{esc(conflict.explanation)}'
            f'<span class="src">解释来源：{esc(origin)} · 提示词指纹 {esc(meta.get("prompt_hash", "—"))}</span></div>'
        )

    return f"""<div class="conflict">
  <h4>[{esc(conflict.rule_id)}] {esc(conflict.headline)}</h4>
  <div class="meta">
    <span>涉及维度：{esc(dims)}</span>
    <span>严重度 {conflict.severity:.2f}<span class="sev-track"><span class="sev" style="width:{band(conflict.severity)}"></span></span></span>
  </div>
  <div>候选原因</div>
  <ul>{causes}</ul>
  {f'<div>建议的区分实验：{esc(conflict.suggested_experiment)}</div>' if conflict.suggested_experiment else ''}
  {explanation}
</div>"""


def render_detail(card: AuditCard) -> str:
    by_dim = card.by_dimension()
    groups_html = []
    for group in (Group.TUMOR, Group.CLINICAL, Group.NORMAL, Group.FEASIBILITY):
        dims = [d for d, m in DIMENSION_META.items() if m.group is group]
        items = []
        for d in dims:
            records = by_dim[d]
            if not records:
                items.append(
                    f'<div class="ev"><div class="mark unknown">?</div><div class="body">'
                    f'<p class="name">{esc(label(d))} <span class="badge unknown">证据不足</span></p>'
                    f'<p class="text">{esc(DIMENSION_META[d].source_hint)}</p></div></div>'
                )
            for r in records:
                items.append(_evidence_block(r))
        groups_html.append(
            f'<div class="group"><h4>{esc(GROUP_TITLE[group])}</h4>{"".join(items)}</div>'
        )

    conflicts_html = (
        "".join(_conflict_block(c) for c in card.conflicts)
        if card.conflicts
        else '<p class="empty">未检出明显矛盾。</p>'
    )

    missing_html = ""
    if card.missing_dimensions:
        lis = "".join(
            f"<li>{esc(label(d))}（{esc(DIMENSION_META[d].source_hint)}）</li>"
            for d in card.missing_dimensions
        )
        missing_html = f'<div class="missing"><b>缺失证据</b><ul>{lis}</ul></div>'

    body = f"""<main class="wrap">
  <div class="verdict-panel">
    <p class="verdict">{esc(card.verdict)}</p>
    <p class="headline">{esc(card.headline)}</p>
    <div class="kv">
      <div><span>靶点</span><b>{esc(card.gene)}</b></div>
      <div><span>癌种</span><b>{esc(card.cancer)}</b></div>
      <div><span>治疗窗</span><b>{esc(card.therapeutic_window)}</b></div>
      <div><span>检出冲突</span><b>{len(card.conflicts)} 条</b></div>
    </div>
  </div>
  <div class="section-title">冲突</div>
  {conflicts_html}
  <div class="section-title">证据矩阵</div>
  {"".join(groups_html)}
  {missing_html}
  <p class="empty" style="margin-top:28px">生成时间 {esc(card.generated_at)}</p>
</main>"""
    return _page(f"{card.gene} / {card.cancer} · 靶点尽调", body)


def render_index(cards: list[AuditCard], *, generated_at: str | None = None) -> str:
    total_conflicts = sum(len(c.conflicts) for c in cards)
    cancers = sorted({c.cancer for c in cards})
    rules = sorted({conf.rule_id for c in cards for conf in c.conflicts})

    card_html = []
    for c in cards:
        tags = "".join(
            f'<span class="tag win">{esc(conf.rule_id)}</span>' for conf in c.conflicts
        ) or '<span class="tag">未检出冲突</span>'
        search = f"{c.gene} {c.cancer} {c.verdict} {' '.join(x.rule_id for x in c.conflicts)}".lower()
        card_html.append(f"""<a class="card" data-search="{esc(search)}" href="{esc(slug(c))}.html">
  <h3>{esc(c.gene)} <span class="cancer">{esc(c.cancer)}</span></h3>
  <p class="verdict">{esc(c.verdict)}</p>
  <p class="headline">{esc(c.headline)}</p>
  <div class="tags">{tags}</div>
</a>""")

    body = f"""<main class="wrap">
  <section class="hero">
    <h1>在投入之前，先看清靶点的风险</h1>
    <p class="lead">
      抗肿瘤药物从临床前到获批的整体失败率长期超过 90%，相当一部分并非倒在化合物优化，
      而是倒在靶点选错。靶点尽调对候选靶点做一次自动化尽调：从七个公开数据源汇集证据，
      把互相矛盾的地方自动揪出来并解释成因，给出治疗窗判断。
    </p>
    <div class="stats">
      <div class="stat"><b>{len(cards)}</b><span>已审计靶点</span></div>
      <div class="stat"><b>{len(cancers)}</b><span>覆盖癌种</span></div>
      <div class="stat"><b>7</b><span>证据维度</span></div>
      <div class="stat"><b>{len(rules)}/7</b><span>已触发的冲突规则</span></div>
    </div>
  </section>
  <div class="section-title">审计结果</div>
  <div class="filter"><input id="q" type="search" placeholder="按靶点、癌种或规则编号筛选，例如 KRAS、PAAD、R1"></div>
  <div class="grid" id="grid">{"".join(card_html)}</div>
  <p class="empty" id="none" style="display:none">没有匹配的靶点。</p>
</main>
<script>
var input=document.getElementById("q"),cards=[].slice.call(document.querySelectorAll("#grid .card")),none=document.getElementById("none");
input.addEventListener("input",function(){{
  var q=input.value.trim().toLowerCase(),shown=0;
  cards.forEach(function(el){{
    var hit=!q||el.getAttribute("data-search").indexOf(q)>-1;
    el.style.display=hit?"":"none";
    if(hit)shown++;
  }});
  none.style.display=shown?"none":"block";
}});
</script>"""
    stamp = generated_at or datetime.now().strftime("%Y-%m-%d")
    return _page("靶点尽调 · 示例审计结果", body).replace(
        "</footer>", f" · 生成于 {esc(stamp)}</footer>"
    )


def write_site(cards: Iterable[AuditCard], out_dir) -> list[str]:
    """把一组审计卡写成静态站点，返回生成的文件列表。"""
    from pathlib import Path

    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "assets" / "style.css").write_text(CSS, encoding="utf-8")

    cards = list(cards)
    written = ["assets/style.css"]
    (out / "index.html").write_text(render_index(cards), encoding="utf-8")
    written.append("index.html")
    for card in cards:
        name = f"{slug(card)}.html"
        (out / name).write_text(render_detail(card), encoding="utf-8")
        written.append(name)
    return written
