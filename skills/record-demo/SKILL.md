---
name: record-demo
description: Record a polished product demo video (1080p MP4, optional GIF, optional Amazon Polly voiceover) of a web application or a terminal session from a short YAML storyboard. Use when asked to make a screen recording, product walkthrough, demo video, promo clip, or "show how easy it is to...", or to add narration or a voiceover to a demo, for a website, web app, API, or CLI tool.
---

# Record a product demo

`scripts/demo-record STORYBOARD.yaml` turns a storyboard into a video. Two modes:

- **web**: drives a real headless Chromium with a visible cursor, human-paced mouse movement, typing and scrolling, plus captions, highlight rings and title cards.
- **terminal**: types commands into a styled terminal window, really runs them, and shows their real output.

Either mode can be narrated: `say` steps are voiced with Amazon Polly and mixed into the video.

The first run installs Playwright and boto3 into `~/.cache/demo-recorder` (about a minute). `ffmpeg` must be on PATH. Voiceover needs AWS credentials that allow `polly:SynthesizeSpeech`.

## Workflow

1. **Scout before you script.** Do not guess selectors or output.
   - Web: fetch the page (`curl -s URL`) and find stable selectors (`id`s first) for every element the demo touches. For content rendered by JavaScript after an action, read the page's script to learn what markup appears.
   - Terminal: run each command yourself first. Shape the output so it fits one screen (`jq` filters, `head`); about 20 lines of 85 characters fit at the default font size.
2. **Write the storyboard** next to the project (for example `demos/<name>.yaml`). Start from `examples/seoscoreapi-web.yaml` or `examples/seoscoreapi-terminal.yaml` in the plugin root. Every step and option is in `references/storyboard.md`.
3. **Record**: `<skill dir>/scripts/demo-record demos/<name>.yaml`. It prints the video path and a `.sheet.jpg` contact sheet.
4. **Look at the contact sheet** with the Read tool, every time. Check that nothing is cut off, captions do not cover the thing they describe, and the result screens are what you expected. To inspect one moment closely: `ffmpeg -ss <sec> -i video.mp4 -frames:v 1 frame.jpg`.
5. **Check the narration timing** if the demo has a voiceover. You cannot hear the video, so read the `voice` lines the recorder prints (start and end of every line) against the contact sheet: each line should be heard over the screen it describes.
6. **Fix and re-record** until the sheet is clean, then report the video path, its length, and anything the storyboard deliberately left out.

If a step fails, the recorder stops, names the step, and saves `<output>.error.png` showing the page at that moment. Read it before changing anything.

## What makes a demo good

- **30 to 60 seconds, one idea.** Open with the question the viewer has, show the product answering it, end on the call to action.
- **Hold on results.** 3 to 4 seconds on any screen the viewer must read; `highlight` with a `caption` says what to look at.
- **Captions carry the story** when there is no voiceover, and still matter when there is one, because most feeds autoplay muted. Keep each under about 60 characters so it stays on one line, and state the benefit rather than describing the click.
- **Small viewport, scaled up.** `viewport: {width: 1024, height: 576}` with `scale: 1.875` renders 1920x1080 with text large enough to read on a phone. Use 1280x720 at `scale: 1.5` for dense dashboards.
- **Cut the boring parts** with `hide` (logins, long loads) or `cut_wait` (slow commands), but never fake a result: commands really run and pages really load.

## Voiceover

Add a voiceover when the user asks for narration, a voiceover or a "talking" demo. Full options are in `references/storyboard.md`.

- Put each `say` just before the steps it describes. It does not block, so those steps play under the voice, and the next `say` waits for the line to finish.
- Write for the ear: one short sentence per `say`, about 2.5 words per second, benefits rather than clicks. Give the screen it describes at least as long as the line takes (`hold` on the `highlight`).
- Keep captions as a shorter version of what is said, not a transcript.
- Polly mispronounces product names and initialisms that are not ordinary words (GPTBot, kubectl, nginx, OAuth). Before recording, scan every line for them and add each to `voiceover.pronounce` spelled as it should sound (`GPTBot: "G P T bot"`). You cannot hear the result, so tell the user which respellings you added and ask them to listen for any others.
- Never put a secret in a `say`.
- Polly is a paid AWS service (a 60-second demo is about 900 characters: roughly 3 cents on the generative engine, 9 cents on long-form). Clips are cached, so only changed lines are synthesized again.

## Secrets

- Terminal: load keys with `env_file` and reference them as `$VAR` in commands. The shell expands them, so the video shows `$VAR`, and any secret value that appears in command output is masked automatically (variables named like KEY, TOKEN, SECRET or PASSWORD, plus anything listed under `redact`).
- Web: text typed into a page is visible. Put login steps inside `hide` so they happen off camera, and never type a real key into a visible field.
- Never write a secret value into a storyboard file; use `${VAR}` (web) or `$VAR` (terminal).
