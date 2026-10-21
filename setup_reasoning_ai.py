"""Check Ollama and qwen2.5:3b; use --install to install missing components.

Python standard library only. No flags means an offline, read-only check.
Model files belong to this project, independently of the default Ollama service.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import urllib.error


ROOT = Path(__file__).resolve().parent
REASONING_DIR = ROOT / "ai_model" / "reasoning ai"
MODELS_DIR = REASONING_DIR / "models"
THRESHOLDS_PATH = REASONING_DIR / "thresholds.md"
MODEL = "qwen2.5:3b"
MANIFEST = MODELS_DIR / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
SERVE_ADDRESS = "127.0.0.1:11435"


class SetupError(RuntimeError):
    pass


def find_ollama():
    executable = shutil.which("ollama")
    if executable:
        return executable
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidate = Path(local) / "Programs" / "Ollama" / "ollama.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def engine_available(executable):
    if not executable:
        return False
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=30)
    return result.returncode == 0


def model_status():
    """Verify the local manifest and every referenced blob's existence and size."""
    if not MANIFEST.is_file():
        return False, "missing from the project model folder"
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        blobs = [manifest["config"], *manifest["layers"]]
        if len(blobs) < 2:
            raise ValueError("manifest has no model layers")
        for blob in blobs:
            digest, size = blob["digest"], blob["size"]
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("invalid blob digest")
            if type(size) is not int or size <= 0:
                raise ValueError("invalid blob size")
            path = MODELS_DIR / "blobs" / digest.replace(":", "-")
            if not path.is_file() or path.stat().st_size != size:
                return False, f"missing or incomplete blob: {path.name}"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return False, f"unreadable or invalid manifest: {exc}"
    # shortcut: normal checks use sizes; Ollama verifies downloads, --smoke-test checks loading.
    return True, "project manifest and blob sizes verified (no inference test)"


def server_environment(address):
    env = os.environ.copy()
    env.update(OLLAMA_MODELS=str(MODELS_DIR), OLLAMA_HOST=address, OLLAMA_NO_CLOUD="1")
    return env


def install_engine():
    system = platform.system()
    if system not in {"Windows", "Linux"}:
        raise SetupError("Automatic installation supports Windows and Ubuntu/Linux only.")
    url = "https://ollama.com/download/OllamaSetup.exe" if system == "Windows" else "https://ollama.com/install.sh"
    print(f"Downloading official Ollama installer: {url}", flush=True)
    with tempfile.TemporaryDirectory(prefix="ollama-setup-") as directory:
        installer = Path(directory) / ("OllamaSetup.exe" if system == "Windows" else "install.sh")
        with urllib.request.urlopen(url, timeout=120) as response, installer.open("wb") as output:
            shutil.copyfileobj(response, output)
        if system == "Windows":
            command = [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
            options = {"creationflags": subprocess.CREATE_NO_WINDOW}
        else:
            # The official script requests sudo itself when system installation needs it.
            command, options = ["sh", str(installer)], {}
        subprocess.run(command, check=True, **options)


def request_json(address, endpoint, payload=None, timeout=5):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://{address}{endpoint}", data=data,
        headers={"Content-Type": "application/json"},
    )
    # Local traffic must not be routed through a machine's HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        result = json.load(response)
    if "error" in result:
        raise SetupError(str(result["error"]))
    return result


@contextmanager
def temporary_server(executable):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        address = f"127.0.0.1:{port.getsockname()[1]}"
    with tempfile.TemporaryFile() as log:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if platform.system() == "Windows" else {}
        process = subprocess.Popen(
            [executable, "serve"], env=server_environment(address),
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options,
        )
        try:
            for _ in range(60):
                if process.poll() is not None:
                    raise SetupError("Ollama exited before startup; run ollama serve to diagnose native/CPU errors.")
                try:
                    request_json(address, "/api/version", timeout=1)
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(1)
            else:
                raise SetupError("Ollama did not start within 60 seconds.")
            yield address
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def pull_model(executable, address):
    print(f"Downloading {MODEL} into {MODELS_DIR} (may take several minutes)...", flush=True)
    subprocess.run([executable, "pull", MODEL], env=server_environment(address), check=True)


def smoke_test(address):
    response = request_json(address, "/api/generate", {
        "model": MODEL, "prompt": "Reply with the single word: ready",
        "stream": False, "keep_alive": 0,
        "options": {"num_ctx": 512, "num_predict": 12, "temperature": 0},
    }, timeout=300)
    answer = response.get("response", "").strip()
    if not answer:
        raise SetupError("The model returned an empty answer.")
    print(f"SMOKE: passed - generated: {answer}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="install missing Ollama and project model")
    parser.add_argument("--smoke-test", action="store_true", help="temporarily start Ollama and test generation")
    parser.add_argument("--serve", action="store_true", help=f"serve project models at {SERVE_ADDRESS} until Ctrl+C")
    args = parser.parse_args(argv)
    if args.serve and (args.install or args.smoke_test):
        parser.error("Run --serve separately from installation or smoke testing.")
    try:
        executable = find_ollama()
        available = engine_available(executable)
        if args.install and not available:
            if executable:
                raise SetupError("Ollama is installed but cannot run. Repair its installation before retrying.")
            install_engine()
            executable = find_ollama()
            available = engine_available(executable)
            if not available:
                raise SetupError("Ollama installer finished but its CLI is unavailable. Reopen the terminal and retry.")
        valid, detail = model_status()
        print(f"ENGINE: {'available' if available else 'missing or unable to run'}")
        print(f"MODEL: {MODEL} - {detail}")
        print(f"STORAGE: {MODELS_DIR}")
        if not available:
            print("Run with --install to install Ollama and the model.")
            return 1
        if args.serve:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            print(f"Serving project models at http://{SERVE_ADDRESS}; Ctrl+C stops it.", flush=True)
            subprocess.run([executable, "serve"], env=server_environment(SERVE_ADDRESS), check=True)
            return 0
        if args.install:
            REASONING_DIR.mkdir(parents=True, exist_ok=True)
            # Exclusive creation preserves any thresholds the owner has already written.
            try:
                with THRESHOLDS_PATH.open("x", encoding="utf-8"):
                    pass
            except FileExistsError:
                pass
        if (args.install and not valid) or (args.smoke_test and valid):
            with temporary_server(executable) as address:
                if args.install and not valid:
                    pull_model(executable, address)
                    valid, detail = model_status()
                    if not valid:
                        raise SetupError(f"Model pull did not create a complete project model: {detail}")
                    print(f"MODEL: {MODEL} installed")
                if args.smoke_test:
                    smoke_test(address)
        if not valid:
            print("Run with --install to download the missing project model.")
            return 1
        if THRESHOLDS_PATH.is_file():
            blank = not THRESHOLDS_PATH.read_text(encoding="utf-8").strip()
            print(f"THRESHOLDS: {'blank - waiting for your rules' if blank else 'present (not interpreted yet)'}")
        else:
            print("THRESHOLDS: missing - --install creates the empty placeholder")
        if not args.smoke_test:
            print("SMOKE: not requested; use --smoke-test for a real generation test.")
        return 0
    except (SetupError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
