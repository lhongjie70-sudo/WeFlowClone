"""WeFlow 客户端测试：对着内置的模拟服务跑，覆盖鉴权 / 分页 / 媒体 / ChatLab。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _support import ROOT  # noqa: F401

from weflow_clone.errors import WeFlowAuthError, WeFlowUnavailable
from weflow_clone.tools.fixtures import GROUP_TALKER, PRIVATE_TALKER
from weflow_clone.tools.mock_weflow import start_server
from weflow_clone.weflow.client import ClientOptions, WeFlowClient


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="wfc-client-test-")
        cls.media_root = Path(cls._tmp.name) / "media"
        cls.server, cls.thread = start_server(cls.media_root, token="secret-token")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls._tmp.cleanup()

    def client(self, token: str = "secret-token", **kwargs) -> WeFlowClient:
        options = ClientOptions(
            base_url=self.server.base_url,
            access_token=token,
            timeout=10,
            retries=0,
            **kwargs,
        )
        return WeFlowClient(options)

    # ------------------------------------------------------------------ #
    def test_health_needs_no_token(self):
        self.assertEqual(self.client(token="").health()["status"], "ok")

    def test_wrong_token_raises_auth_error(self):
        with self.assertRaises(WeFlowAuthError):
            self.client(token="wrong").sessions()

    def test_sessions(self):
        sessions = self.client().sessions()
        self.assertEqual(len(sessions), 2)
        names = {session.display_name for session in sessions}
        self.assertIn("张三", names)
        self.assertTrue(any(session.is_group for session in sessions))

    def test_contacts(self):
        contacts = self.client().contacts()
        self.assertTrue(any(contact.remark == "老张" for contact in contacts))

    def test_group_members(self):
        members = self.client().group_members(GROUP_TALKER)
        self.assertEqual(len(members), 3)
        self.assertTrue(any(member.group_nickname == "雷子" for member in members))

    def test_messages_and_pagination(self):
        client = self.client()
        page = client.messages(PRIVATE_TALKER, limit=5)
        self.assertEqual(len(page.messages), 5)
        self.assertTrue(page.has_more)

        all_messages = list(client.iter_messages(PRIVATE_TALKER, page_size=7))
        self.assertEqual(len(all_messages), 35)
        self.assertEqual(len({m.server_id for m in all_messages}), 35)  # 无重复
        self.assertEqual([m.create_time for m in all_messages], sorted(m.create_time for m in all_messages))

    def test_time_range_filter(self):
        client = self.client()
        everything = list(client.iter_messages(PRIVATE_TALKER, page_size=50))
        middle = everything[len(everything) // 2].create_time
        from datetime import datetime

        start = datetime.fromtimestamp(middle).strftime("%Y%m%d")
        filtered = list(client.iter_messages(PRIVATE_TALKER, page_size=50, start=start))
        self.assertLessEqual(len(filtered), len(everything))
        self.assertTrue(all(message.create_time >= middle - 86400 for message in filtered))

    def test_media_copy_from_local_path(self):
        client = self.client()
        messages = list(client.iter_messages(PRIVATE_TALKER, page_size=50))
        image = next(message for message in messages if message.local_type == 3)
        target_dir = Path(self._tmp.name) / "downloaded"
        path = client.copy_or_download_media(image, target_dir)
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 0)

    def test_chatlab_format(self):
        client = self.client()
        page = client.messages(PRIVATE_TALKER, limit=10, chatlab=True)
        self.assertEqual(len(page.messages), 10)
        self.assertTrue(all(message.local_type for message in page.messages))
        texts = [message for message in page.messages if message.local_type == 1]
        self.assertTrue(texts)

    def test_unreachable_server_gives_actionable_error(self):
        client = WeFlowClient(ClientOptions(base_url="http://127.0.0.1:1", retries=0, timeout=2))
        with self.assertRaises(WeFlowUnavailable) as ctx:
            client.health()
        self.assertIn("API", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
