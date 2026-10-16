"""治疗模态映射：把"要做哪一类药"翻译成"该看哪些证据"。

为什么需要这一层：七个维度的语义默认偏小分子。对降解剂，"结构里没有深口袋"
不构成硬伤，它要的是有可结合的配体；对 CAR-T，ChEMBL 的成药性根本不适用。
同一套标准套所有模态，会给出误导性的否定。

三条设计约束：

  1. **模态是有限集合，疾病是开放集合。** 所以要专家核对的是"六种模态各看什么"
     这十几条规则，而不是几十种疾病各写一套；疾病层面的差异作为备注与额外检索补充。
  2. **使用者可以覆盖。** 内置表之外，数据目录里可以放一份 user.json，
     补充或修改任意条目；不填就用内置的。覆盖是按字段生效的，不是整表替换。
  3. **每一条判断都记得是谁定的。** 合并之后逐项保留来源（内置/用户/专家）与依据状态，
     报告里可以标注"本条判断来自用户自定义标准"。这是这个工具可被信任的前提 ——
     评价标准本身不能是唯一一处没有出处的东西。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .dimensions import DIMENSION_META, label as dimension_label
from .paths import asset_path, data_root
from .schema import Dimension

# 受控词表：治疗模态。加新模态要同时补 MODALITY_META 与 default.json。
MODALITY_META: dict[str, str] = {
    "general": "未指定（通用标准）",
    "small_molecule": "小分子抑制剂",
    "antibody": "单抗",
    "adc": "抗体偶联药物（ADC）",
    "protac": "降解剂（PROTAC / 分子胶）",
    "cell_therapy": "细胞治疗（CAR-T / NK）",
    "oligo": "核酸药物（siRNA / ASO）",
}

VALID_DIMENSIONS = {d.value for d in Dimension}

# 作用域越具体，优先级越高；同一作用域下，来源越权威优先级越高。
SCOPE_SPECIFICITY = {"global": 0, "modality": 1, "cancer": 2, "gene": 3}
SOURCE_RANK = {"builtin": 1, "user": 2, "expert": 3}

SOURCE_LABEL = {"builtin": "内置默认", "user": "使用者自定义", "expert": "领域专家确认"}

# 依据状态。留空表示沿用条目自身的说明；"待补出处"是诚实的占位，
# 表示这条判断目前只有推理依据、没有可引用的出处，需要人核对。
BASIS_PENDING = "待补出处"
BASIS_ENGINEERING = "工程设定"
BASIS_CONFIRMED = "专家确认"

LIST_FIELDS = ("required", "not_applicable", "downweight", "extra_queries")


def modality_label(key: str) -> str:
    return MODALITY_META.get(key, key)


@dataclass(frozen=True)
class MappingRule:
    """一条评价标准。scope + key 决定它在什么情况下生效。"""

    scope: str
    key: str
    source: str = "builtin"
    author: str = ""
    basis: str = ""
    basis_status: str = BASIS_PENDING
    note: str = ""
    required: tuple[str, ...] = ()
    not_applicable: tuple[str, ...] = ()
    downweight: tuple[str, ...] = ()
    extra_queries: tuple[str, ...] = ()
    thresholds: dict[str, float] = field(default_factory=dict)

    @property
    def source_text(self) -> str:
        who = SOURCE_LABEL.get(self.source, self.source)
        if self.author:
            who = f"{who}（{self.author}）"
        return who

    @property
    def sort_key(self) -> tuple[int, int]:
        return (
            SCOPE_SPECIFICITY.get(self.scope, 0),
            SOURCE_RANK.get(self.source, 0),
        )


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def parse_rule(raw: dict, *, source: str, origin: str) -> tuple[MappingRule | None, str | None]:
    """解析一条规则。返回 (规则, 问题说明)。问题不抛异常 —— 用过的手改文件里
    出现一个笔误会毁掉整份配置，所以坏条目跳过、其余照常生效。"""

    scope = str(raw.get("scope", "")).strip().lower()
    key = str(raw.get("key", "")).strip()
    if scope not in SCOPE_SPECIFICITY:
        return None, f"{origin}：scope 取值不认识（{scope or '空'}），应为 {'/'.join(SCOPE_SPECIFICITY)}"
    if not key:
        return None, f"{origin}：缺少 key"
    if scope == "modality" and key != "*" and key not in MODALITY_META:
        return None, f"{origin}：未知模态 {key}，可用取值：{', '.join(sorted(MODALITY_META))}"

    thresholds: dict[str, float] = {}
    for dim, value in (raw.get("thresholds") or {}).items():
        if dim not in VALID_DIMENSIONS:
            return None, f"{origin}：thresholds 里的 {dim} 不是已知维度"
        try:
            thresholds[dim] = float(value)
        except (TypeError, ValueError):
            return None, f"{origin}：thresholds[{dim}] 不是数字（{value!r}）"

    lists: dict[str, tuple[str, ...]] = {}
    for name in LIST_FIELDS:
        items = _as_tuple(raw.get(name))
        bad = [i for i in items if i.lstrip("-") not in VALID_DIMENSIONS]
        # extra_queries 是自由文本，不做维度校验
        if name != "extra_queries" and bad:
            return None, f"{origin}：{name} 里有未知维度：{', '.join(bad)}"
        lists[name] = items

    rule = MappingRule(
        scope=scope,
        key=key if scope != "gene" else key.upper(),
        source=source,
        author=str(raw.get("author", "")),
        basis=str(raw.get("basis", "")),
        basis_status=str(raw.get("basis_status") or BASIS_PENDING),
        note=str(raw.get("note", "")),
        thresholds=thresholds,
        **lists,
    )
    return rule, None


def parse_rules(payload, *, source: str, origin: str) -> tuple[list[MappingRule], list[str]]:
    """接受 {rules: [...]} 或直接 [...] 两种写法。"""

    if isinstance(payload, dict):
        raw_rules = payload.get("rules") or []
    elif isinstance(payload, list):
        raw_rules = payload
    else:
        return [], [f"{origin}：顶层结构应为对象或数组"]

    rules: list[MappingRule] = []
    problems: list[str] = []
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            problems.append(f"{origin}：第 {index + 1} 条不是对象，已跳过")
            continue
        rule, problem = parse_rule(raw, source=source, origin=f"{origin} 第 {index + 1} 条")
        if problem:
            problems.append(problem)
        if rule is not None:
            rules.append(rule)
    return rules, problems


def load_file(path: Path, *, source: str) -> tuple[list[MappingRule], list[str]]:
    if not path.exists():
        return [], []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"{path.name} 读取失败：{exc}"]
    return parse_rules(payload, source=source, origin=path.name)


def builtin_path() -> Path:
    return asset_path("data", "mappings", "default.json")


def user_path() -> Path:
    """使用者自己填的那一份。放在可写数据目录，不随仓库分发。"""

    return data_root() / "mappings" / "user.json"


def load_rules(
    *,
    builtin: Path | None = None,
    user: Path | None = None,
) -> tuple[list[MappingRule], list[str]]:
    """内置规则 + 使用者覆盖。用户文件缺失是正常情况，不算问题。"""

    rules, problems = load_file(builtin or builtin_path(), source="builtin")
    user_rules, user_problems = load_file(user if user is not None else user_path(), source="user")
    return rules + user_rules, problems + user_problems


@dataclass
class EffectiveMapping:
    """合并后的生效标准。每个条目都带着它是谁定的。"""

    modality: str | None = None
    cancer: str | None = None
    gene: str | None = None
    required: list[str] = field(default_factory=list)
    not_applicable: list[str] = field(default_factory=list)
    downweight: list[str] = field(default_factory=list)
    extra_queries: list[str] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    item_source: dict[tuple[str, str], str] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)

    def source_of(self, field_name: str, item: str) -> str:
        return self.item_source.get((field_name, item), "未标注")

    def to_dict(self) -> dict:
        def decorated(name: str) -> list[dict]:
            return [
                {"item": item, "source": self.source_of(name, item)}
                for item in getattr(self, name)
            ]

        return {
            "modality": self.modality,
            "modality_label": modality_label(self.modality or "general"),
            "cancer": self.cancer,
            "gene": self.gene,
            "required": decorated("required"),
            "not_applicable": decorated("not_applicable"),
            "downweight": decorated("downweight"),
            "extra_queries": decorated("extra_queries"),
            "thresholds": self.thresholds,
            "notes": self.notes,
            "applied": self.applied,
        }


def _applies(rule: MappingRule, modality: str, cancer: str | None, gene: str | None) -> bool:
    if rule.scope == "global":
        return True
    if rule.scope == "modality":
        return rule.key in (modality, "*")
    if rule.scope == "cancer":
        return cancer is not None and rule.key.upper() in (cancer.upper(), "*")
    if rule.scope == "gene":
        return gene is not None and rule.key.upper() == gene.upper()
    return False


def resolve(
    modality: str | None = None,
    cancer: str | None = None,
    gene: str | None = None,
    *,
    rules: list[MappingRule] | None = None,
) -> EffectiveMapping:
    """把命中的规则按"越具体越优先、来源越权威越优先"依次叠加。

    列表字段是**并集**语义：更具体的条目往集合里加东西，用 "-维度名"
    可以取消上层已经加进来的条目。这样使用者要"只改一条"时不必重抄整表 ——
    这是让覆盖真正可用的前提。
    """

    modality = (modality or "general").strip().lower()
    if rules is None:
        rules, _ = load_rules()

    effective = EffectiveMapping(modality=modality, cancer=cancer, gene=gene)
    hits = [r for r in rules if _applies(r, modality, cancer, gene)]
    for rule in sorted(hits, key=lambda r: r.sort_key):
        scope_zh = {"global": "全局", "modality": "模态", "cancer": "癌种", "gene": "靶点"}[rule.scope]
        effective.applied.append(f"{scope_zh} {rule.key} · {rule.source_text}")
        for name in LIST_FIELDS:
            target: list[str] = getattr(effective, name)
            for item in getattr(rule, name):
                if item.startswith("-"):
                    stripped = item[1:]
                    if stripped in target:
                        target.remove(stripped)
                    effective.item_source.pop((name, stripped), None)
                    continue
                if item not in target:
                    target.append(item)
                effective.item_source[(name, item)] = rule.source_text
        for dim, value in rule.thresholds.items():
            effective.thresholds[dim] = value
            effective.item_source[("thresholds", dim)] = rule.source_text
        if rule.note:
            entry = f"{rule.note}（{rule.source_text}"
            entry += f"；依据状态：{rule.basis_status}" if rule.basis_status else ""
            entry += "）"
            if entry not in effective.notes:
                effective.notes.append(entry)
    return effective


def describe(effective: EffectiveMapping) -> list[str]:
    """给人看的说明，可直接放进报告或命令行输出。"""

    lines: list[str] = []
    lines.append(
        f"治疗模态：{modality_label(effective.modality or 'general')}"
        + (f" · 癌种 {effective.cancer}" if effective.cancer else "")
        + (f" · 靶点 {effective.gene}" if effective.gene else "")
    )

    def render(name: str, title: str) -> None:
        items = getattr(effective, name)
        if not items:
            return
        lines.append(f"{title}：")
        for item in items:
            if name == "extra_queries":
                text = item
            else:
                text = dimension_label(Dimension(item))
            lines.append(f"  - {text}（来源：{effective.source_of(name, item)}）")

    render("required", "必须看到的证据")
    render("not_applicable", "不适用（不应当作硬伤）")
    render("downweight", "降权")
    render("extra_queries", "需要额外查证")

    if effective.thresholds:
        lines.append("阈值覆盖：")
        for dim, value in sorted(effective.thresholds.items()):
            lines.append(
                f"  - {dimension_label(Dimension(dim))} = {value:g}"
                f"（来源：{effective.source_of('thresholds', dim)}）"
            )
    for note in effective.notes:
        lines.append(f"备注：{note}")
    if effective.applied:
        lines.append("生效规则：" + "；".join(effective.applied))
    return lines


def template_payload(modality: str = "general") -> dict:
    """给使用者一份可以直接改的模板：把内置标准导出成 user.json 的形状。"""

    effective = resolve(modality)
    return {
        "_说明": (
            "把本文件另存为 user.json（与 default.json 同一目录，或放在程序旁的 data/mappings/），"
            "即可覆盖内置标准。列表字段是并集语义：写上条目表示新增，"
            "写 \"-维度名\" 表示取消上层已加进来的条目。"
        ),
        "_依据状态": f"可选：{BASIS_PENDING} / {BASIS_ENGINEERING} / {BASIS_CONFIRMED}",
        "rules": [
            {
                "scope": "modality",
                "key": modality,
                "source": "user",
                "author": "填写你的名字或单位",
                "basis": "填写这条判断的出处（文献、指南，或写清是哪位老师确认的）",
                "basis_status": BASIS_PENDING,
                "note": "示例：本模态下我认为还应额外关注 X",
                "required": [],
                "not_applicable": [],
                "downweight": [],
                "extra_queries": [],
                "thresholds": {dim: effective.thresholds[dim] for dim in effective.thresholds},
            }
        ],
    }
