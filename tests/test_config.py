"""配置测试：JSONC 注释 / 尾逗号 / 默认值 / 错误提示。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _support import ROOT  # noqa: F401

from weflow_clone.config import load_settings
from weflow_clone.errors import ConfigError


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wfc-config-test-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, text: str) -> Path:
        path = self.root / "weflow.jsonc"
        path.write_text(text, encoding="utf-8")
        return path

    def test_comments_and_trailing_commas(self) -> None:
        path = self._write(
            """{
              // 行注释
              "weflow": {
                "base_url": "http://127.0.0.1:9999", /* 块注释 */
                "access_token": "abc",
              },
              "export": {
                "include_types": ["text", "image"], // 只留两种
              },
            }"""
        )
        settings = load_settings(path, root=self.root)
        self.assertEqual(settings.weflow.base_url, "http://127.0.0.1:9999")
        self.assertEqual(settings.weflow.access_token, "abc")
        self.assertEqual(settings.export.include_types, ["text", "image"])

    def test_defaults(self) -> None:
        path = self._write("{}")
        settings = load_settings(path, root=self.root)
        self.assertEqual(settings.weflow.base_url, "http://127.0.0.1:5031")
        self.assertEqual(settings.export.data_dir, "data")
        self.assertEqual(settings.data_dir, self.root / "data")
        self.assertEqual(settings.csv_dir, self.root / "data" / "dataset" / "csv")
        self.assertEqual(settings.export.self_name, "我")
        self.assertFalse(settings.weflow.include_groups)

    def test_unknown_type_rejected(self) -> None:
        path = self._write('{"export": {"include_types": ["text", "hologram"]}}')
        with self.assertRaises(ConfigError):
            load_settings(path, root=self.root)

    def test_bool_strings(self) -> None:
        path = self._write('{"weflow": {"include_groups": "true"}, "export": {"media": {"download": "0"}}}')
        settings = load_settings(path, root=self.root)
        self.assertTrue(settings.weflow.include_groups)
        self.assertFalse(settings.export.media.download)

    def test_missing_file(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(self.root / "missing.jsonc", root=self.root)

    def test_bad_json(self) -> None:
        path = self._write("{ this is not json }")
        with self.assertRaises(ConfigError):
            load_settings(path, root=self.root)

    def test_weclone_settings_path_is_inside_weclone_dir(self) -> None:
        path = self._write('{"weclone": {"project_dir": "vendor/WeClone"}}')
        settings = load_settings(path, root=self.root)
        self.assertEqual(
            settings.weclone_settings_path,
            self.root / "vendor" / "WeClone" / "settings.jsonc",
        )


if __name__ == "__main__":
    unittest.main()
