#!/usr/bin/env python3
"""
pdf2audio — Convert PDF / Markdown / TXT / Claude session transcripts to MP3.

Reads content verbatim (no summarization / paraphrasing), applies structural
cleanup (dehyphenation, page-number removal, whitespace collapse), then
synthesizes speech via the OpenAI TTS API and concatenates the pieces into a
single MP3.

Usage:
    python3 pdf2audio.py INPUT [-o OUT.mp3] [--voice alloy] [--model gpt-4o-mini-tts]
                               [--speed 1.0] [--no-clean]

Supported inputs: .pdf, .md, .markdown, .txt, .log (Claude session dumps)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from openai import OpenAI

# OpenAI TTS hard limit is 4096 chars/request. We aim lower so we can split on
# sentence boundaries without thrashing.
CHUNK_TARGET = 3500

VOICES = {"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
          "sage", "shimmer", "verse"}


# ───────────────────────── text extraction ─────────────────────────

def extract_text(path: Path) -> str:
    """Pull raw text out of a supported input file."""
    suf = path.suffix.lower()
    if suf == ".pdf":
        # pdftotext with default layout gives us readable flow; -layout
        # preserves columns which can garble reading order, so avoid it.
        result = subprocess.run(
            ["pdftotext", "-nopgbrk", str(path), "-"],
            check=True, capture_output=True, text=True,
        )
        return result.stdout
    if suf in {".md", ".markdown", ".txt", ".log"}:
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"Unsupported input type: {suf}")


# ───────────────────────── text cleanup ─────────────────────────

# Page numbers on their own line (e.g. "  12  ").
_PAGE_NUM_LINE = re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE)
# Hyphenated line-break: "investi-\ngation" → "investigation".
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
# Soft-wrapped prose line breaks (lone newlines inside a paragraph).
_SOFT_WRAP = re.compile(r"([^\n])\n(?!\n)(?=[a-z(\[\"'])")
# Reference markers like "[12]" or "[12, 34]" inline in prose.
_REFS = re.compile(r"\[\d+(?:\s*[,\u2013-]\s*\d+)*\]")
# Markdown syntax we don't want read aloud.
_MD_STRIP = [
    (re.compile(r"^```.*?\n.*?^```", re.DOTALL | re.MULTILINE), ""),  # fenced code
    (re.compile(r"`([^`]+)`"), r"\1"),                                # inline code
    (re.compile(r"!\[[^\]]*\]\([^)]+\)"), ""),                        # images
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),                    # links → anchor
    (re.compile(r"^>\s?", re.MULTILINE), ""),                         # blockquote (first, may expose #)
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),                    # heading #s
    (re.compile(r"[*_]{1,3}([^*_\n]+)[*_]{1,3}"), r"\1"),             # bold/italic
    (re.compile(r"^[\-*+]\s+", re.MULTILINE), ""),                    # bullet markers
    (re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE), ""),                # table rows
    (re.compile(r"^[-=]{3,}\s*$", re.MULTILINE), ""),                 # hrules / setext
]

# "References" / "Bibliography" section — drop everything from there on.
_REFS_HEADING = re.compile(
    r"\n\s*(?:references|bibliography|works cited)\s*\n",
    re.IGNORECASE,
)


def clean_text(raw: str, is_markdown: bool = False) -> str:
    text = raw

    # Strip reference/bibliography tail (academic PDFs).
    m = _REFS_HEADING.search(text)
    if m:
        text = text[: m.start()]

    if is_markdown:
        for pat, repl in _MD_STRIP:
            text = pat.sub(repl, text)

    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _PAGE_NUM_LINE.sub("", text)
    text = _REFS.sub("", text)
    text = _SOFT_WRAP.sub(r"\1 ", text)

    # Collapse 3+ newlines to paragraph break, trim trailing spaces.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


# ───────────────────────── chunking ─────────────────────────

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def chunk_text(text: str, target: int = CHUNK_TARGET) -> list[str]:
    """Split text into <=target-char chunks, preferring sentence boundaries."""
    chunks: list[str] = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= target:
            _append(chunks, para, target)
            continue
        sentences = _SENT_SPLIT.split(para)
        buf = ""
        for s in sentences:
            if len(buf) + len(s) + 1 <= target:
                buf = f"{buf} {s}".strip()
            else:
                if buf:
                    _append(chunks, buf, target)
                if len(s) > target:
                    # Sentence itself too long — hard split on whitespace.
                    for piece in _hard_split(s, target):
                        _append(chunks, piece, target)
                    buf = ""
                else:
                    buf = s
        if buf:
            _append(chunks, buf, target)
    return chunks


def _append(chunks: list[str], piece: str, target: int) -> None:
    piece = piece.strip()
    if not piece:
        return
    if chunks and len(chunks[-1]) + len(piece) + 2 <= target:
        chunks[-1] = f"{chunks[-1]}\n\n{piece}"
    else:
        chunks.append(piece)


def _hard_split(s: str, target: int) -> list[str]:
    words = s.split()
    out, buf = [], ""
    for w in words:
        if len(buf) + len(w) + 1 > target:
            out.append(buf)
            buf = w
        else:
            buf = f"{buf} {w}".strip()
    if buf:
        out.append(buf)
    return out


# ───────────────────────── TTS + assembly ─────────────────────────

def synthesize(chunks: list[str], out_path: Path, voice: str, model: str,
               speed: float) -> None:
    client = OpenAI()
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        parts: list[Path] = []
        for i, chunk in enumerate(chunks, 1):
            part = tmpdir / f"part_{i:04d}.mp3"
            print(f"  [{i}/{len(chunks)}] synthesizing ({len(chunk)} chars)…",
                  flush=True)
            kwargs = {"model": model, "voice": voice, "input": chunk,
                      "response_format": "mp3"}
            if speed != 1.0:
                kwargs["speed"] = speed
            with client.audio.speech.with_streaming_response.create(**kwargs) as r:
                r.stream_to_file(part)
            parts.append(part)

        if len(parts) == 1:
            parts[0].replace(out_path)
            return

        concat_list = tmpdir / "list.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in parts)
        )
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(out_path)],
            check=True, capture_output=True,
        )


# ───────────────────────── CLI ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Convert text documents to MP3 audio.")
    ap.add_argument("input", type=Path, help="PDF / MD / TXT file")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="output MP3 path (default: <input>.mp3)")
    ap.add_argument("--voice", default="alloy", choices=sorted(VOICES))
    ap.add_argument("--model", default="gpt-4o-mini-tts",
                    help="OpenAI TTS model (gpt-4o-mini-tts, tts-1, tts-1-hd)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="playback speed 0.25–4.0 (default 1.0)")
    ap.add_argument("--no-clean", action="store_true",
                    help="skip structural cleanup (truly verbatim)")
    ap.add_argument("--dry-run", action="store_true",
                    help="extract + clean + chunk, print stats, don't call TTS")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"error: {args.input} not found", file=sys.stderr)
        return 2
    if not os.environ.get("OPENAI_API_KEY") and not args.dry_run:
        print("error: OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    out = args.output or args.input.with_suffix(".mp3")

    print(f"→ extracting {args.input.name}")
    raw = extract_text(args.input)
    is_md = args.input.suffix.lower() in {".md", ".markdown"}
    text = raw if args.no_clean else clean_text(raw, is_markdown=is_md)
    chunks = chunk_text(text)

    total = sum(len(c) for c in chunks)
    print(f"→ {total:,} chars in {len(chunks)} chunk(s)")
    # OpenAI TTS pricing ~$0.015 per 1K chars for gpt-4o-mini-tts
    est_cost = total / 1000 * 0.015
    print(f"→ est. cost: ${est_cost:.3f} (model={args.model})")

    if args.dry_run:
        preview = text[:500].replace("\n", " ")
        print(f"\n--- cleaned preview ---\n{preview}…\n")
        return 0

    print(f"→ synthesizing → {out}")
    synthesize(chunks, out, args.voice, args.model, args.speed)
    size_kb = out.stat().st_size / 1024
    print(f"✓ wrote {out} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
