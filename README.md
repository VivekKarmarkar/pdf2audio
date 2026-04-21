# pdf2audio

Convert PDFs, Markdown, plain text, log files, and Claude Code session transcripts into a single MP3 — **verbatim**, not summarized.

## Overview

pdf2audio is a single-file Python CLI that reads a document, extracts and cleans its text, chunks it at sentence boundaries, synthesizes speech with the OpenAI TTS API, and stitches the pieces into one MP3 using `ffmpeg`. Unlike NotebookLM-style tools, it does not paraphrase, summarize, or generate a dialogue — the audio you hear is the words in the document, in order.

It was built for listening to papers, homework, and session recaps on the go.

## Features

- **Verbatim narration.** Every sentence of the source reaches the output; the only edits are structural artifacts that sound broken when read aloud.
- **Multi-format input.** `.pdf`, `.md` / `.markdown`, `.txt`, `.log`, and Claude Code session JSONL (via `extract_session.py`).
- **Structural cleanup.** Dehyphenates line-break hyphens, drops page-number-only lines and inline reference markers (`[12]`), strips markdown (blockquote before heading, so `> ## Title` unwraps cleanly), and trims everything after a `References` / `Bibliography` heading.
- **Sentence-aware chunking.** Packs chunks at ≤ 3500 chars (under OpenAI's 4096 limit) at sentence boundaries for the fewest API calls.
- **Lossless concatenation.** `ffmpeg -f concat -c copy` joins parts without re-encoding.
- **Session transcripts.** `extract_session.py` flattens a `~/.claude/projects/*/<uuid>.jsonl` into clean alternating "User said." / "Assistant replied." prose — no tool calls, thinking blocks, system reminders, or skill envelope text.
- **Dry-run cost preview.** `--dry-run` reports char count, chunk count, and estimated cost without calling the API.

## Getting Started

### Prerequisites

- Python 3.10+
- `openai` SDK (tested with 2.17)
- `ffmpeg` and `pdftotext` (from Poppler) on `PATH` — both standard on most Linux distros
- `OPENAI_API_KEY` environment variable

### Installation

```bash
git clone https://github.com/VivekKarmarkar/pdf2audio.git
cd pdf2audio
pip install openai
```

### Usage

```bash
# Dry run — preview cost and cleaned text, no API call
python3 pdf2audio.py paper.pdf --dry-run

# Convert a PDF to MP3 (writes paper.mp3 next to the input)
python3 pdf2audio.py paper.pdf

# Custom output path, voice, and speed
python3 pdf2audio.py notes.md -o notes.mp3 --voice nova --speed 1.1

# Audify a Claude Code session
python3 extract_session.py \
    ~/.claude/projects/-home-user-my-project/abc123.jsonl session.txt
python3 pdf2audio.py session.txt
```

### CLI reference

```
pdf2audio.py INPUT [-o OUT.mp3] [--voice V] [--model M]
                   [--speed S] [--no-clean] [--dry-run]
```

| Flag | Default | Notes |
|---|---|---|
| `-o, --output` | `<input>.mp3` | Output path |
| `--voice` | `alloy` | `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `nova`, `onyx`, `sage`, `shimmer`, `verse` |
| `--model` | `gpt-4o-mini-tts` | Also: `tts-1`, `tts-1-hd` |
| `--speed` | `1.0` | Range 0.25–4.0 |
| `--no-clean` | off | Skip structural cleanup (true verbatim — sounds rough on PDFs) |
| `--dry-run` | off | Extract + clean + chunk + cost estimate, no API call |

Approximate cost with `gpt-4o-mini-tts`: **$0.015 per 1,000 characters**. A 70,000-char paper ≈ $1.

## Project Structure

```
pdf2audio/
├── pdf2audio.py              # Main CLI: extract → clean → chunk → TTS → concat
├── extract_session.py        # Claude Code session JSONL → narration text
├── docs/superpowers/specs/
│   └── 2026-04-20-pdf2audio-design.md   # Design doc
└── *.pdf, *.md               # Sample inputs
```

## How it works

```
input → extract → clean → chunk (≤3500 chars) → OpenAI TTS per chunk
                                                         ↓
                                          ffmpeg concat -c copy → MP3
```

- PDFs go through `pdftotext -nopgbrk` with default flow (not `-layout` — column-preserving mode garbles reading order on two-column papers).
- Single-chunk inputs skip `ffmpeg` entirely.

## Out of scope (deliberate)

- Equation-to-speech (would need an LLM pass; risks hallucinating "verbatim" content).
- Multi-speaker / dialogue generation (that's NotebookLM — the explicit non-goal).
- Streaming-while-synthesizing playback.
- GUI.
- Partial-resume / chunk caching — short inputs re-run cheaply.

## License

No license file. All rights reserved by the author unless a `LICENSE` is added.
