from __future__ import annotations

import unittest

from butler.launcher.core import _api_model_env


class ApiModelEnvTests(unittest.TestCase):
    def test_routes_llm_to_hosted_openai(self):
        env = _api_model_env({}, "gpt-4.1-mini")
        self.assertEqual(env["BUTLER_LLM_BACKEND"], "openai_chat")
        self.assertEqual(env["BUTLER_LLM_BASE_URL"], "https://api.openai.com/v1")
        self.assertEqual(env["BUTLER_LLM_MODEL"], "gpt-4.1-mini")

    def test_leaves_speech_to_text_local(self):
        # Hosted STT gated every clip on a CPU whisper "base" wake check that
        # missed the wake word; API mode must not re-route transcription.
        base = {"BUTLER_LOCAL_STT": "1", "BUTLER_REMOTE_STT_FALLBACK": "0"}
        env = _api_model_env(base, "gpt-4o-mini")
        self.assertEqual(env["BUTLER_LOCAL_STT"], "1")
        self.assertEqual(env["BUTLER_REMOTE_STT_FALLBACK"], "0")

        bare = _api_model_env({}, "gpt-4o-mini")
        self.assertNotIn("BUTLER_LOCAL_STT", bare)
        self.assertNotIn("BUTLER_REMOTE_STT_FALLBACK", bare)


if __name__ == "__main__":
    unittest.main()
