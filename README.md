# demo-recorder

A Claude Code plugin that records product demo videos of web apps and terminal sessions from a short YAML storyboard, with an optional Amazon Polly voiceover.

- **Web demos** drive a real headless Chromium with a visible cursor, human-paced typing and scrolling, captions, highlight rings and title cards.
- **Terminal demos** type commands into a styled terminal window, really run them, and show their real output. Secrets are masked.
- **Voiceover** narrates either kind: each `say` step is voiced by Amazon Polly and mixed into the video.

Output is a 1080p MP4 (and optionally a GIF), plus a contact sheet of frames for a quick visual check.

## Install

    claude plugin marketplace add avansledright/demo-recorder
    claude plugin install demo-recorder@45sq-demo-recorder

Then ask Claude for a demo ("record a narrated demo of the pricing page on example.com") or run `/demo-recorder:record-demo`.

## Requirements

- `python3` and `ffmpeg` on PATH (Linux or macOS).
- The first run installs Playwright, Chromium and boto3 into `~/.cache/demo-recorder`. Nothing is installed system-wide.
- Voiceover only: AWS credentials that allow `polly:SynthesizeSpeech`. Polly is a paid service; a 60-second video costs roughly 3 cents on the generative engine. Clips are cached, so re-recording only pays for lines that changed.

## Use without Claude

    skills/record-demo/scripts/demo-record examples/seoscoreapi-web.yaml
    skills/record-demo/scripts/demo-record examples/seoscoreapi-voiceover.yaml
    skills/record-demo/scripts/demo-record examples/seoscoreapi-terminal.yaml

The terminal example needs a free SEO Score API key in `examples/.env`. Videos are written to `out/`.

A storyboard looks like this:

```yaml
type: web
viewport: { width: 1280, height: 720 }
voiceover: { voice: Matthew }
steps:
  - say: "See your site's SEO score in seconds."
  - goto: https://seoscoreapi.com
  - type: { selector: "#url-input", text: example.com }
  - click: "#audit-btn"
  - highlight: { selector: "#demo-result .score-display", caption: "A score and a grade", hold: 3 }
```

## Documentation

- [`skills/record-demo/SKILL.md`](skills/record-demo/SKILL.md): how Claude uses the recorder, and what makes a demo good
- [`skills/record-demo/references/storyboard.md`](skills/record-demo/references/storyboard.md): every step and option
- [`examples/`](examples): three working storyboards

## License

MIT. See [LICENSE](LICENSE).
