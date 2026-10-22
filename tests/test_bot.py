"""内置机器人测试：人设抽取、检索、OpenAI 兼容客户端、HTTP 服务。

用内置的假 OpenAI 服务来跑，完全离线。
"""

from __future__ import annotations

import csv
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _support import make_settings

from weflow_clone.bot.config import BotConfig, load_bot_config, save_bot_config
from weflow_clone.bot.llm import LLMClient, LLMError
from weflow_clone.bot.persona import build_persona, compose_prompt
from weflow_clone.bot.retriever import ExampleIndex, load_examples, tokenize
from weflow_clone.bot.server import BotServer

HEADER = "id,MsgSvrID,type_name,is_sender,talker,msg,src,CreateTime"

ROWS = [
    ["1", "1", "text", "0", "张三", "周六有空吗", "", "2024-08-16 09:00:00"],
    ["2", "2", "text", "1", "我", "在的", "", "2024-08-16 09:01:00"],
    ["3", "3", "text", "0", "张三", "一起去爬山不", "", "2024-08-16 09:02:00"],
    ["4", "4", "text", "1", "我", "可以啊，几点", "", "2024-08-16 09:03:00"],
    ["5", "5", "text", "0", "张三", "七点半北门", "", "2024-08-16 09:04:00"],
    ["6", "6", "text", "1", "我", "好，我带点水", "", "2024-08-16 09:05:00"],
]


def write_dataset(settings) -> None:
    folder = settings.csv_dir / "张三"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "张三_1_6.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write(HEADER + "\r\n")
        for row in ROWS:
            handle.write(",".join(row) + "\r\n")


# --------------------------------------------------------------------------- #
# 假 OpenAI 服务
# --------------------------------------------------------------------------- #
class FakeOpenAI(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D401 - 静默
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization") or ""
        if self.server.token and auth != f"Bearer {self.server.token}":
            body = json.dumps({"error": {"message": "invalid api key"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.server.requests.append(payload)
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for piece in ["在", "的", "呀"]:
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            return

        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "在的呀"}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeServer(ThreadingHTTPServer):
    daemon_threads = True
    token = "test-key"
    requests: list = []


class PersonaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wfc-bot-persona-")
        self.settings = make_settings(Path(self._tmp.name))
        write_dataset(self.settings)

    def tearDown(self):
        self._tmp.cleanup()

    def test_persona_learns_style(self):
        persona = build_persona(self.settings)
        self.assertEqual(persona.message_count, 3)
        self.assertIn("【你的说话习惯】", persona.prompt)
        self.assertIn("【规则】", persona.prompt)
        self.assertTrue(persona.exemplars)
        self.assertGreater(persona.avg_length, 0)

    def test_persona_handles_empty_dataset(self):
        empty = make_settings(Path(self._tmp.name) / "empty")
        persona = build_persona(empty)
        self.assertEqual(persona.message_count, 0)
        self.assertEqual(persona.prompt, "")

    def test_compose_prompt_includes_custom_name_and_extra(self):
        persona = build_persona(self.settings)
        prompt = compose_prompt(persona, bot_name="阿泽", extra_system="不要用哈哈哈")
        self.assertIn("阿泽", prompt)
        self.assertIn("不要用哈哈哈", prompt)


class RetrieverTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wfc-bot-retriever-")
        self.settings = make_settings(Path(self._tmp.name))
        write_dataset(self.settings)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tokenize_chinese_bigrams(self):
        self.assertEqual(tokenize("爬山"), ["爬山"])
        self.assertIn("爬山", tokenize("周末去爬山吗"))

    def test_load_examples_pairs(self):
        examples = load_examples(self.settings)
        self.assertEqual(len(examples), 3)
        self.assertEqual(examples[0].question, "周六有空吗")
        self.assertEqual(examples[0].answer, "在的")

    def test_search_returns_relevant(self):
        index = ExampleIndex.from_settings(self.settings)
        results = index.search("要不要去爬山", top_k=2)
        self.assertTrue(results)
        self.assertTrue(any("爬山" in item.question or "爬山" in item.answer for item in results))

    def test_search_empty_query(self):
        index = ExampleIndex.from_settings(self.settings)
        self.assertEqual(index.search("", top_k=3), [])


class LLMClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeServer(("127.0.0.1", 0), FakeOpenAI)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def config(self, **kwargs) -> BotConfig:
        config = BotConfig(api_base=self.base, api_key="test-key", model="fake-model")
        for key, value in kwargs.items():
            setattr(config, key, value)
        return config

    def test_non_stream(self):
        client = LLMClient(self.config())
        self.assertEqual(client.chat([{"role": "user", "content": "在吗"}]), "在的呀")

    def test_stream(self):
        client = LLMClient(self.config())
        chunks = list(client.chat_stream([{"role": "user", "content": "在吗"}]))
        self.assertEqual("".join(chunks), "在的呀")
        self.assertGreaterEqual(len(chunks), 2)

    def test_missing_key_is_explained(self):
        client = LLMClient(self.config(api_key=""))
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("API Key", str(ctx.exception))

    def test_bad_key_is_explained(self):
        client = LLMClient(self.config(api_key="wrong"))
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("401", str(ctx.exception))

    def test_endpoint_normalization(self):
        client = LLMClient(self.config(api_base=self.base + "/chat/completions"))
        self.assertEqual(client.chat([{"role": "user", "content": "hi"}]), "在的呀")


class BotServerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="wfc-bot-server-")
        self.settings = make_settings(Path(self._tmp.name))
        write_dataset(self.settings)

        self.fake = FakeServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.fake.server_address[1]}/v1"
        save_bot_config(
            self.settings.data_dir,
            {"api_base": base, "api_key": "test-key", "model": "fake-model", "retrieve_top_k": 1},
        )

        self.server = BotServer(("127.0.0.1", 0), self.settings)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.fake.shutdown()
        self.fake.server_close()
        self._tmp.cleanup()

    def _get(self, path: str) -> bytes:
        with urllib.request.urlopen(f"{self.url}{path}", timeout=10) as response:
            return response.read()

    def _post(self, path: str, payload: dict) -> bytes:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read()

    def test_index_page(self):
        html = self._get("/").decode("utf-8")
        self.assertIn("WeFlowClone", html)
        self.assertIn("内置机器人", html)

    def test_state(self):
        data = json.loads(self._get("/api/state"))
        self.assertTrue(data["config"]["configured"])
        self.assertEqual(data["config"]["model"], "fake-model")
        self.assertNotIn("test-key", json.dumps(data))  # Key 必须打码
        self.assertTrue(data["health"]["persona_ready"])

    def test_chat_stream(self):
        request = urllib.request.Request(
            f"{self.url}/api/chat",
            data=json.dumps({"message": "周六去爬山吗", "history": []}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")

        deltas, done = [], False
        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[5:].strip())
            if payload.get("delta"):
                deltas.append(payload["delta"])
            if payload.get("done"):
                done = True
        self.assertTrue(done)
        self.assertEqual("".join(deltas), "在的呀")

    def test_openai_compatible(self):
        data = json.loads(
            self._post("/v1/chat/completions", {"messages": [{"role": "user", "content": "在吗"}]})
        )
        self.assertEqual(data["choices"][0]["message"]["content"], "在的呀")
        self.assertEqual(data["object"], "chat.completion")

    def test_openai_models(self):
        data = json.loads(self._get("/v1/models"))
        self.assertEqual(data["data"][0]["id"], "fake-model")

    def test_config_roundtrip_keeps_key(self):
        # 前端回显打码值，不能把真 Key 覆盖掉
        self._post("/api/config", {"api_key": "sk-1234…cdef", "model": "fake-model"})
        config = load_bot_config(self.settings.data_dir)
        self.assertEqual(config.api_key, "test-key")


if __name__ == "__main__":
    unittest.main()
