"""字段映射单元测试：这是「WeFlow 数据能不能进 WeClone」的地基。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from _support import ROOT  # noqa: F401 - 顺带把项目根加进 sys.path

from weflow_clone.weflow.mapping import (
    CSV_HEADER,
    build_row,
    csv_line,
    escape_cell,
    extract_text,
    format_create_time,
    is_media_type,
    is_supported,
    sanitize_folder_name,
    type_name_for,
    unique_folder_name,
)
from weflow_clone.weflow.schema import Message


def message(local_type: int = 1, content: str = "", **kwargs) -> Message:
    return Message(local_type=local_type, content=content, **kwargs)


class TypeMappingTest(unittest.TestCase):
    def test_basic_types(self):
        self.assertEqual(type_name_for(1, "你好"), "text")
        self.assertEqual(type_name_for(3, "[图片]"), "image")
        self.assertEqual(type_name_for(34, "[语音]"), "voice")
        self.assertEqual(type_name_for(43, "[视频]"), "video")
        self.assertEqual(type_name_for(47, "[动画表情]"), "sticker")
        self.assertEqual(type_name_for(48, ""), "location")

    def test_appmsg_file(self):
        xml = "<msg><appmsg><title>报告.pdf</title><type>6</type></appmsg></msg>"
        self.assertEqual(type_name_for(49, xml), "file")

    def test_appmsg_link_is_text(self):
        xml = "<msg><appmsg><title>链接</title><type>5</type></appmsg></msg>"
        self.assertEqual(type_name_for(49, xml), "text")

    def test_system_message_is_unsupported(self):
        self.assertEqual(type_name_for(10000, "你撤回了一条消息"), "unsupported")

    def test_supported_filter(self):
        self.assertTrue(is_supported("text", ["text", "image"]))
        self.assertFalse(is_supported("voice", ["text", "image"]))
        self.assertFalse(is_supported("unsupported", ["text", "unsupported"]))

    def test_media_flag(self):
        self.assertTrue(is_media_type("image"))
        self.assertFalse(is_media_type("text"))


class TextExtractionTest(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(extract_text(message(1, "在吗？")), "在吗？")

    def test_text_control_chars_removed(self):
        self.assertEqual(extract_text(message(1, "你好\x00\x07世界\r\n第二行")), "你好世界\n第二行")

    def test_link_title_and_description(self):
        xml = (
            "<msg><appmsg><title>北坡路线</title><des>含补给点</des>"
            "<url>https://example.com/a</url><type>5</type></appmsg></msg>"
        )
        text = extract_text(message(49, xml))
        self.assertIn("北坡路线", text)
        self.assertIn("含补给点", text)

    def test_cdata_title(self):
        xml = "<msg><appmsg><title><![CDATA[周末爬山]]></title><type>5</type></appmsg></msg>"
        self.assertEqual(extract_text(message(49, xml)), "周末爬山")

    def test_file_uses_filename(self):
        xml = "<msg><appmsg><title>忽略</title><filename>行程.xlsx</filename><type>6</type></appmsg></msg>"
        self.assertEqual(extract_text(message(49, xml)), "行程.xlsx")

    def test_media_placeholders(self):
        self.assertEqual(extract_text(message(3, "")), "[图片]")
        self.assertEqual(extract_text(message(34, "")), "[语音]")

    def test_parsed_content_wins_for_media(self):
        msg = Message(local_type=3, content="raw", parsed_content="[图片]")
        self.assertEqual(extract_text(msg), "[图片]")


class TimeAndCsvTest(unittest.TestCase):
    def test_local_time(self):
        epoch = int(datetime(2024, 8, 16, 10, 30, 0).timestamp())
        self.assertEqual(format_create_time(epoch, "local"), "2024-08-16 10:30:00")

    def test_iso_time_is_utc(self):
        epoch = int(datetime(2024, 8, 16, 10, 30, 0).timestamp())
        expected = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(format_create_time(epoch, "iso"), expected)

    def test_zero_timestamp(self):
        self.assertEqual(format_create_time(0, "local"), "")

    def test_escape(self):
        self.assertEqual(escape_cell("普通"), "普通")
        self.assertEqual(escape_cell('带"引号"'), '"带""引号"""')
        self.assertEqual(escape_cell("a,b"), '"a,b"')
        self.assertEqual(escape_cell("a\nb"), '"a\nb"')
        self.assertEqual(escape_cell(None), "")

    def test_csv_line(self):
        self.assertEqual(csv_line([1, "a", "b,c"]), '1,a,"b,c"')

    def test_header_shape(self):
        self.assertEqual(len(CSV_HEADER), 8)
        self.assertEqual(CSV_HEADER[0], "id")
        self.assertEqual(CSV_HEADER[-1], "CreateTime")

    def test_build_row(self):
        msg = message(
            1,
            "周六爬山吗",
            server_id="123",
            is_send=False,
            create_time=int(datetime(2024, 8, 16, 9, 0, 0).timestamp()),
        )
        row = build_row(msg, 7, "张三", "", "local")
        self.assertEqual(row[0], 7)
        self.assertEqual(row[1], "123")
        self.assertEqual(row[2], "text")
        self.assertEqual(row[3], 0)
        self.assertEqual(row[4], "张三")
        self.assertEqual(row[5], "周六爬山吗")
        self.assertEqual(row[7], "2024-08-16 09:00:00")


class NamingTest(unittest.TestCase):
    def test_illegal_chars_removed(self):
        self.assertEqual(sanitize_folder_name('张三:爬山/群*?'), "张三_爬山_群__")

    def test_empty_name_fallback(self):
        self.assertEqual(sanitize_folder_name("   "), "chat")

    def test_reserved_name_prefixed(self):
        self.assertEqual(sanitize_folder_name("CON"), "_CON")

    def test_long_name_truncated(self):
        self.assertLessEqual(len(sanitize_folder_name("长" * 200)), 80)

    def test_duplicate_names_get_suffix(self):
        taken: dict[str, str] = {}
        first = unique_folder_name("张三", "wxid_a", taken)
        second = unique_folder_name("张三", "wxid_b", taken)
        self.assertEqual(first, "张三")
        self.assertNotEqual(second, first)
        self.assertIn("wxid_b", second)


if __name__ == "__main__":
    unittest.main()
