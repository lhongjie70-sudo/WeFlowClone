"""CLI 层测试：参数解析与「缺配置」的友好失败。"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from _support import ROOT  # noqa: F401

from weflow_clone.cli import build_parser, main


class ParserTest(unittest.TestCase):
    def test_config_before_subcommand(self):
        args = build_parser().parse_args(["--config", "a.jsonc", "pull"])
        self.assertEqual(args.config, "a.jsonc")
        self.assertEqual(args.command, "pull")

    def test_config_after_subcommand(self):
        args = build_parser().parse_args(["pull", "--config", "b.jsonc"])
        self.assertEqual(args.config, "b.jsonc")

    def test_config_defaults_to_none(self):
        args = build_parser().parse_args(["status"])
        self.assertIsNone(args.config)

    def test_pull_flags(self):
        args = build_parser().parse_args(
            ["pull", "--session", "张三", "--session", "李四", "--groups", "--limit", "100"]
        )
        self.assertEqual(args.session, ["张三", "李四"])
        self.assertTrue(args.groups)
        self.assertEqual(args.limit, 100)


class MainTest(unittest.TestCase):
    def test_no_command_prints_help(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main([])
        self.assertEqual(code, 0)
        self.assertIn("usage", buffer.getvalue().lower())

    def test_version(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_missing_config_returns_error_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.jsonc"
            buffer = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(stderr):
                code = main(["status", "--config", str(missing)])
        self.assertEqual(code, 1)
        self.assertIn("找不到配置文件", stderr.getvalue())

    def test_unknown_command_exits(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["not-a-command"])


if __name__ == "__main__":
    unittest.main()
