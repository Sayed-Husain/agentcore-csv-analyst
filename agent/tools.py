"""Tools the agent can call, plus helpers that prep the sandbox.

Image capture strategy: instead of `listFiles` + `readFiles` (whose response shapes
varied), we use `executeCode` for everything. After the agent's code runs, we run
a tiny glob inside the sandbox to find new image files, then read each file's
bytes via base64-encoded stdout. Single API surface, no shape guessing.
"""

import base64
import logging
import re

from strands import tool

logger = logging.getLogger(__name__)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg")

_B64_START = "===B64IMG_START==="
_B64_END = "===B64IMG_END==="
_B64_BLOCK_RE = re.compile(rf"{_B64_START}\s*\n(.*?)\n\s*{_B64_END}", re.DOTALL)


def upload_text_file(code_client, sandbox_path: str, content: str) -> None:
    """Write a text file into the Code Interpreter sandbox FS."""
    code_client.invoke(
        "writeFiles",
        {"content": [{"path": sandbox_path, "text": content}]},
    )


def _walk_stream(response) -> str:
    """Collect text + error content from a Code Interpreter response stream.

    Any non-text event surfaces as a tagged line so silent failures aren't lost.
    """
    chunks: list[str] = []
    for event in response["stream"]:
        if "error" in event:
            chunks.append(f"[stream-error] {event['error']}")
        result = event.get("result", {})
        if isinstance(result, dict) and result.get("isError"):
            chunks.append(f"[result-error] {result}")
        for item in result.get("content", []) if isinstance(result, dict) else []:
            t = item.get("type", "")
            if t == "text":
                chunks.append(item.get("text", ""))
            elif t == "error":
                chunks.append(f"[content-error] {item.get('text') or item.get('message') or item}")
    return "\n".join(chunks).strip()


def _exec(code_client, code: str) -> str:
    """Run code in the sandbox, return stdout (and any error text)."""
    resp = code_client.invoke(
        "executeCode",
        {"code": code, "language": "python", "clearContext": False},
    )
    return _walk_stream(resp)


def _find_new_images(code_client, seen: set[str]) -> list[str]:
    """Glob inside the sandbox for image files we haven't already captured."""
    globs = " + ".join(f"glob.glob('*{ext}')" for ext in IMAGE_EXTS)
    code = (
        "import glob\n"
        f"_found = sorted(set({globs}))\n"
        "print('\\n'.join(_found))\n"
    )
    out = _exec(code_client, code)
    paths = [line.strip() for line in out.splitlines() if line.strip()]
    return [p for p in paths if p not in seen]


def _read_image_bytes(code_client, path: str) -> bytes | None:
    """Pull a file's bytes back via a base64-encoded stdout block."""
    code = (
        "import base64\n"
        f"with open({path!r}, 'rb') as _f:\n"
        "    _b = _f.read()\n"
        f"print({_B64_START!r})\n"
        "print(base64.b64encode(_b).decode())\n"
        f"print({_B64_END!r})\n"
    )
    out = _exec(code_client, code)
    match = _B64_BLOCK_RE.search(out)
    if not match:
        logger.warning("No base64 block in read-back for %s — output: %r", path, out[:300])
        return None
    try:
        return base64.b64decode(match.group(1).replace("\n", "").replace(" ", ""))
    except Exception as e:
        logger.warning("Base64 decode failed for %s: %s", path, e)
        return None


def make_python_executor(code_client, image_sink: list | None = None):
    """Build a Strands tool bound to an open Code Interpreter session."""
    seen_images: set[str] = set()

    @tool
    def python_executor(code: str) -> str:
        """Execute Python code in a sandboxed environment.

        pandas, numpy, and matplotlib are pre-installed. Variables, imports, and
        files persist across calls within a single agent run.

        For charts: build the figure, then `plt.savefig('<unique>.png', bbox_inches='tight')`
        followed by `plt.close()`. Use a fresh filename per chart. Do NOT call
        `plt.show()` — the sandbox is headless. The UI auto-detects and displays
        any new image file you save.

        Always `print()` text results — only stdout is returned.

        Args:
            code: Python source to execute.
        """
        output = _exec(code_client, code)
        logger.info("python_executor stdout: %r", output[:300])

        if image_sink is not None:
            try:
                new_paths = _find_new_images(code_client, seen_images)
                if new_paths:
                    logger.info("New image files: %s", new_paths)
                for path in new_paths:
                    seen_images.add(path)
                    img_bytes = _read_image_bytes(code_client, path)
                    if img_bytes:
                        image_sink.append(img_bytes)
                        logger.info("✓ Captured %s (%d bytes)", path, len(img_bytes))
            except Exception as e:
                logger.warning("Image capture error: %s", e)

        return output if output else "(executed; no stdout)"

    return python_executor
