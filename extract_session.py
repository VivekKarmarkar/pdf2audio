#!/usr/bin/env python3
"""Extract listenable narrative from a Claude Code session JSONL transcript."""
import json
import re
import sys
from pathlib import Path

# Drop any block wrapped in these XML-style envelope tags.
ENVELOPE_TAGS = ("system-reminder", "command-name", "command-message",
                 "command-args", "local-command-stdout", "local-command-caveat",
                 "task-notification", "channel", "user-prompt-submit-hook",
                 "command-stderr")
SKIP_BLOCK = re.compile(
    r"<(" + "|".join(ENVELOPE_TAGS) + r")\b[^>]*>.*?</\1>",
    re.DOTALL,
)
ANY_TAG = re.compile(r"<[^>]+>")

# User messages whose content starts with one of these are tool/skill plumbing,
# not real user speech.
SKILL_ENVELOPE_PREFIXES = (
    "Base directory for this skill:",
    "Tool loaded.",
    "Launching skill:",
    "Skill loaded:",
    "Caveat:",
    "ultraplan:",
)


def clean(text: str) -> str:
    text = SKIP_BLOCK.sub("", text)
    text = ANY_TAG.sub("", text)
    return text.strip()


def is_skill_plumbing(text: str) -> bool:
    head = text.lstrip()[:120]
    return any(head.startswith(p) for p in SKILL_ENVELOPE_PREFIXES)


def extract(path: Path) -> str:
    out: list[str] = []
    last_role = None
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        text_parts: list[str] = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(block.get("text") or "")
                # Skip tool_use, tool_result, thinking, image.
        text = clean("\n".join(text_parts))

        if not text:
            continue
        if role == "user" and is_skill_plumbing(text):
            continue
        # Skip bare slash-command echoes.
        if text.startswith("/") and len(text) < 80 and "\n" not in text:
            continue

        label = "User said." if role == "user" else "Assistant replied."
        if role != last_role:
            out.append(f"\n\n{label}\n{text}")
            last_role = role
        else:
            out.append(f"\n{text}")

    return ("Recording of a Claude Code session in which we built "
            "pdf2audio, a tool that converts documents to MP3.\n"
            + "".join(out).strip())


if __name__ == "__main__":
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".txt")
    dst.write_text(extract(src))
    print(f"wrote {dst} ({dst.stat().st_size:,} bytes)")
