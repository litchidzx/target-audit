"""打包入口的分流规则。

这段逻辑容易出错且后果隐蔽：判错了不会报错，只会安静地启动错的那个模式
（双击出命令行、命令行出图形界面），而且只有打包后才看得出来。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_entry import should_use_cli  # noqa: E402


class TestDispatch(unittest.TestCase):
    def test_double_click_opens_gui(self):
        self.assertFalse(should_use_cli([]))

    def test_subcommands_go_to_cli(self):
        for argv in (
            ["audit", "KRAS", "--cancer", "PAAD"],
            ["self-test"],
            ["doctor"],
            ["mappings", "--modality", "protac"],
            ["demo", "--cancer", "LUAD"],
            ["未来新增的子命令"],  # 不在任何名单里，也应该走命令行
        ):
            with self.subTest(argv=argv):
                self.assertTrue(should_use_cli(argv))

    def test_option_only_arguments_keep_gui(self):
        self.assertFalse(should_use_cli(["--port", "8081"]))


if __name__ == "__main__":
    unittest.main()
