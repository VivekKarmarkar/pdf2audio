# pdf2audio — Design

**Date:** 2026-04-20
**Status:** Implemented (v0) — `pdf2audio.py`

## Purpose

Convert text-bearing documents (PDF, Markdown, TXT, Claude Code session
transcripts) into a single MP3 audio file, preserving content **verbatim**
(no summarization or paraphrasing). The MP3 can be shared, uploaded to
cloud storage, or emailed. This distinguishes it from NotebookLM-style tools
that synthesize podcast-style dialogues and rewrite the source.

## Autonomous decisions

Locked in without further back-and-forth per user direction:

| Decision | Choice | Rationale |
|---|---|---|
| TTS provider | **OpenAI** (`gpt-4o-mini-tts`) | User confirmed. Cheap (~$0.015/1K chars), high-quality voices, `OPENAI_API_KEY` already set. |
| Fidelity | **Verbatim prose + structural cleanup** | Reads every sentence of the source; strips only artifacts that sound broken (page numbers, hyphenated line breaks, reference markers). Opt out with `--no-clean`. |
| Output format | **MP3** | Universally playable, small, emails/uploads trivially. |
| Interface | **Single-file Python CLI** | No framework weight; easy to wire into other skills (`/email`, `/upload`). |
| Voice | **alloy** default, selectable via `--voice` | Neutral, listenable for long-form. |
| Sharing | Delegated to existing skills (`email`, `upload`, `share`) | The MP3 is just a file — no reason to reinvent GDrive/Gmail plumbing. |

## Architecture

```
┌───────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌─────────┐
│  input    │ → │ extract  │ → │  clean  │ → │  chunk   │ → │   TTS   │
│ .pdf/.md/ │   │pdftotext │   │ regex   │   │≤3500 chr │   │ OpenAI  │
│ .txt/.log │   │ / read   │   │ passes  │   │ sentence │   │ streams │
└───────────┘   └──────────┘   └─────────┘   └──────────┘   └────┬────┘
                                                                  ▼
                                             ┌──────────┐   ┌─────────┐
                                             │  final   │ ← │ ffmpeg  │
                                             │  .mp3    │   │ concat  │
                                             └──────────┘   └─────────┘
```

### Components

**`extract_text(path)`** — dispatches by suffix. PDFs via `pdftotext -nopgbrk`
(default layout, not `-layout`, because column-preservation garbles reading
order for prose). MD/TXT/LOG read as UTF-8.

**`clean_text(raw, is_markdown)`** — ordered regex passes:

1. Drop everything from a `References`/`Bibliography`/`Works Cited` heading
   onward (academic PDFs).
2. If markdown: strip fenced code blocks, inline code, images, link syntax
   (keep anchor text), **blockquote markers first**, then heading `#`s,
   emphasis markers, bullet markers, table rows, horizontal rules. Order
   matters — blockquote stripping must precede heading stripping so a
   `> ## Title` line is cleanly unwrapped.
3. Dehyphenate line-break hyphens: `investi-\ngation` → `investigation`.
4. Drop lone-number lines (page numbers).
5. Strip inline reference markers `[12]`, `[3, 4]`.
6. Fold soft-wrapped prose lines into flowing paragraphs.
7. Collapse whitespace; normalize blank-line spacing.

**`chunk_text(text, target=3500)`** — OpenAI TTS has a 4096-char request
limit; aim for 3500 to leave headroom. Split on paragraph boundaries first,
then sentence boundaries; fall back to hard whitespace split for
pathologically long sentences. Greedily pack adjacent fragments up to target
so we issue the fewest API calls.

**`synthesize(chunks, out, voice, model, speed)`** — calls OpenAI TTS
streaming endpoint per chunk, writes part files to tempdir, concatenates via
`ffmpeg -f concat -c copy` (stream copy — no re-encode, no quality loss).
Single-chunk inputs skip ffmpeg entirely.

### CLI

```
pdf2audio.py INPUT [-o OUT.mp3] [--voice V] [--model M] [--speed S]
                   [--no-clean] [--dry-run]
```

- `--dry-run` reports char count, chunk count, estimated cost, and a 500-char
  cleaned preview without calling the API. Cheap way to verify extraction.
- `--no-clean` bypasses structural cleanup for true verbatim playback.
- Cost estimate printed before synthesis so the user can ctrl-C a surprise.

## Error handling

- Unsupported suffix → `ValueError` with the extension.
- Missing input → exit 2 with message.
- Missing `OPENAI_API_KEY` → exit 2 (unless `--dry-run`).
- `pdftotext` / `ffmpeg` failures bubble up as `CalledProcessError` with
  stderr (captured). No silent swallowing.
- TTS chunk failure: surfaces the OpenAI SDK exception; aborts the whole run
  rather than producing a partial file.

## Sharing / distribution

Out of scope for this module — the output is a plain MP3. Existing skills
handle distribution:
- `/upload` + `/share` → Google Drive with a shareable link.
- `/email` → attach the MP3 to a Gmail message.
- Manual: any file manager, Dropbox, S3, etc.

## Sample outputs

Validated against the three sample files in the project root:

| Input | Chars after cleanup | Chunks | Est. cost |
|---|---|---|---|
| `chen_arc_story.pdf` | 8,270 | 4 | $0.12 |
| `ergodic_arc_story.pdf` | 10,034 | 3 | $0.15 |
| `whatsnew_cc_20_April_2026.md` | 2,163 | 1 | $0.03 |

## Out of scope (YAGNI)

- Equation-to-speech conversion (would require an LLM pass; risks
  hallucination of "verbatim" content).
- Multi-speaker / dialogue generation (that's NotebookLM territory — the
  explicit non-goal).
- Streaming-while-synthesizing playback.
- GUI / web UI.
- Language detection / non-English voices (OpenAI voices handle many
  languages, but tuning is a follow-up).
- Resume-on-failure / partial-chunk caching. Short documents re-run cheaply.
