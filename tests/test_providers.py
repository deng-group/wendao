from __future__ import annotations

import io
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from wendao.rag import providers
from wendao.rag.answer import AnswerGenerator
from wendao.rag.providers import (
    GeminiProvider,
    OpenAICompatibleProvider,
    default_provider_name,
    temperature_setting,
)
from wendao.rag.prompts import PromptBuilder


PROMPT_PACKAGE = {
    "messages": [
        {"role": "system", "content": "Answer from the course evidence."},
        {"role": "user", "content": "What is a crystal structure?"},
    ],
    "evidence": [{"chunk_id": "course:test:chunk-1"}],
}


class FakeOpenAIServer(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /chat/completions endpoint."""

    requests: list[dict] = []

    def do_POST(self):  # noqa: N802 - http.server naming
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAIServer.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for text in ["Crystal ", "structures ", "repeat."]:
                event = {"choices": [{"delta": {"content": text}}]}
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": "Crystal structures repeat."}}]})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload.encode())

    def log_message(self, *args):
        pass


class OpenAICompatibleProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeOpenAIServer)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeOpenAIServer.requests.clear()

    def test_generate_sends_chat_completion(self):
        env = {"OPENAI_BASE_URL": self.base_url, "OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "local-model"}
        with mock.patch.dict(os.environ, env):
            result = OpenAICompatibleProvider().generate(PROMPT_PACKAGE)

        self.assertEqual(result["answer"], "Crystal structures repeat.")
        self.assertEqual(result["model"], "local-model")
        sent = FakeOpenAIServer.requests[0]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["auth"], "Bearer sk-test")
        self.assertEqual([m["role"] for m in sent["body"]["messages"]], ["system", "user"])

    def test_stream_yields_deltas_without_api_key_for_custom_endpoint(self):
        env = {"OPENAI_BASE_URL": self.base_url, "OPENAI_MODEL": "local-model"}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("OPENAI_API_KEY", None)
            deltas = list(OpenAICompatibleProvider().stream(PROMPT_PACKAGE))

        self.assertEqual("".join(deltas), "Crystal structures repeat.")
        self.assertIsNone(FakeOpenAIServer.requests[0]["auth"])

    def test_official_endpoint_requires_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                OpenAICompatibleProvider().generate(PROMPT_PACKAGE)


    def test_temperature_is_configurable(self):
        env = {"OPENAI_BASE_URL": self.base_url, "OPENAI_MODEL": "local-model"}
        for setting, expected in [(None, 0.2), ("0.7", 0.7), ("none", "missing")]:
            with self.subTest(setting=setting), mock.patch.dict(os.environ, env):
                os.environ.pop("LLM_TEMPERATURE", None)
                if setting is not None:
                    os.environ["LLM_TEMPERATURE"] = setting
                FakeOpenAIServer.requests.clear()
                OpenAICompatibleProvider().generate(PROMPT_PACKAGE)
                sent = FakeOpenAIServer.requests[0]["body"]
                self.assertEqual(sent.get("temperature", "missing"), expected)


class TemperatureSettingTest(unittest.TestCase):
    def test_rejects_invalid_value(self):
        with mock.patch.dict(os.environ, {"LLM_TEMPERATURE": "warm"}):
            with self.assertRaisesRegex(RuntimeError, "LLM_TEMPERATURE"):
                temperature_setting()


class GeminiProviderTest(unittest.TestCase):
    def test_stream_parses_sse_candidates(self):
        events = [
            {"candidates": [{"content": {"parts": [{"text": "Crystal "}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "structures repeat."}]}}]},
        ]
        body = b"".join(f"data: {json.dumps(event)}\r\n\r\n".encode() for event in events)
        captured = {}

        def fake_open(req, label, timeout, follow_redirects=True):
            captured["url"] = req.full_url
            return io.BytesIO(body)

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test", "GEMINI_MODEL": "gemini-test"}):
            with mock.patch.object(providers, "_open", fake_open):
                deltas = list(GeminiProvider().stream(PROMPT_PACKAGE))

        self.assertEqual("".join(deltas), "Crystal structures repeat.")
        self.assertTrue(captured["url"].endswith("/models/gemini-test:streamGenerateContent?alt=sse"))


class DefaultProviderTest(unittest.TestCase):
    def test_explicit_provider_wins(self):
        env = {"LLM_PROVIDER": "openai", "ANTHROPIC_AUTH_TOKEN": "token"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(default_provider_name(), "openai")

    def test_detects_openai_compatible_base_url(self):
        with mock.patch.dict(os.environ, {"OPENAI_BASE_URL": "http://localhost:11434/v1"}, clear=True):
            self.assertEqual(default_provider_name(), "openai")

    def test_falls_back_to_dry_run(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(default_provider_name(), "dry_run")

    def test_rejects_unknown_provider(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "unknown"}, clear=True):
            with self.assertRaises(ValueError):
                default_provider_name()


class NonStreamingProvider:
    name = "non_streaming"
    model = "fake-model"

    def generate(self, prompt_package: dict) -> dict:
        return {"answer": "A complete answer."}


class FakePipeline:
    def ask(self, query: str, short_memory: list[dict] | None = None) -> dict:
        return {
            "query": query,
            "status": "answerable",
            "next_action": "build_prompt",
            "confidence": 0.91,
            "temporal_context": None,
            "needs_temporal_context": False,
            "evidence": [
                {
                    "chunk_id": "course:test:chunk-1",
                    "file_path": "structures/index.md",
                    "title": "Structures",
                    "score": 0.91,
                    "time_sensitive": False,
                    "temporal_context": None,
                    "content": "Crystal structures describe ordered arrangements of atoms.",
                }
            ],
        }


class StreamingFallbackTest(unittest.TestCase):
    def test_provider_without_stream_still_answers(self):
        generator = AnswerGenerator(pipeline=FakePipeline(), prompt_builder=PromptBuilder(), provider=NonStreamingProvider())
        events = list(generator.stream_answer("What is a crystal structure?"))

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["answer"], "A complete answer.")


if __name__ == "__main__":
    unittest.main()
