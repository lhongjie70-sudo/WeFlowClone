"""同步编排测试：选会话、鉴权、导出、干跑。"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from _support import make_settings

from weflow_clone.config import WeFlowSettings
from weflow_clone.errors import WeFlowCloneError
from weflow_clone.sync import normalize_date, pull, select_sessions
from weflow_clone.tools.fixtures import GROUP_NAME, PRIVATE_NAME
from weflow_clone.tools.mock_weflow import start_server
from weflow_clone.weflow.client import ClientOptions, WeFlowClient
from weflow_clone.weflow.schema import Session


class SelectSessionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wfc-select-test-")
        self.root = Path(self._tmp.name)
        self.settings = make_settings(self.root)
        self.sessions = [
            Session(username="wxid_a", display_name="张三", type=1),
            Session(username="123@chatroom", display_name="爬山群", type=2),
            Session(username="wxid_b", display_name="李四", type=1),
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_groups_excluded_by_default(self):
        chosen = select_sessions(self.sessions, self.settings)
        self.assertEqual([s.display_name for s in chosen], ["张三", "李四"])

    def test_groups_included_when_asked(self):
        chosen = select_sessions(self.sessions, self.settings, include_groups=True)
        self.assertEqual(len(chosen), 3)

    def test_selector_by_name_and_substring(self):
        self.assertEqual(
            [s.display_name for s in select_sessions(self.sessions, self.settings, ["张三"])],
            ["张三"],
        )
        self.assertEqual(
            [s.display_name for s in select_sessions(self.sessions, self.settings, ["张"])],
            ["张三"],
        )

    def test_config_include_and_exclude(self):
        self.settings.weflow = WeFlowSettings(include=[], exclude=["李四"])
        chosen = select_sessions(self.sessions, self.settings)
        self.assertEqual([s.display_name for s in chosen], ["张三"])

    def test_max_sessions(self):
        self.settings.weflow = WeFlowSettings(max_sessions=1)
        self.assertEqual(len(select_sessions(self.sessions, self.settings)), 1)

    def test_date_normalization(self):
        self.assertEqual(normalize_date("2024-08-16"), "20240816")
        self.assertEqual(normalize_date("20240816"), "20240816")
        self.assertEqual(normalize_date("2024/08/16"), "20240816")
        self.assertIsNone(normalize_date(None))
        with self.assertRaises(WeFlowCloneError):
            normalize_date("上周")


class PullTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="wfc-pull-test-")
        cls.media_root = Path(cls._tmp.name) / "weflow-media"
        cls.server, cls.thread = start_server(cls.media_root, token="tok")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp(dir=self._tmp.name))
        self.settings = make_settings(self.workdir, token="tok", base_url=self.server.base_url)
        self.client = WeFlowClient(
            ClientOptions(
                base_url=self.server.base_url, access_token="tok", retries=0, timeout=10, page_size=7
            )
        )

    def test_pull_private_only(self):
        summary = pull(self.settings, self.client)
        self.assertEqual(len(summary.results), 1)
        result = summary.results[0]
        self.assertEqual(result.session.display_name, PRIVATE_NAME)
        # 30 条文本 + 1 图片 + 1 链接(file) = 32；语音/表情/系统消息被过滤
        self.assertEqual(result.kept, 32)

        csv_file = next(self.settings.csv_dir.rglob("*.csv"))
        with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 32)
        self.assertTrue(any(row["type_name"] == "image" for row in rows))
        self.assertTrue((self.settings.media_dir / "images").is_dir())
        self.assertTrue(list((self.settings.media_dir / "images").glob("*.png")))

    def test_pull_with_groups(self):
        summary = pull(self.settings, self.client, include_groups=True)
        names = {result.session.display_name for result in summary.results}
        self.assertEqual(names, {PRIVATE_NAME, GROUP_NAME})

    def test_dry_run_writes_nothing(self):
        summary = pull(self.settings, self.client, dry_run=True)
        self.assertEqual(summary.results, [])
        self.assertFalse(self.settings.csv_dir.exists() and any(self.settings.csv_dir.iterdir()))

    def test_unknown_selector_raises(self):
        with self.assertRaises(WeFlowCloneError):
            pull(self.settings, self.client, selectors=["不存在的人"])

    def test_limit_caps_messages(self):
        summary = pull(self.settings, self.client, limit=5)
        self.assertEqual(summary.results[0].kept, 5)


if __name__ == "__main__":
    unittest.main()
