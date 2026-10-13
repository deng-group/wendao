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

        def fake_open(req, label, timeout, follow_redirects=True, **kwargs):
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


class RefusingServer(BaseHTTPRequestHandler):
    """Answers like a relay that doesn't offer the requested model."""

    def do_POST(self):  # noqa: N802 - http.server naming
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"error": {"message": "No available accounts: no available accounts", "type": "api_error"},
                           "type": "error"}).encode()
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ErrorsNameTheModelTest(unittest.TestCase):
    """A wrong model name is the usual reason a relay refuses; the error should say which model was asked for."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), RefusingServer)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def package(self):
        return {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], "evidence": []}

    def test_anthropic_and_openai_errors_name_the_model(self):
        env = {"ANTHROPIC_BASE_URL": self.base, "ANTHROPIC_AUTH_TOKEN": "sk-test", "OPENAI_BASE_URL": self.base + "/v1",
               "OPENAI_API_KEY": "sk-test"}
        with mock.patch.dict(os.environ, env):
            anthropic = providers.provider_from_name("anthropic", model="claude-sonnet-4-6")
            for call in (lambda: anthropic.generate(self.package()), lambda: list(anthropic.stream(self.package()))):
                with self.assertRaisesRegex(RuntimeError, "for model claude-sonnet-4-6: .*No available accounts"):
                    call()
            openai = providers.provider_from_name("openai", model="gpt-4.1-mini")
            with self.assertRaisesRegex(RuntimeError, "API error 503 for model gpt-4.1-mini: .*No available accounts"):
                openai.generate(self.package())

    def test_unreachable_server_names_the_model(self):
        with mock.patch.dict(os.environ, {"OPENAI_BASE_URL": "http://127.0.0.1:1/v1", "OPENAI_API_KEY": "sk-test"}):
            openai = providers.provider_from_name("openai", model="gpt-4.1-mini")
            with self.assertRaisesRegex(RuntimeError, "API unreachable for model gpt-4.1-mini"):
                openai.generate(self.package())


class ThinkingServer(BaseHTTPRequestHandler):
    """Answers like DeepSeek through an Anthropic-style relay: thinking first, then (maybe) the answer."""

    answer = ""  # empty: the thinking used up the token limit
    seen_max_tokens = []

    def do_POST(self):  # noqa: N802 - http.server naming
        request_body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).seen_max_tokens.append(request_body.get("max_tokens"))
        stop = "end_turn" if self.answer else "max_tokens"
        self.send_response(200)
        if not request_body.get("stream"):
            content = [{"type": "thinking", "thinking": "SECRET THOUGHTS", "signature": "x"}]
            if self.answer:
                content.append({"type": "text", "text": self.answer})
            body = json.dumps({"model": "deepseek-v4-flash", "content": content, "stop_reason": stop}).encode()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [{"type": "message_start", "message": {}},
                  {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "SECRET THOUGHTS"}}]
        if self.answer:
            events.append({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": self.answer}})
        events += [{"type": "message_delta", "delta": {"stop_reason": stop}}, {"type": "message_stop"}]
        for event in events:
            self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())

    def log_message(self, *args):
        pass


class ThinkingModelTest(unittest.TestCase):
    """Models that think before answering: show only the answer, never the raw reply or the thinking."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), ThinkingServer)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.env = {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{cls.server.server_address[1]}", "ANTHROPIC_AUTH_TOKEN": "sk-test"}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        ThinkingServer.answer = ""
        ThinkingServer.seen_max_tokens = []

    def provider(self):
        return providers.provider_from_name("anthropic", model="deepseek-v4-flash")

    def package(self):
        return {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], "evidence": []}

    def test_only_the_answer_is_shown(self):
        ThinkingServer.answer = "A convex hull is the lowest-energy envelope."
        with mock.patch.dict(os.environ, self.env):
            self.assertEqual(self.provider().generate(self.package())["answer"], ThinkingServer.answer)
            self.assertEqual("".join(self.provider().stream(self.package())), ThinkingServer.answer)
        self.assertEqual(ThinkingServer.seen_max_tokens, [8192, 8192])  # room for thinking and the answer

    def test_thinking_that_uses_up_the_limit_gives_a_clear_error(self):
        with mock.patch.dict(os.environ, {**self.env, "LLM_MAX_TOKENS": "3000"}):
            for call in (lambda: self.provider().generate(self.package()), lambda: list(self.provider().stream(self.package()))):
                with self.assertRaises(RuntimeError) as caught:
                    call()
                message = str(caught.exception)
                self.assertIn("thinking used up the 3000-token limit", message)
                self.assertIn("max_tokens", message)
                self.assertNotIn("SECRET THOUGHTS", message)  # never the raw reply
        self.assertEqual(ThinkingServer.seen_max_tokens, [3000, 3000])

    def test_max_tokens_setting(self):
        with mock.patch.dict(os.environ, {"LLM_MAX_TOKENS": "lots"}), self.assertRaisesRegex(RuntimeError, "whole number"):
            providers.max_tokens_setting()
