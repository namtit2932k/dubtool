# AGENT_UPDATE_2.md — Keep original audio under the dub, and burn Vietnamese subtitles into the video

This is a second update. `AGENT.md` and `AGENT_UPDATE.md` still apply (ground rules, data contracts, resumability, secret safety, review zip rule in `AGENT_UPDATE.md` 7.2, reporting format). This file changes only stage 6 (mix/mux) and stage 7 (subtitles), plus config and CLI. If it conflicts with the earlier files for those parts, this file wins.

The user listened to `sample/output/test1.mp4` and wants two changes:

1. **The original audio must stay.** Right now it is completely replaced by the Vietnamese voice. Instead, the Vietnamese voice must be laid **on top of** the original audio, and the original audio must be **lowered in volume** so the Vietnamese voice is clearly understandable.
2. **Vietnamese subtitles burned into the video**, at the bottom of the frame.

---

## 1. Do not redo the expensive work

The translation and TTS for `sample/input/test1.mp4` already exist. **Do not rerun stages 1–5** (no new Gemini calls, no new TTS calls). Reuse the latest existing job for this video (find it with `dubtool status` or in `jobs/`), and rerun only stages 6 and 7 with `--force` on that job. If a required file from stages 1–5 is missing (`fitted/*.wav`, `translated.json`), stop and tell the user rather than silently regenerating.

Write the new result to **`sample/output/test1_v2.mp4`** so the user can compare it with the existing `sample/output/test1.mp4`. Do **not** overwrite or delete the old file.

---

## 2. Stage 6 — Mix: Vietnamese on top of the original audio

### 2.1 Behavior

- Take the **original audio from `input.mp4` directly** (full mix, original channels and sample rate). Do **not** use `audio.wav` from stage 1; that file is mono 16 kHz and made for ASR only.
- Build the Vietnamese track `vi.wav` as before from `fitted/<id>.wav`, but make it match the original audio's sample rate and channel layout (convert mono to stereo if needed).
- Mix = original audio at a reduced level + Vietnamese voice at normal level.
- No Demucs in this update. (A later phase may replace the "original full mix" with a background-only track. Keep the mixing code structured so that swap is a one-line change: the mixer takes "background audio path" as input.)

### 2.2 Modes (config + CLI)

```yaml
mix:
  mode: static           # static | duck
  original_gain_db: -14  # how much to lower the original audio (static mode, and the idle level in duck mode)
  dub_gain_db: 0
  duck:
    threshold: 0.03      # sidechaincompress parameters, used only in duck mode
    ratio: 8
    attack_ms: 20
    release_ms: 400
```

CLI options on `dubtool run` and `dubtool stage`: `--mix-mode static|duck`, `--original-gain-db <number>`, `--dub-gain-db <number>`.

- **`static` (default, implement first):** the original audio is lowered by `original_gain_db` for the whole video, then mixed with the Vietnamese track. Predictable and easy to verify.
- **`duck` (implement after static works):** the original audio sits at `original_gain_db` and is additionally compressed while the Vietnamese voice is speaking, using FFmpeg `sidechaincompress` with the Vietnamese track as the sidechain. The parameters above are starting points that cannot be tuned without listening; say so in your report and do not claim they sound right.

### 2.3 Implementation requirements

- Use FFmpeg `filter_complex` for the mix. Do **not** let `amix` halve the levels: use `amix=inputs=2:normalize=0` if the installed FFmpeg supports the `normalize` option (check `ffmpeg -version` and `ffmpeg -h filter=amix`), otherwise compensate with explicit `volume` filters and `weights`.
- Add a limiter at the end (for example `alimiter`) so the mix does not clip.
- On Windows, the filter graph can become too long for the command line or contain paths that need escaping. Write the filter graph to a file and use `-filter_complex_script`, and run FFmpeg with the job directory as the working directory using relative file names.
- Keep the Vietnamese voice level unchanged by default (`dub_gain_db: 0`). Never raise the original audio above the Vietnamese voice.
- If `input.mp4` has no audio stream, skip the original audio and produce the dub-only mix, with a clear log message.

---

## 3. Stage 7 — Burn Vietnamese subtitles into the video

### 3.1 Behavior

- Produce the soft subtitle file `output.vi.srt` as before, **and** burn the same subtitles into the video at the bottom center.
- Config + CLI: `subtitles.burn: true` by default, with `--no-burn` to skip burning (then the video stream is copied as in `AGENT.md`; the earlier rule "never re-encode video" still applies when not burning).
- When burning, the video must be re-encoded. Use: `-c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p`, keep the original resolution and frame rate, `-c:a aac -b:a 192k`, `-movflags +faststart`. Make `crf` and `preset` configurable. Report the encode time.

### 3.2 Subtitle timing must match the dubbed audio

In `output.vi.srt`, each cue starts at the segment's `start`. The cue's end must be `max(end, start + actual fitted clip duration)`, but never later than the next cue's start minus 0.05 s. Reason: stretched or overflowing clips can run past the original `end`, and the text must stay visible while the voice is still speaking. Enforce a minimum cue duration of 1.0 s where the next cue allows it.

Keep the existing wrapping rule (at most 2 lines, about 42 characters per line).

### 3.3 Appearance (Vietnamese diacritics must render correctly)

- Use FFmpeg's `subtitles` filter (libass) with `force_style`: white text, black outline (`BorderStyle=1`, `Outline=2`, `Shadow=0`), `Alignment=2` (bottom center), and a bottom margin so the text is not glued to the edge.
- Make the font size depend on the video height. Get the video size with `ffprobe`, pass `original_size=<width>x<height>` to the filter so `FontSize` is in real video pixels, and start from `FontSize ≈ height / 24` and `MarginV ≈ height / 18`. Make all of these configurable (`subtitles.font`, `subtitles.size_ratio`, `subtitles.margin_ratio`).
- **The font must support Vietnamese.** The user is on Windows. Prefer a system font such as Arial or Segoe UI, found in the Windows fonts folder; point libass to a fonts directory with `fontsdir` (use a relative path, for example copy the one chosen font file into `<job dir>/fonts/` for local use and reference it there, which also avoids Windows path escaping problems with drive letters and colons). If no suitable font is found, stop with a clear message instead of rendering missing-glyph boxes.
- On Windows, escaping of the subtitle path inside the filter is a known problem. Run FFmpeg with the job directory as the working directory and refer to `output.vi.srt` by its relative name.

---

## 4. Tests

Offline, no API keys, no network.

- **Mix test with synthetic audio**: generate a 6-second "original" (a 440 Hz tone) and a "dub" (an 880 Hz tone present only from 2 s to 4 s). Run the mixer in static mode with `original_gain_db: -14`. Assert, using an FFT or FFmpeg analysis, that (a) the 440 Hz component is still present at the start (0–2 s) and its level is lower than the original by about 14 dB (tolerance ±1.5 dB), (b) both tones are present between 2 s and 4 s, (c) the dub level is not reduced, (d) the output does not clip.
- **Subtitle timing test**: cues extend to cover a clip longer than `end`, never overlap the next cue, and respect the minimum duration.
- **SRT wrapping test**: at most 2 lines and about 42 characters per line, with Vietnamese diacritics preserved (UTF-8, no mojibake).
- **Command construction test**: the FFmpeg command or filter script for the mix and for burning is built as expected (no real encode needed in the unit test).
- Existing tests still pass.

---

## 5. Run on the sample and verify

Run for the existing job (rerun only stages 6 and 7, output to `sample/output/test1_v2.mp4`). Then measure, do not assume:

- [ ] `ffprobe` reads `sample/output/test1_v2.mp4` without errors; it has one video stream (H.264) and one audio stream (AAC); resolution and frame rate equal the input's; duration differs from the input by at most 1 second.
- [ ] `volumedetect` on the output audio shows `max_volume` at or below -0.1 dB (no clipping). Report mean and max volume.
- [ ] **Original audio is present**: in a stretch of the video where no Vietnamese clip plays (use `translated.json` to find a gap of at least 1 second, or state that none exists), the output's level is clearly above silence, and it is lower than the corresponding level in the original audio by about `original_gain_db` (report both measured numbers).
- [ ] **Vietnamese voice is on top**: during a stretch where Vietnamese plays, the output is louder than in the gap measurement above, and the dub is not attenuated (report the numbers).
- [ ] **Subtitles are burned in**: extract 3 frames at the middle of 3 different subtitle cues (include at least one cue that contains characters like ắ, ữ, ề, ộ, ỵ) and 1 frame at a time with no cue, saving them as PNG in `jobs/<job_id>/check/`. Compare each against the same frame from the version without burned subtitles (use `--no-burn` output or the original video) and report that the changed pixels are concentrated in the bottom third of the frame for cue frames and absent for the no-cue frame. State plainly that you cannot judge readability or whether the diacritics look right; the user will check the PNG files and the video.
- [ ] `sample/output/test1.mp4` (the old result) still exists and is unchanged.
- [ ] Rerunning the same command without `--force` skips finished stages and does not overwrite `test1_v2.mp4`.

If something fails, do not report success. Say which check failed and what you tried.

---

## 6. Report back

Follow `AGENT.md` section 12 and `AGENT_UPDATE.md` section 7.2. Include:

1. What changed (files), and the exact commands you ran.
2. The measured numbers from section 5, in a small table (original in a gap, original in the output, dub region, max volume).
3. Encode time for the burn step, and the size of `test1_v2.mp4` compared with `test1.mp4`.
4. The font you chose and where it came from, the final `FontSize`, `MarginV`, and the paths of the PNG frames for the user to look at.
5. What you could not verify (listening quality, subtitle appearance), stated plainly.
6. The review zip path and size (produce a new one at the end of this session).

Then stop and wait for the user's feedback. Expected next adjustments: the user may ask for a different `original_gain_db`, the duck mode, a different font size or position, or Demucs for a cleaner background. Do not do these unless asked.
