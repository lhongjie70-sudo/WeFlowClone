"""导出器测试：产物必须严格符合 WeClone 的 dataset/csv 约定。"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from _support import make_settings

from weflow_clone.tools.fixtures import make_png
from weflow_clone.weflow.exporter import CsvExporter, collect_contact_names, collect_member_names
from weflow_clone.weflow.mapping import CSV_HEADER
from weflow_clone.weflow.schema import Contact, Message, Session

BASE_TS = int(datetime(2024, 8, 16, 9, 0, 0).timestamp())


def text_message(index: int, *, is_send: bool, content: str = "在吗") -> Message:
    return Message(
        local_id=index,
        server_id=str(1000 + index),
        local_type=1,
        create_time=BASE_TS + index * 60,
        is_send=is_send,
        sender="wxid_me" if is_send else "wxid_zhangsan",
        content=content,
        parsed_content=content,
    )


class ExporterTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wfc-export-test-")
        self.root = Path(self._tmp.name)
        self.media_root = self.root / "weflow-media"
        self.media_root.mkdir()
        self.session = Session(username="wxid_zhangsan", display_name="张三", type=1)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _settings(self, **kwargs):
        return make_settings(self.root, **kwargs)

    # ------------------------------------------------------------------ #
    def test_basic_export_shape(self):
        settings = self._settings()
        messages = [text_message(i, is_send=i % 2 == 0) for i in range(1, 6)]
        exporter = CsvExporter(settings)
        result = exporter.export_session(self.session, messages)

        self.assertEqual(result.kept, 5)
        self.assertEqual(len(result.csv_files), 1)
        csv_path = result.csv_files[0]
        self.assertEqual(csv_path.name, "张三_1_5.csv")
        self.assertEqual(csv_path.parent.name, "张三")

        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            rows = list(reader)
        self.assertEqual(header, CSV_HEADER)
        self.assertEqual(len(rows), 5)
        self.assertEqual([row[0] for row in rows], ["1", "2", "3", "4", "5"])
        self.assertEqual([row[3] for row in rows], ["0", "1", "0", "1", "0"])
        self.assertEqual([row[4] for row in rows], ["张三", "我", "张三", "我", "张三"])
        self.assertTrue(all(row[2] == "text" for row in rows))
        self.assertEqual(rows[0][7], datetime.fromtimestamp(BASE_TS + 60).strftime("%Y-%m-%d %H:%M:%S"))

    def test_users_json_written(self):
        settings = self._settings()
        exporter = CsvExporter(settings)
        result = exporter.export_session(
            self.session,
            [text_message(1, is_send=False)],
            contact_names={"wxid_zhangsan": "老张"},
        )
        payload = json.loads((result.csv_files[0].parent / "users.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["relation"], "好友")
        self.assertEqual(payload["张三"]["remark"], "老张")

    def test_type_filtering_and_skip_stats(self):
        settings = self._settings(include_types=["text"])
        messages = [
            text_message(1, is_send=False),
            Message(local_type=34, content="[语音]", create_time=BASE_TS + 120, is_send=False),
            Message(local_type=10000, content="系统提示", create_time=BASE_TS + 180, is_send=False),
        ]
        exporter = CsvExporter(settings)
        result = exporter.export_session(self.session, messages)
        self.assertEqual(result.kept, 1)
        self.assertEqual(result.skipped.get("voice"), 1)
        self.assertEqual(result.skipped.get("unsupported"), 1)

    def test_image_media_copied_and_src_relative(self):
        settings = self._settings()
        image_name = "photo.png"
        (self.media_root / image_name).write_bytes(make_png(8, 8, (1, 2, 3)))
        image = Message(
            local_type=3,
            content="[图片]",
            parsed_content="[图片]",
            create_time=BASE_TS + 60,
            is_send=True,
            media_file_name=image_name,
            media_local_path=str(self.media_root / image_name),
        )
        exporter = CsvExporter(settings)
        result = exporter.export_session(self.session, [text_message(0, is_send=False), image])

        self.assertEqual(result.media_files, 1)
        media_path = settings.media_dir / "images" / image_name
        self.assertTrue(media_path.is_file())

        with result.csv_files[0].open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        image_row = next(row for row in rows if row["type_name"] == "image")
        self.assertEqual(image_row["src"], f"images/{image_name}")

    def test_media_skipped_when_disabled(self):
        settings = self._settings(download_media=False)
        image = Message(
            local_type=3,
            content="[图片]",
            create_time=BASE_TS,
            is_send=True,
            media_file_name="photo.png",
            media_local_path=str(self.media_root / "photo.png"),
        )
        exporter = CsvExporter(settings)
        result = exporter.export_session(self.session, [image])
        self.assertEqual(result.media_files, 0)
        with result.csv_files[0].open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["src"], "")

    def test_chunking_by_max_messages(self):
        settings = self._settings(max_messages_per_file=10)
        messages = [text_message(i, is_send=i % 2 == 0) for i in range(1, 26)]
        exporter = CsvExporter(settings)
        result = exporter.export_session(self.session, messages)
        names = [path.name for path in result.csv_files]
        self.assertEqual(names, ["张三_1_10.csv", "张三_11_20.csv", "张三_21_25.csv"])
        total = 0
        for path in result.csv_files:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                total += len(list(csv.DictReader(handle)))
        self.assertEqual(total, 25)

    def test_reexport_overwrites_previous_csvs(self):
        settings = self._settings()
        exporter = CsvExporter(settings)
        exporter.export_session(self.session, [text_message(i, is_send=False) for i in range(1, 6)])
        exporter.export_session(self.session, [text_message(i, is_send=False) for i in range(1, 3)])
        files = list((settings.csv_dir / "张三").glob("*.csv"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "张三_1_2.csv")

    def test_group_talker_uses_member_names(self):
        settings = self._settings()
        group = Session(username="123@chatroom", display_name="爬山群", type=2)
        messages = [
            Message(
                local_type=1,
                content="早上七点集合",
                create_time=BASE_TS,
                is_send=False,
                sender="wxid_li",
                parsed_content="早上七点集合",
            ),
            Message(
                local_type=1,
                content="收到",
                create_time=BASE_TS + 30,
                is_send=True,
                sender="wxid_me",
                parsed_content="收到",
            ),
        ]
        exporter = CsvExporter(settings)
        result = exporter.export_session(
            group, messages, member_names={"wxid_li": "雷子"}
        )
        with result.csv_files[0].open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["talker"] for row in rows], ["雷子", "我"])

    def test_collect_helpers(self):
        contacts = [
            Contact(username="a", display_name="甲", remark="甲哥"),
            Contact(username="b", display_name="乙"),
        ]
        self.assertEqual(collect_contact_names(contacts), {"a": "甲哥", "b": "乙"})


if __name__ == "__main__":
    unittest.main()
