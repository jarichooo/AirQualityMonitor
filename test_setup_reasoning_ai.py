import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import setup_reasoning_ai as setup


class ReasoningSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reasoning setup ")
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name) / "ai_model" / "reasoning ai"
        patch = mock.patch.multiple(setup, REASONING_DIR=directory,
            MODELS_DIR=directory / "models", THRESHOLDS_PATH=directory / "thresholds.md",
            MANIFEST=directory / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b")
        patch.start()
        self.addCleanup(patch.stop)
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def write_model(self):
        setup.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        blob = {"digest": "sha256:" + "a" * 64, "size": 4}
        setup.MANIFEST.write_text(json.dumps({"config": blob, "layers": [blob]}), encoding="utf-8")
        folder = setup.MODELS_DIR / "blobs"
        folder.mkdir(exist_ok=True)
        (folder / ("sha256-" + "a" * 64)).write_bytes(b"data")

    def test_check_missing_is_read_only_and_offline(self):
        with mock.patch.object(setup, "find_ollama", return_value=None), mock.patch.object(setup, "install_engine") as install:
            self.assertEqual(setup.main([]), 1)
        install.assert_not_called()
        self.assertFalse(setup.REASONING_DIR.exists())

    def test_existing_install_reused_and_thresholds_preserved(self):
        self.write_model()
        setup.THRESHOLDS_PATH.write_text("my rules", encoding="utf-8")
        with mock.patch.object(setup, "find_ollama", return_value="ollama"), mock.patch.object(setup, "engine_available", return_value=True), mock.patch.object(setup, "install_engine") as install, mock.patch.object(setup, "temporary_server") as server:
            self.assertEqual(setup.main(["--install"]), 0)
        install.assert_not_called()
        server.assert_not_called()
        self.assertEqual(setup.THRESHOLDS_PATH.read_text(), "my rules")

    def test_install_missing_engine_and_model(self):
        @contextlib.contextmanager
        def server(_):
            yield "127.0.0.1:12345"
        with mock.patch.object(setup, "find_ollama", side_effect=[None, "ollama"]), mock.patch.object(setup, "engine_available", side_effect=[False, True]), mock.patch.object(setup, "install_engine") as install, mock.patch.object(setup, "temporary_server", server), mock.patch.object(setup, "pull_model", side_effect=lambda *_: self.write_model()) as pull:
            self.assertEqual(setup.main(["--install"]), 0)
        install.assert_called_once()
        pull.assert_called_once_with("ollama", "127.0.0.1:12345")
        self.assertEqual(setup.THRESHOLDS_PATH.read_bytes(), b"")

    def test_incomplete_or_unsafe_manifest_is_rejected(self):
        self.write_model()
        (setup.MODELS_DIR / "blobs" / ("sha256-" + "a" * 64)).write_bytes(b"bad")
        self.assertFalse(setup.model_status()[0])
        setup.MANIFEST.write_text('{"config":{"digest":"../../outside","size":4},"layers":[]}', encoding="utf-8")
        self.assertFalse(setup.model_status()[0])

    def test_install_failure_reports_nonzero(self):
        with mock.patch.object(setup, "find_ollama", return_value=None), mock.patch.object(setup, "install_engine", side_effect=subprocess.CalledProcessError(1, "installer")):
            self.assertEqual(setup.main(["--install"]), 1)

    def test_pull_uses_exact_model_and_project_store(self):
        with mock.patch.object(setup.subprocess, "run") as run:
            setup.pull_model("ollama", "127.0.0.1:12345")
        self.assertEqual(run.call_args.args[0], ["ollama", "pull", "qwen2.5:3b"])
        self.assertEqual(run.call_args.kwargs["env"]["OLLAMA_MODELS"], str(setup.MODELS_DIR))
        self.assertEqual(run.call_args.kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:12345")

    def test_smoke_empty_response_is_failure(self):
        with mock.patch.object(setup, "request_json", return_value={"response": ""}):
            with self.assertRaises(setup.SetupError):
                setup.smoke_test("127.0.0.1:12345")

    def test_serve_allows_missing_model_for_manual_repair(self):
        with mock.patch.object(setup, "find_ollama", return_value="ollama"), mock.patch.object(setup, "engine_available", return_value=True), mock.patch.object(setup.subprocess, "run") as run:
            self.assertEqual(setup.main(["--serve"]), 0)
        self.assertEqual(run.call_args.args[0], ["ollama", "serve"])
        self.assertEqual(run.call_args.kwargs["env"]["OLLAMA_HOST"], setup.SERVE_ADDRESS)

    def test_server_is_stopped_after_failed_operation(self):
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch.object(setup.subprocess, "Popen", return_value=process), mock.patch.object(setup, "request_json", return_value={"version": "test"}):
            with self.assertRaises(RuntimeError):
                with setup.temporary_server("ollama"):
                    raise RuntimeError("download failed")
        process.terminate.assert_called_once()
        process.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
