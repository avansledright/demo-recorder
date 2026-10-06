# Storyboard reference

A storyboard is a YAML (or JSON) file. Paths in it are relative to the storyboard file.

## Top-level keys

| Key | Default | Meaning |
| --- | --- | --- |
| `type` | `web` | `web` or `terminal` |
| `output` | `out/<storyboard name>.mp4` | Video path. A `.sheet.jpg` contact sheet is written beside it. |
| `viewport` | `{width: 1280, height: 720}` | Browser size in CSS pixels |
| `scale` | `1.5` | Device pixel ratio. Output size is viewport × scale (1920x1080 by default). |
| `fps` / `crf` | `30` / `18` | Frame rate and x264 quality (lower is sharper and larger) |
| `gif` | `false` | Also write a 960px-wide GIF |
| `tail` | `1.0` | Seconds to hold after the last step |
| `timeout` | `20` | Seconds to wait for any element before failing |
| `color_scheme` | `dark` | `dark` or `light`, for sites that follow the system theme |
| `env_file` | none | `.env` file to load. Values are available as `$VAR` in terminal commands and `${VAR}` in web steps. |
| `redact` | none | Extra variable names whose values must be masked in terminal output |
| `cwd` | storyboard folder | Terminal only: where commands run. `tmp` uses a scratch folder that is deleted afterwards. |
| `terminal` | none | Terminal only: `{title, font_size (19), prompt ("❯ ")}` |
| `voiceover` | none | Defaults for `say` steps: `{voice (Matthew), engine (generative), language, pronounce, profile, region}`. See Voiceover below. |
| `steps` | required | The list of steps below |

Most steps take either a single value or a mapping: `click: "#go"` equals `click: {selector: "#go"}`.

## Steps for both modes

| Step | Options | What it does |
| --- | --- | --- |
| `wait: 3` | | Hold for N seconds |
| `caption` | `text`, `hold` | Show a caption bar. With `hold` it disappears after N seconds, otherwise it stays until `caption: ""`. |
| `card` | `title`, `subtitle`, `hold` (2.5), `while`, `keep` | Full-screen title card. `while` is a list of steps run off camera behind the card (load the first page there). `keep: true` leaves the card up to end the video. |
| `hide` | list of steps | Run steps off camera; the time they take is cut from the video. |
| `say` | `text`, `wait`, `gap` (0.35), `voice`, `engine`, `language` | Speak a line of narration (Amazon Polly). See Voiceover below. |

## Voiceover

`say: "One sentence of narration."` voices the line with Amazon Polly and adds an audio track to the video.

- **Timing.** `say` starts the line and returns at once, so the steps after it play under the voice. The next `say` waits until the previous line has finished, plus `gap` seconds, and the video never ends before the last line does. So the usual pattern is `say`, then the actions and `highlight` it describes, then the next `say`. `wait: true` holds the picture until the line is finished.
- **Opening line.** A `say` placed before the first visual step starts 0.4 seconds into the video, over that step.
- **Voices.** `voice` is a Polly voice ID and `engine` is `generative`, `long-form`, `neural` or `standard`. US English generative voices: Matthew, Stephen, Danielle, Joanna, Ruth, Salli, Tiffany. List others with `aws polly describe-voices --engine generative --language-code en-GB`. Set them once under `voiceover`, or per line.
- **Credentials.** The standard AWS chain (environment variables, `~/.aws`), or `voiceover: {profile: name, region: us-west-2}`. The identity needs `polly:SynthesizeSpeech`. Generative voices exist only in some regions; use `engine: neural` elsewhere.
- **Pronunciation.** Polly reads unfamiliar names as words ("GPTBot" came out as "geh-pah-tah bot"). List them once under `voiceover.pronounce` and every line is respelled before it is voiced: `pronounce: {GPTBot: "G P T bot", nginx: "engine x"}`. Matching is case-sensitive and whole-word.
- **SSML.** Text that starts with `<speak>` is sent as SSML, for pauses (`<break time="400ms"/>`) and pronunciation (`<sub alias="S E O">SEO</sub>`).
- **Cache.** Clips are cached in `~/.cache/demo-recorder/voice` by text and voice, so re-recording does not call Polly again unless a line changes.
- Audio keeps playing across `hide` and `cut_wait` cuts. Narration is raised to about -16 LUFS with a single fixed gain, with no compression. A GIF has no sound.

After recording, the recorder prints when each line starts and ends. Use that to check each line is heard over the screen it describes.

## Web steps

Selectors are Playwright selectors: CSS, `text=Sign in`, `role=button[name="Save"]`. The first match is used. Elements outside the viewport are scrolled into view smoothly first.

| Step | Options | What it does |
| --- | --- | --- |
| `goto` | `url`, `until` (`load`) | Navigate. As the very first step it happens off camera, so the video opens on the loaded page. |
| `click` | `selector`, `hover_pause` (0.4), `after` (0.3) | Glide the cursor over, pause, click with a ripple |
| `hover` | `selector`, `hold` (1.0) | Glide the cursor over and rest there |
| `type` | `text`, `selector`, `clear` (true), `delay` (0.075) | Click the field, clear it, type at human speed. Without `selector`, types into whatever has focus. |
| `press: Enter` | | Press a key or chord (`Control+K`) |
| `wait_for` | `selector` or `text`, `state` (`visible`), `timeout` | Wait for an element or text |
| `scroll_to` | `selector`, `align` (`top` or `center`), `offset` (70), `duration` | Smooth-scroll so the element sits `offset` px from the top |
| `scroll` | `by` (pixels, `top`, or `bottom`), `duration` | Smooth-scroll by an amount |
| `highlight` | `selector`, `hold` (2.0), `caption` | Draw a glowing ring around an element, with an optional caption for the same duration |
| `eval` | JavaScript | Escape hatch: run script in the page (dismiss a cookie banner, for example) |

Links that open a new tab are not followed; `goto` the URL instead.

## Terminal steps

| Step | Options | What it does |
| --- | --- | --- |
| `run` | `cmd`, `show`, `hold` (2.0), `cut_wait`, `allow_fail`, `timeout` (60), `delay`, `line_delay` | Type the command, run it with bash, print its real output |
| `comment: text` | | Type a `# comment` line |
| `clear` | | Clear the screen |

- `show` changes what is typed on screen while `cmd` is what runs. Use it only for cosmetic differences such as forcing colour (`jq -C`); the output must be what the shown command would produce.
- `cut_wait: true` removes the time the command took from the video.
- A non-zero exit stops the recording unless `allow_fail: true`.
- Commands run without a TTY, so most tools drop colour: force it (`jq -C`, `ls --color=always`, `git -c color.ui=always`). ANSI colours, bold, dim and underline are rendered.
- State does not carry between `run` steps (each is its own `bash -c`), but files do; write to a file in one step and read it in the next.
