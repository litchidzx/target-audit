"""治疗模态映射的单元测试。

重点覆盖三件事：覆盖能不能生效（并集与取消语义）、每条判断是否记得来源、
以及使用者手改的文件出问题时是否只损失那一条而不毁掉整份配置。
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tsa.mapping import (  # noqa: E402
    BASIS_ENGINEERING,
    MappingRule,
    builtin_path,
    describe,
    load_rules,
    parse_rules,
    resolve,
    template_payload,
)

# 测试用的临时目录放在仓库内。不用系统临时目录，是因为受限环境下那里可能不可写
# （本项目就是在一个会拦住 %TEMP% 写入的沙箱里开发的），而测试跑不起来会掩盖真问题。
# 也不用 tempfile.mkdtemp：它在 Windows 上按 0700 建目录，受限环境里写进去会被拒。
TMP_ROOT = Path(__file__).resolve().parent / ".tmp"


def make_workdir() -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        candidate = TMP_ROOT / ("case_" + uuid.uuid4().hex[:12])
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError(f"无法在 {TMP_ROOT} 下创建临时目录")


class TestBuiltinTable(unittest.TestCase):
    def test_builtin_file_is_valid(self):
        rules, problems = load_rules(builtin=builtin_path(), user=Path("__不存在__/user.json"))
        self.assertEqual(problems, [])
        self.assertGreater(len(rules), 5)

    def test_antibody_marks_druggability_not_applicable(self):
        effective = resolve("antibody", rules=load_rules(user=Path("__none__"))[0])
        self.assertIn("druggability", effective.not_applicable)
        self.assertIn("normal_expression", effective.required)

    def test_cell_therapy_excludes_molecule_level_dimensions(self):
        effective = resolve("cell_therapy", rules=load_rules(user=Path("__none__"))[0])
        self.assertIn("druggability", effective.not_applicable)
        self.assertIn("on_target_toxicity", effective.not_applicable)

    def test_general_modality_has_no_exclusions(self):
        effective = resolve(None, rules=load_rules(user=Path("__none__"))[0])
        self.assertEqual(effective.not_applicable, [])
        self.assertEqual(effective.required, [])

    def test_gene_and_cancer_scope_are_merged(self):
        rules, _ = load_rules(user=Path("__none__"))
        effective = resolve("small_molecule", "PAAD", "KRAS", rules=rules)
        joined = " ".join(effective.extra_queries)
        self.assertIn("变异构成", joined)  # 来自 gene/KRAS
        self.assertIn("递送", joined)  # 来自 cancer/PAAD
        self.assertIn("亚型", joined)  # 来自 modality/small_molecule

    def test_every_item_records_its_source(self):
        rules, _ = load_rules(user=Path("__none__"))
        effective = resolve("antibody", rules=rules)
        self.assertEqual(effective.source_of("not_applicable", "druggability"), "内置默认")


class TestUserOverride(unittest.TestCase):
    def setUp(self):
        self.dir = make_workdir()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_user(self, payload) -> Path:
        path = self.dir / "user.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_user_rule_adds_to_builtin(self):
        user = self.write_user(
            {
                "rules": [
                    {
                        "scope": "modality",
                        "key": "antibody",
                        "source": "user",
                        "author": "某课题组",
                        "extra_queries": ["本课题组关心的额外指标"],
                    }
                ]
            }
        )
        rules, problems = load_rules(user=user)
        self.assertEqual(problems, [])
        effective = resolve("antibody", rules=rules)
        self.assertIn("本课题组关心的额外指标", effective.extra_queries)
        # 内置条目仍然在，没有被整表替换
        self.assertIn("druggability", effective.not_applicable)

    def test_user_rule_can_cancel_inherited_item(self):
        user = self.write_user(
            {
                "rules": [
                    {
                        "scope": "modality",
                        "key": "antibody",
                        "source": "user",
                        "not_applicable": ["-druggability"],
                    }
                ]
            }
        )
        effective = resolve("antibody", rules=load_rules(user=user)[0])
        self.assertNotIn("druggability", effective.not_applicable)

    def test_more_specific_scope_wins_on_thresholds(self):
        user = self.write_user(
            {
                "rules": [
                    {
                        "scope": "gene",
                        "key": "KRAS",
                        "source": "user",
                        "thresholds": {"dependency": 55},
                    }
                ]
            }
        )
        effective = resolve("small_molecule", "PAAD", "KRAS", rules=load_rules(user=user)[0])
        self.assertEqual(effective.thresholds["dependency"], 55)
        self.assertEqual(effective.source_of("thresholds", "dependency"), "使用者自定义")

    def test_user_overrides_same_scope_builtin_threshold(self):
        rules = [
            MappingRule(scope="modality", key="general", source="builtin", thresholds={"dependency": 70}),
            MappingRule(scope="modality", key="general", source="user", thresholds={"dependency": 40}),
        ]
        effective = resolve(None, rules=rules)
        self.assertEqual(effective.thresholds["dependency"], 40)

    def test_bad_entry_is_skipped_without_losing_the_rest(self):
        user = self.write_user(
            {
                "rules": [
                    {"scope": "modality", "key": "antibody", "required": ["not_a_dimension"]},
                    {
                        "scope": "modality",
                        "key": "antibody",
                        "source": "user",
                        "extra_queries": ["仍然生效的条目"],
                    },
                ]
            }
        )
        rules, problems = load_rules(user=user)
        self.assertEqual(len(problems), 1)
        self.assertIn("not_a_dimension", problems[0])
        effective = resolve("antibody", rules=rules)
        self.assertIn("仍然生效的条目", effective.extra_queries)

    def test_unknown_modality_key_is_rejected(self):
        rules, problems = parse_rules(
            {"rules": [{"scope": "modality", "key": "反义寡核苷酸", "extra_queries": ["x"]}]},
            source="user",
            origin="user.json",
        )
        self.assertEqual(rules, [])
        self.assertIn("未知模态", problems[0])

    def test_missing_user_file_is_not_a_problem(self):
        rules, problems = load_rules(user=self.dir / "不存在.json")
        self.assertEqual(problems, [])
        self.assertTrue(rules)

    def test_broken_json_is_reported_not_raised(self):
        path = self.dir / "user.json"
        path.write_text("{ 这不是 JSON", encoding="utf-8")
        rules, problems = load_rules(user=path)
        self.assertTrue(problems)
        self.assertTrue(rules)  # 内置规则照常可用

    def test_template_is_writable_and_reloadable(self):
        payload = template_payload("cell_therapy")
        path = self.write_user(payload)
        rules, problems = load_rules(user=path)
        self.assertEqual(problems, [])
        effective = resolve("cell_therapy", rules=rules)
        self.assertIn("druggability", effective.not_applicable)


class TestDescribe(unittest.TestCase):
    def test_description_carries_source_labels(self):
        effective = resolve("protac", rules=load_rules(user=Path("__none__"))[0])
        text = "\n".join(describe(effective))
        self.assertIn("降解剂", text)
        self.assertIn("来源：内置默认", text)
        self.assertIn("成药性与结构", text)

    def test_engineering_setting_note_is_visible(self):
        effective = resolve("general", rules=load_rules(user=Path("__none__"))[0])
        text = "\n".join(describe(effective))
        self.assertIn(BASIS_ENGINEERING, text)


if __name__ == "__main__":
    unittest.main()
