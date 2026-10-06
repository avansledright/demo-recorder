#!/usr/bin/env python3
"""Record a web or terminal product demo from a YAML/JSON storyboard.

Usage: demo-record STORYBOARD [--headed] [--keep-frames] [--no-sheet]

The browser is driven by Playwright. Frames come from the Chrome DevTools
screencast and are assembled with ffmpeg, which gives a much cleaner picture
than Playwright's built-in 1 Mbit/s video. `say` steps are narrated with
Amazon Polly and mixed in afterwards. See references/storyboard.md.
"""
import argparse
import asyncio
import base64
import hashlib
import html
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|PWD|CREDENTIAL", re.I)
MASK = "••••••••"


class StepError(Exception):
    pass


# ---------------------------------------------------------------- timeline

class Timeline:
    """Collects screencast frames with timestamps; hidden spans are cut out."""

    def __init__(self, frame_dir):
        self.dir = Path(frame_dir)
        self.frames = []  # (seconds, path)
        self.t0 = time.monotonic()
        self.cut = 0.0
        self.hidden_since = None
        self.pending = None

    def now(self):
        ref = self.hidden_since if self.hidden_since is not None else time.monotonic()
        return ref - self.t0 - self.cut

    def add(self, data):
        if self.hidden_since is not None:
            self.pending = data
            return
        self._write(data)

    def _write(self, data):
        path = self.dir / f"f{len(self.frames):06d}.jpg"
        path.write_bytes(data)
        self.frames.append((self.now(), path))

    def hide(self):
        if self.hidden_since is None:
            self.hidden_since = time.monotonic()

    def show(self):
        if self.hidden_since is None:
            return
        self.cut += time.monotonic() - self.hidden_since
        self.hidden_since = None
        if self.pending:  # whatever the screen looked like when we came back
            self._write(self.pending)
            self.pending = None

    def encode(self, out, fps, size, crf):
        if not self.frames:
            raise StepError("no frames were captured")
        end = self.now()
        # Resample to a constant frame rate: each output frame shows the newest
        # capture at that instant (the screencast can deliver 60 fps or 1 fps).
        lines, j = [], 0
        for k in range(max(1, round(end * fps))):
            while j + 1 < len(self.frames) and self.frames[j + 1][0] <= k / fps:
                j += 1
            lines += [f"file '{self.frames[j][1]}'", f"duration {1 / fps:.6f}"]
        lines.append(f"file '{self.frames[j][1]}'")
        listing = self.dir / "frames.txt"
        listing.write_text("\n".join(lines) + "\n")
        w, h = size
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-vf", f"scale={w}:{h}:flags=lanczos,format=yuv420p",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-r", str(fps), "-movflags", "+faststart", str(out),
        ]
        subprocess.run(cmd, check=True)
        return end


# ---------------------------------------------------------------- helpers

def load_env_file(path):
    env = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.removeprefix("export ").split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        env[key.strip()] = val
    return env


def one(step):
    """A step is either a bare string ('clear') or a single-key mapping."""
    if isinstance(step, str):
        return step, None
    if isinstance(step, dict) and len(step) == 1:
        return next(iter(step.items()))
    raise StepError(f"each step must be a name or a single-key mapping, got: {step!r}")


def opts(arg, main_key):
    """Normalise 'click: "#btn"' and 'click: {selector: "#btn", ...}'."""
    return dict(arg) if isinstance(arg, dict) else {main_key: arg}


ANSI = re.compile(r"\x1b\[([0-9;]*)m|\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
PALETTE = ["#6e7681", "#ff7b72", "#7ee787", "#e3b341", "#79c0ff", "#d2a8ff", "#56d4dd", "#e6edf3",
           "#8b949e", "#ffa198", "#aff5b4", "#f2cc60", "#a5d6ff", "#e2c5ff", "#b3f0ff", "#ffffff"]


def xterm_color(n):
    if n < 16:
        return PALETTE[n]
    if n < 232:
        n -= 16
        return "#%02x%02x%02x" % tuple(0 if v == 0 else 55 + v * 40 for v in (n // 36, n // 6 % 6, n % 6))
    return "#%02x%02x%02x" % ((8 + (n - 232) * 10,) * 3)


class AnsiHtml:
    """Converts SGR-coloured text to HTML one line at a time, keeping style across lines."""

    def __init__(self):
        self.fg = None
        self.flags = set()

    def _sgr(self, params):
        codes = [int(p) if p else 0 for p in params.split(";")]
        i = 0
        while i < len(codes):
            c = codes[i]
            if c == 0:
                self.fg, self.flags = None, set()
            elif c in (1, 2, 3, 4):
                self.flags.add("bdiu"[c - 1])
            elif c == 22:
                self.flags -= {"b", "d"}
            elif c in (23, 24):
                self.flags.discard("iu"[c - 23])
            elif 30 <= c <= 37:
                self.fg = PALETTE[c - 30]
            elif 90 <= c <= 97:
                self.fg = PALETTE[c - 82]
            elif c == 39:
                self.fg = None
            elif c == 38 and i + 2 < len(codes) and codes[i + 1] == 5:
                self.fg = xterm_color(codes[i + 2])
                i += 2
            elif c == 38 and i + 4 < len(codes) and codes[i + 1] == 2:
                self.fg = "#%02x%02x%02x" % tuple(codes[i + 2:i + 5])
                i += 4
            i += 1

    def _span(self, text):
        if not text:
            return ""
        text = html.escape(text)
        if self.fg is None and not self.flags:
            return text
        style = f' style="color:{self.fg}"' if self.fg else ""
        cls = f' class="{" ".join(sorted(self.flags))}"' if self.flags else ""
        return f"<span{cls}{style}>{text}</span>"

    def line(self, text):
        text = text.rsplit("\r", 1)[-1]  # keep the final state of progress-bar lines
        out, pos = [], 0
        for m in ANSI.finditer(text):
            out.append(self._span(text[pos:m.start()]))
            if m.group(1) is not None:
                self._sgr(m.group(1))
            pos = m.end()
        out.append(self._span(text[pos:]))
        return "".join(out)


# ---------------------------------------------------------------- voiceover

class Voice:
    """Narration: Amazon Polly clips, cached on disk and mixed under the video after recording."""

    LEAD = 0.4  # players clip audio that starts on the very first frame

    def __init__(self, cfg):
        self.cfg = cfg
        self.client = None
        self.cache = Path(os.environ.get("DEMO_RECORDER_HOME") or Path.home() / ".cache/demo-recorder") / "voice"
        self.clips = {}   # request hash -> (path, seconds)
        self.placed = []  # (start second, path, seconds, text)
        self.end = 0.0    # timeline second at which the narration so far finishes

    def request(self, o):
        text = " ".join(str(o.get("text") or "").split())
        if not text:
            raise StepError("say needs some text")
        # Longest first, so "GPTBot" is respelled before a shorter entry such as "GPT" can touch it.
        for word, spoken in sorted((self.cfg.get("pronounce") or {}).items(), key=lambda kv: -len(str(kv[0]))):
            text = re.sub(rf"(?<!\w){re.escape(str(word))}(?!\w)", lambda _m: str(spoken), text)
        req = {"Text": text, "TextType": "ssml" if text.startswith("<speak") else "text", "OutputFormat": "mp3",
               "VoiceId": str(o.get("voice") or self.cfg.get("voice") or "Matthew"),
               "Engine": str(o.get("engine") or self.cfg.get("engine") or "generative")}
        if o.get("language") or self.cfg.get("language"):
            req["LanguageCode"] = str(o.get("language") or self.cfg["language"])
        return req

    def clip(self, o):
        req = self.request(o)
        key = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()[:24]
        if key not in self.clips:
            path = self.cache / f"{key}.mp3"
            if not path.exists():  # re-recording the same line costs nothing
                self.synthesize(req, path)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True)
            self.clips[key] = (path, float(probe.stdout))
        return self.clips[key]

    def synthesize(self, req, path):
        try:
            import boto3
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError:
            raise StepError("voiceover needs boto3: run scripts/demo-record, which installs it")
        try:
            if self.client is None:
                self.client = boto3.Session(profile_name=self.cfg.get("profile"),
                                            region_name=self.cfg.get("region")).client("polly")
            audio = self.client.synthesize_speech(**req)["AudioStream"].read()
        except (BotoCoreError, ClientError) as exc:
            raise StepError(f"Amazon Polly could not voice {req['Text'][:60]!r} "
                            f"({req['Engine']}/{req['VoiceId']}): {exc}")
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_bytes(audio)
        part.replace(path)

    def prepare(self, steps):
        """Voice every line before the browser starts, so a credentials problem fails fast."""
        for step in steps or []:
            name, arg = one(step)
            if name == "say":
                self.clip(opts(arg, "text"))
            elif name == "hide":
                self.prepare(arg)
            elif name == "card" and isinstance(arg, dict):
                self.prepare(arg.get("while"))

    def place(self, start, o):
        path, seconds = self.clip(o)
        self.placed.append((start, path, seconds, self.request(o)["Text"]))
        self.end = start + seconds

    def mux(self, video, seconds):
        def mixdown(first, tail):
            chains = [f"[{first + n}:a]aresample=48000,adelay={round(c[0] * 1000)}:all=1[a{n}]"
                      for n, c in enumerate(self.placed)]
            return (";".join(chains) + ";" + "".join(f"[a{n}]" for n in range(len(self.placed)))
                    + f"amix=inputs={len(self.placed)}:normalize=0:dropout_transition=0,"
                    + f"aformat=channel_layouts=stereo,{tail}[a]")

        inputs = [x for c in self.placed for x in ("-i", str(c[1]))]
        # Measure, then apply one fixed gain to reach -16 LUFS (web video) without clipping.
        # A fixed gain leaves the voice as Polly made it; one-pass loudnorm compresses it.
        probe = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *inputs, "-filter_complex",
                                mixdown(0, "loudnorm=print_format=json"), "-map", "[a]", "-f", "null", "-"],
                               check=True, capture_output=True, text=True).stderr
        stats = json.loads(probe[probe.rindex("{"):probe.rindex("}") + 1])
        gain = min(-16.0 - float(stats["input_i"]), -1.5 - float(stats["input_tp"]))
        mixed = video.with_name(f"{video.stem}.voice{video.suffix}")
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), *inputs,
                            "-filter_complex", mixdown(1, f"volume={gain:.2f}dB,apad"),
                            "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                            "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-t", f"{seconds:.3f}",
                            "-movflags", "+faststart", str(mixed)], check=True)
            mixed.replace(video)
        finally:
            mixed.unlink(missing_ok=True)

    def report(self):
        def clock(t):
            return f"{int(t // 60)}:{t % 60:04.1f}"

        print(f"voice  {len(self.placed)} lines, {self.end - self.placed[0][0]:.1f}s from first word to last")
        for start, _, seconds, text in self.placed:
            print(f"       {clock(start)}-{clock(start + seconds)}  {text[:70]}")


# ---------------------------------------------------------------- recorder

class Recorder:
    def __init__(self, board, base, args):
        self.board = board
        self.base = base
        self.args = args
        self.kind = board.get("type", "web")
        if self.kind not in ("web", "terminal"):
            raise StepError("type must be 'web' or 'terminal'")
        vp = board.get("viewport", {})
        self.w, self.h = int(vp.get("width", 1280)), int(vp.get("height", 720))
        self.scale = float(board.get("scale", 1.5))
        self.rng = random.Random(board.get("seed", 7))
        self.mouse = (self.w * 0.74, self.h * 0.8)
        self.overlay = {"caption": "", "card": None, "showCursor": self.kind == "web"}
        self.tmp_cwd = None
        self.voice = Voice(board.get("voiceover") or {})

        self.env = dict(os.environ)
        file_env = {}
        if board.get("env_file"):
            file_env = load_env_file((base / board["env_file"]).resolve())
            self.env.update(file_env)
        names = {k for k in file_env if SECRET_NAME.search(k)} | set(board.get("redact", []))
        self.secrets = sorted({self.env[n] for n in names if len(self.env.get(n, "")) >= 6}, key=len, reverse=True)

    # -- shared plumbing

    def scrub(self, text):
        for s in self.secrets:
            text = text.replace(s, MASK)
        return text

    def expand(self, text):
        return re.sub(r"\$\{(\w+)\}", lambda m: self.env.get(m.group(1), m.group(0)), str(text))

    async def sync(self):
        state = dict(self.overlay, x=self.mouse[0], y=self.mouse[1])
        try:
            await self.page.evaluate("s => window.__demo && window.__demo.sync(s)", state)
        except Exception:
            pass  # mid-navigation; the next step syncs again

    async def run(self):
        out = (self.base / self.board.get("output", f"out/{self.args.storyboard.stem}.mp4")).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        frame_dir = Path(tempfile.mkdtemp(prefix="demo-frames-"))
        self.timeline = Timeline(frame_dir)
        steps = self.board.get("steps") or []
        try:
            self.voice.prepare(steps)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=not self.args.headed)
                ctx = await browser.new_context(
                    viewport={"width": self.w, "height": self.h}, device_scale_factor=self.scale,
                    bypass_csp=True, color_scheme=self.board.get("color_scheme", "dark"),
                    locale=self.board.get("locale", "en-US"),
                )
                await ctx.add_init_script(path=str(HERE / "overlay.js"))
                self.page = await ctx.new_page()
                self.page.set_default_timeout(float(self.board.get("timeout", 20)) * 1000)

                self.timeline.t0 = time.monotonic()  # the clock starts now, not while the browser launched
                self.timeline.hide()  # nothing is on screen yet
                if self.kind == "terminal":
                    await self.page.goto((HERE / "terminal.html").as_uri())
                    term = self.board.get("terminal", {})
                    await self.page.evaluate("o => term.setup(o)", term)
                    self.prompt = term.get("prompt", "❯ ")
                else:
                    await self.page.set_content('<body style="margin:0;background:#0a0a12"></body>')
                await self.start_screencast()
                await self.sync()

                i, started = 0, False
                try:
                    for i, step in enumerate(steps, 1):
                        name, arg = one(step)
                        first = not started and name != "say"  # narration ahead of the first picture opens with it
                        if first and not (self.kind == "web" and name in ("goto", "hide")):
                            self.timeline.show()
                        await self.step(name, arg)
                        if first:
                            self.timeline.show()
                            started = True
                        await self.sync()
                    if self.kind == "terminal":
                        await self.page.evaluate("p => term.prompt(p)", self.prompt)
                    await self.voice_wait()
                    await asyncio.sleep(float(self.board.get("tail", 1.0)))
                except Exception as exc:
                    shot = out.with_suffix(".error.png")
                    try:
                        await self.page.screenshot(path=str(shot))
                    except Exception:
                        shot = None
                    raise StepError(f"step {i} ({json.dumps(steps[i - 1], default=str)[:160]}) failed: "
                                    f"{self.scrub(str(exc)).splitlines()[0]}"
                                    + (f"\n  screenshot of the failure: {shot}" if shot else "")) from exc
                finally:
                    self.timeline.show()
                    await browser.close()

            fps = int(self.board.get("fps", 30))
            size = (round(self.w * self.scale / 2) * 2, round(self.h * self.scale / 2) * 2)
            seconds = self.timeline.encode(out, fps, size, int(self.board.get("crf", 18)))
            if self.voice.placed:
                self.voice.mux(out, seconds)
            print(f"video  {out}  ({seconds:.1f}s, {size[0]}x{size[1]}, {out.stat().st_size / 1e6:.1f} MB)")
            if self.voice.placed:
                self.voice.report()
            if self.board.get("gif"):
                gif = out.with_suffix(".gif")
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-vf",
                                "fps=12,scale=960:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
                                "[b][p]paletteuse=dither=bayer:bayer_scale=4", str(gif)], check=True)
                print(f"gif    {gif}  ({gif.stat().st_size / 1e6:.1f} MB)")
            if not self.args.no_sheet:
                sheet = out.with_suffix(".sheet.jpg")
                every = max(seconds / 12, 0.5)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out), "-vf",
                                f"fps=1/{every:.3f},scale=640:-2,tile=4x3:padding=6:color=0x222222",
                                "-frames:v", "1", "-update", "1", str(sheet)], check=True)
                print(f"sheet  {sheet}  (12 evenly spaced frames, for a quick visual check)")
        finally:
            if self.tmp_cwd:
                shutil.rmtree(self.tmp_cwd, ignore_errors=True)
            if self.args.keep_frames:
                print(f"frames {frame_dir}")
            else:
                shutil.rmtree(frame_dir, ignore_errors=True)

    async def start_screencast(self):
        self.cdp = await self.page.context.new_cdp_session(self.page)

        def on_frame(ev):
            self.timeline.add(base64.b64decode(ev["data"]))
            asyncio.ensure_future(self._ack(ev["sessionId"]))

        self.cdp.on("Page.screencastFrame", on_frame)
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "everyNthFrame": 1})

    async def _ack(self, session_id):
        try:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception:
            pass

    # -- steps available in both modes

    async def step(self, name, arg):
        fn = getattr(self, f"{self.kind}_{name}", None) or getattr(self, f"do_{name}", None)
        if fn is None:
            raise StepError(f"unknown step '{name}' for a {self.kind} recording")
        await fn(arg)

    async def do_wait(self, arg):
        await asyncio.sleep(float(arg))

    async def do_caption(self, arg):
        o = opts(arg, "text")
        self.overlay["caption"] = o.get("text") or ""
        await self.sync()
        if o.get("hold"):
            await asyncio.sleep(float(o["hold"]))
            self.overlay["caption"] = ""
            await self.sync()
            await asyncio.sleep(0.35)

    async def do_card(self, arg):
        o = opts(arg, "title")
        self.overlay["card"] = {"title": o.get("title", ""), "subtitle": o.get("subtitle", "")}
        await self.sync()
        await asyncio.sleep(0.5)
        if o.get("while"):  # e.g. load the first page behind the card
            self.timeline.hide()
            for sub in o["while"]:
                await self.step(*one(sub))
                await self.sync()
            await asyncio.sleep(0.6)
            self.timeline.show()
        await asyncio.sleep(float(o.get("hold", 2.5)))
        if o.get("keep"):  # closing card: stay up until the video ends
            return
        self.overlay["card"] = None
        await self.sync()
        await asyncio.sleep(0.6)

    async def voice_wait(self, extra=0.0):
        """Sleep until the narration so far has finished. Off camera the clock is stopped, so nothing to wait for."""
        if self.voice.placed and self.timeline.hidden_since is None:
            await asyncio.sleep(max(0.0, self.voice.end + extra - self.timeline.now()))

    async def do_say(self, arg):
        """Narrate. Returns at once so the next steps play under the voice; a new line waits for the last one."""
        o = opts(arg, "text")
        ready = self.voice.end + float(o.get("gap", 0.35)) if self.voice.placed else Voice.LEAD
        if self.timeline.hidden_since is None:
            await asyncio.sleep(max(0.0, ready - self.timeline.now()))
        self.voice.place(max(ready, self.timeline.now()), o)
        if o.get("wait"):
            await self.voice_wait()

    async def do_hide(self, arg):
        """Run steps off camera: logins, slow loads, anything not worth showing."""
        self.timeline.hide()
        for sub in arg or []:
            await self.step(*one(sub))
            await self.sync()
        await asyncio.sleep(0.4)
        self.timeline.show()

    # -- web steps

    async def locate(self, selector, center=False):
        loc = self.page.locator(self.expand(selector)).first
        await loc.wait_for(state="visible")
        box = await loc.bounding_box()
        mid = box["y"] + box["height"] / 2
        if center or mid < 90 or mid > self.h - 90:
            await self.scroll_by(mid - self.h / 2)
            box = await loc.bounding_box()
        return loc, box

    async def scroll_by(self, dy, duration=None):
        if abs(dy) < 2:
            return
        if duration is None:
            duration = min(2.6, max(0.6, abs(dy) / 900))
        y = await self.page.evaluate("window.scrollY")
        await self.page.evaluate("([y, ms]) => window.__demo.scrollTo(y, ms)", [y + dy, duration * 1000])
        await asyncio.sleep(0.15)

    async def move_to(self, x, y):
        x0, y0 = self.mouse
        dist = math.hypot(x - x0, y - y0)
        if dist < 2:
            return
        n = max(8, int(min(1.1, 0.3 + dist / 1700) * 60))
        bend = dist * 0.09 * self.rng.choice((-1, 1))  # slight arc, like a wrist
        cx, cy = (x0 + x) / 2 - (y - y0) / dist * bend, (y0 + y) / 2 + (x - x0) / dist * bend
        for i in range(1, n + 1):
            t = i / n
            e = 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2
            px = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * cx + e * e * x
            py = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * cy + e * e * y
            await self.page.mouse.move(px, py)
            await asyncio.sleep(1 / 60)
        self.mouse = (x, y)

    async def point_at(self, selector):
        loc, box = await self.locate(selector)
        await self.move_to(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        return loc

    async def web_goto(self, arg):
        o = opts(arg, "url")
        await self.page.goto(self.expand(o["url"]), wait_until=o.get("until", "load"))
        await self.sync()
        await asyncio.sleep(0.4)

    async def web_wait_for(self, arg):
        o = opts(arg, "selector")
        timeout = float(o["timeout"]) * 1000 if "timeout" in o else None
        if "text" in o:
            await self.page.get_by_text(self.expand(o["text"])).first.wait_for(state="visible", timeout=timeout)
        else:
            await self.page.locator(self.expand(o["selector"])).first.wait_for(
                state=o.get("state", "visible"), timeout=timeout)

    async def web_hover(self, arg):
        o = opts(arg, "selector")
        await self.point_at(o["selector"])
        await asyncio.sleep(float(o.get("hold", 1.0)))

    async def web_click(self, arg):
        o = opts(arg, "selector")
        await self.point_at(o["selector"])
        await asyncio.sleep(float(o.get("hover_pause", 0.4)))
        await self.page.mouse.down()
        await asyncio.sleep(0.07)
        await self.page.mouse.up()
        await asyncio.sleep(float(o.get("after", 0.3)))

    async def web_type(self, arg):
        o = dict(arg)
        if "selector" in o:
            await self.web_click({"selector": o["selector"], "after": 0.2})
            if o.get("clear", True):
                await self.page.keyboard.press("ControlOrMeta+a")
                await self.page.keyboard.press("Delete")
                await asyncio.sleep(0.25)
        delay = float(o.get("delay", 0.075))
        for ch in self.expand(o["text"]):
            await self.page.keyboard.type(ch)
            await asyncio.sleep(delay * self.rng.uniform(0.6, 1.5))
        await asyncio.sleep(0.3)

    async def web_press(self, arg):
        await self.page.keyboard.press(str(arg))
        await asyncio.sleep(0.3)

    async def web_scroll(self, arg):
        o = opts(arg, "by")
        by = o["by"]
        if by in ("top", "bottom"):
            y = await self.page.evaluate("window.scrollY")
            full = await self.page.evaluate("document.scrollingElement.scrollHeight")
            by = -y if by == "top" else full - self.h - y
        await self.scroll_by(float(by), o.get("duration"))

    async def web_scroll_to(self, arg):
        o = opts(arg, "selector")
        loc = self.page.locator(self.expand(o["selector"])).first
        await loc.wait_for(state="attached")
        box = await loc.bounding_box()
        if box is None:
            raise StepError(f"{o['selector']} is not rendered, cannot scroll to it")
        align = o.get("align", "top")
        target = box["y"] - float(o.get("offset", 70)) if align == "top" else box["y"] + box["height"] / 2 - self.h / 2
        await self.scroll_by(target, o.get("duration"))

    async def web_highlight(self, arg):
        o = opts(arg, "selector")
        _, box = await self.locate(o["selector"])
        if o.get("caption"):
            self.overlay["caption"] = o["caption"]
            await self.sync()
        await self.page.evaluate("b => window.__demo.ring(b)", box)
        await asyncio.sleep(float(o.get("hold", 2.0)))
        await self.page.evaluate("window.__demo.ring(null)")
        if o.get("caption"):
            self.overlay["caption"] = ""
            await self.sync()
        await asyncio.sleep(0.35)

    async def web_eval(self, arg):
        await self.page.evaluate(str(arg))

    # -- terminal steps

    def workdir(self):
        """Where commands run. 'cwd: tmp' gives a scratch directory that is deleted afterwards."""
        if self.board.get("cwd") != "tmp":
            return (self.base / self.board.get("cwd", ".")).resolve()
        if self.tmp_cwd is None:
            self.tmp_cwd = Path(tempfile.mkdtemp(prefix="demo-cwd-"))
        return self.tmp_cwd

    async def type_line(self, text, delay):
        await self.page.evaluate("p => term.prompt(p)", self.prompt)
        await asyncio.sleep(0.5)
        for ch in self.scrub(text):
            await self.page.evaluate("c => term.key(c)", ch)
            await asyncio.sleep(delay * self.rng.uniform(0.5, 1.5) * (2.2 if ch == " " else 1))
        await asyncio.sleep(0.45)
        await self.page.evaluate("term.enter()")

    async def terminal_comment(self, arg):
        text = str(arg)
        await self.type_line(text if text.startswith("#") else f"# {text}", 0.04)
        await asyncio.sleep(0.4)

    async def terminal_clear(self, _arg):
        await self.page.evaluate("term.clear()")

    async def terminal_run(self, arg):
        o = opts(arg, "cmd")
        cmd = str(o["cmd"]).strip()
        await self.type_line(str(o.get("show", cmd)).strip(), float(o.get("delay", 0.035)))

        if o.get("cut_wait"):
            self.timeline.hide()
        cwd = self.workdir()
        env = dict(self.env, TERM="xterm-256color", CLICOLOR_FORCE="1", FORCE_COLOR="1")
        proc = await asyncio.create_subprocess_exec(
            "bash", "-o", "pipefail", "-c", cmd, cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), float(o.get("timeout", 60)))
        except asyncio.TimeoutError:
            proc.kill()
            raise StepError(f"command timed out: {cmd[:80]}")
        finally:
            self.timeline.show()
        text = self.scrub(raw.decode("utf-8", "replace")).rstrip("\n")
        if proc.returncode != 0 and not o.get("allow_fail"):
            raise StepError(f"command exited {proc.returncode}: {text[-300:]}")

        lines = text.split("\n") if text else []
        conv = AnsiHtml()
        per_line = min(float(o.get("line_delay", 0.03)), 2.5 / max(len(lines), 1))
        for line in lines:
            await self.page.evaluate("h => term.out(h)", conv.line(line))
            await asyncio.sleep(per_line)
        await asyncio.sleep(float(o.get("hold", 2.0)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("storyboard", type=Path)
    ap.add_argument("--headed", action="store_true", help="show the browser window while recording")
    ap.add_argument("--keep-frames", action="store_true", help="keep the captured JPEG frames")
    ap.add_argument("--no-sheet", action="store_true", help="skip the contact-sheet image")
    args = ap.parse_args()
    try:
        board = yaml.safe_load(args.storyboard.read_text())
        asyncio.run(Recorder(board, args.storyboard.resolve().parent, args).run())
    except (StepError, subprocess.CalledProcessError, OSError, yaml.YAMLError) as exc:
        sys.exit(f"demo-record: {exc}")


if __name__ == "__main__":
    main()
