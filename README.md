# Readout — PDFs, read aloud, word by word

Readout is a desktop PDF reader that speaks your documents and **highlights each word on the real page as it's spoken**, like karaoke for books. You can see exactly where the voice is, click any word to jump there, and pick up later where you stopped.

## Features

- **Live follow-along**: the spoken word is highlighted on the rendered PDF page, its line is tinted, and the view scrolls smoothly to keep up.
- **Captions bar**: the current sentence is shown in large type with the active word marked. Useful for focus, language learners and dyslexic readers.
- **Click to seek**: click a word to move the reading cursor there, or double-click to start reading from it.
- **Resume anywhere**: every PDF remembers its page and word. The Library sidebar shows your progress through each document.
- **Live controls**: voice, speed (wpm) and volume changes take effect mid-sentence.
- **Smart text cleanup**: rejoins words hyphenated across line breaks, expands ligatures, adds pauses after headings and list items, and can skip page headers/footers.
- **Export audiobook**: turn any page range into a single `.wav` file.
- **Sleep timer**: pause after 5–60 minutes, or at the end of the current page.
- **Night paper**: a dark reading mode that inverts brightness but keeps the original colours.
- **Time left estimate**, a clickable progress bar, page jump, zoom/fit-width, and password-protected PDF support.

## Getting started

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python main.py                  # or: python main.py path\to\book.pdf
```

Readout uses your system's speech voices (SAPI5 on Windows, NSSpeechSynthesizer on macOS, eSpeak on Linux). To get more voices on Windows, go to *Settings → Time & language → Speech → Add voices*.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| `Space` | Play / pause |
| `←` / `→` | Previous / next sentence |
| `PgUp` / `PgDn` | Previous / next page |
| `Ctrl+O` | Open PDF |
| `Ctrl+E` | Export audio |
| `Ctrl` + `+` / `-` / `0` | Zoom in / out / fit width (or `Ctrl` + mouse wheel) |

## Project layout

```
main.py              entry point
readout/app.py       the window: viewer, highlighting, playback, library
readout/document.py  PDF rendering + speech-ready text with word positions (PyMuPDF)
readout/speech.py    background text-to-speech worker (pyttsx3)
readout/store.py     settings and reading positions (~/.readout/state.json)
```

Pages with no text layer (scanned images) are skipped, and Readout tells you when that happens.
