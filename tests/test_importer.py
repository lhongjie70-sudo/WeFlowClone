"""导入器测试：WeFlow 手工导出（CSV / ChatLab / 原始 JSON）都要能落地。"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from _support import make_settings

from weflow_clone.errors import WeFlowCloneError
from weflow_clone.tools.fixtures import make_png
from weflow_clone.weflow.importer import Importer

HEADER = "id,MsgSvrID,type_name,is_sender,talker,msg,src,CreateTime"


class ImporterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wfc-import-test-")
        self.root = Path(self._tmp.name)
        self.export_dir = self.root / "weflow-export" / "张三"
        self.export_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_csv(self, rows: list[list[str]]) -> Path:
        path = self.export_dir / "张三_1_3.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            handle.write(HEADER + "\r\n")
            for row in rows:
                handle.write(",".join(row) + "\r\n")
        return path

    # ------------------------------------------------------------------ #
    def test_import_weflow_csv_folder(self):
        (self.export_dir / "images").mkdir()
        (self.export_dir / "images" / "photo.png").write_bytes(make_png(8, 8, (10, 20, 30)))
        self._write_csv(
            [
                ["1", "111", "text", "0", "张三", "在吗", "", "2024-08-16 09:00:00"],
                ["2", "112", "text", "1", "我", "在的", "", "2024-08-16 09:01:00"],
                ["3", "113", "image", "0", "张三", "[图片]", "images/photo.png", "2024-08-16 09:02:00"],
            ]
        )

        settings = make_settings(self.root)
        importer = Importer(settings)
        results = importer.import_path(self.root / "weflow-export")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].messages, 3)
        self.assertEqual(results[0].media, 1)

        target = settings.csv_dir / "张三" / "张三_1_3.csv"
        self.assertTrue(target.is_file())
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["msg"] for row in rows], ["在吗", "在的", "[图片]"])
        self.assertTrue((settings.media_dir / "images" / "photo.png").is_file())

    def test_import_skips_csv_without_required_columns(self):
        bad_dir = self.root / "weflow-export" / "坏数据"
        bad_dir.mkdir(parents=True)
        (bad_dir / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        settings = make_settings(self.root)
        with self.assertRaises(WeFlowCloneError):
            Importer(settings).import_path(self.root / "weflow-export")

    def test_import_chatlab_json(self):
        payload = {
            "chatlab": {"version": "0.0.2", "exportedAt": 0, "generator": "WeFlow"},
            "meta": {"name": "张三", "type": "private", "groupId": "wxid_zhangsan", "ownerId": "wxid_me"},
            "members": [],
            "messages": [
                {
                    "sender": "wxid_zhangsan",
                    "accountName": "张三",
                    "timestamp": 1723770000,
                    "type": 0,
                    "content": "在吗",
                    "platformMessageId": "9001",
                },
                {
                    "sender": "wxid_me",
                    "accountName": "我",
                    "timestamp": 1723770060,
                    "type": 0,
                    "content": "在的",
                    "platformMessageId": "9002",
                },
            ],
        }
        path = self.root / "chatlab.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        settings = make_settings(self.root)
        results = Importer(settings).import_path(path)
        self.assertEqual(results[0].kind, "ChatLab-JSON")
        self.assertEqual(results[0].messages, 2)

        target = next(settings.csv_dir.rglob("*.csv"))
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["type_name"] for row in rows], ["text", "text"])
        self.assertEqual(rows[0]["MsgSvrID"], "9001")
        self.assertEqual([row["is_sender"] for row in rows], ["0", "1"])

    def test_import_raw_weflow_json(self):
        payload = {
            "talker": "张三",
            "messages": [
                {
                    "localId": 1,
                    "serverId": "7001",
                    "localType": 1,
                    "createTime": 1723770000,
                    "isSend": 0,
                    "senderUsername": "wxid_zhangsan",
                    "content": "周末爬山？",
                    "parsedContent": "周末爬山？",
                },
                {
                    "localId": 2,
                    "serverId": "7002",
                    "localType": 1,
                    "createTime": 1723770060,
                    "isSend": 1,
                    "senderUsername": "wxid_me",
                    "content": "去",
                    "parsedContent": "去",
                },
            ],
        }
        path = self.root / "raw.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        settings = make_settings(self.root)
        results = Importer(settings).import_path(path)
        self.assertEqual(results[0].kind, "WeFlow-JSON")
        self.assertEqual(results[0].messages, 2)

    def test_missing_path_raises(self):
        settings = make_settings(self.root)
        with self.assertRaises(WeFlowCloneError):
            Importer(settings).import_path(self.root / "nope")


if __name__ == "__main__":
    unittest.main()
