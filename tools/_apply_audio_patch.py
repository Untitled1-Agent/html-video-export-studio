"""Apply the reviewed audio patch on the isolated feature branch; removed after applying."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def edit(name, old, new):
    path = ROOT / name
    text = path.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise RuntimeError(f'{name}: expected one patch anchor, found {text.count(old)}: {old[:80]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


def section(name, start, end, value):
    path = ROOT / name
    text = path.read_text(encoding='utf-8')
    first = text.index(start)
    last = text.index(end, first + len(start))
    path.write_text(text[:first] + value.rstrip() + '\n\n' + text[last:], encoding='utf-8')


edit('models.py', 'MAX_CPU_THREADS = 256',
     'MAX_CPU_THREADS = 256\nAUDIO_BITRATES = (0, 64, 96, 128, 160, 192, 256)\n'
     'AUDIO_SAMPLE_RATES = (0, 44100, 48000)\nAUDIO_CHANNELS = (0, 1, 2)')
section('models.py', '@dataclass\nclass AudioConfig:', '@dataclass\nclass RenderConfig:', '''@dataclass
class AudioConfig:
    path: str = ""
    mode: AudioMode = AudioMode.NONE
    volume: float = 1.0
    offset_seconds: float = 0.0
    fade_in_seconds: float = 0.0
    fade_out_seconds: float = 0.0
    bitrate_kbps: int = 0  # 0: output-profile default; PCM ignores bitrate.
    sample_rate_hz: int = 0  # 0: output-profile default (48 kHz).
    channels: int = 0  # 0: output-profile default (stereo).
    normalize_loudness: bool = False

    def validate(self) -> None:
        if not isinstance(self.mode, AudioMode):
            raise ValueError("Invalid audio mode.")
        if not isinstance(self.path, str):
            raise ValueError("Audio path must be a string.")
        if self.mode != AudioMode.NONE and not self.path.strip():
            raise ValueError("Choose an audio file or disable audio.")
        if strict_float(self.volume) < 0:
            raise ValueError("Audio volume must be a finite non-negative number.")
        if not -86400 <= strict_float(self.offset_seconds) <= 86400:
            raise ValueError("Audio offset must be between -86400 and +86400 seconds.")
        for label, value in (("Audio fade-in", self.fade_in_seconds),
                             ("Audio fade-out", self.fade_out_seconds)):
            if not 0 <= strict_float(value) <= 86400:
                raise ValueError(f"{label} must be between 0 and 86400 seconds.")
        for label, value, choices in (
            ("Audio bitrate", self.bitrate_kbps, AUDIO_BITRATES),
            ("Audio sample rate", self.sample_rate_hz, AUDIO_SAMPLE_RATES),
            ("Audio channels", self.channels, AUDIO_CHANNELS),
        ):
            if strict_int(value) not in choices:
                raise ValueError(f"{label} must be one of {choices}; 0 uses the profile default.")
        strict_bool(self.normalize_loudness)
''')
edit('models.py', '        fade_out_seconds=strict_float(data.get("fade_out_seconds", base.fade_out_seconds)),',
     '        fade_out_seconds=strict_float(data.get("fade_out_seconds", base.fade_out_seconds)),\n'
     '        bitrate_kbps=strict_int(data.get("bitrate_kbps", base.bitrate_kbps)),\n'
     '        sample_rate_hz=strict_int(data.get("sample_rate_hz", base.sample_rate_hz)),\n'
     '        channels=strict_int(data.get("channels", base.channels)),\n'
     '        normalize_loudness=strict_bool(data.get("normalize_loudness", base.normalize_loudness)),')
edit('models.py', '        self.audio.validate()\n',
     '        self.audio.validate()\n'
     '        if (self.audio.mode != AudioMode.NONE\n'
     '                and OUTPUT_PROFILES[self.output_profile_key].audio_codec == "libopus"\n'
     '                and int(self.audio.sample_rate_hz) == 44100):\n'
     '            raise ValueError("WebM/Opus requires 48000 Hz; choose Profile default or 48000 Hz.")\n')
edit('media_pipeline.py', 'from typing import TYPE_CHECKING', 'from typing import TYPE_CHECKING, Iterable')
edit('media_pipeline.py', '    from models import JobConfig',
     '    from models import AudioConfig, JobConfig, SourceSpec\n    from presets import OutputProfile')
path = ROOT / 'media_pipeline.py'
path.write_text(path.read_text(encoding='utf-8').rstrip() + r'''


# Keep these aligned with the desktop picker. Inputs are decoded and re-encoded;
# file extensions never determine the audio codec of the delivery container.
AUDIO_FILE_SUFFIXES = frozenset({'.wav', '.m4a', '.mp3', '.aac', '.flac', '.ogg', '.opus'})


def audio_source_path(value: str) -> Path:
    """Resolve a user-selected local audio file without shell interpolation."""
    path = Path(value).expanduser().resolve()
    if path.suffix.lower() not in AUDIO_FILE_SUFFIXES:
        raise ValueError('Choose a WAV, M4A, MP3, AAC, FLAC, OGG, or Opus audio file.')
    if not path.is_file():
        raise ValueError(f'Audio file not found: {path}')
    return path


def find_matching_audio(source: SourceSpec, candidates: Iterable[Path]) -> Path | None:
    """Match local HTML/audio basenames, never by selection order.

    Prefer a unique sibling of the HTML when the same stem occurs in several
    folders. Otherwise ambiguous matches are errors, not arbitrary choices.
    """
    from models import SourceKind

    if source.kind != SourceKind.FILE:
        return None
    html = Path(source.value).expanduser().resolve()
    matches = sorted({Path(p).expanduser().resolve() for p in candidates
                      if Path(p).suffix.lower() in AUDIO_FILE_SUFFIXES
                      and Path(p).stem.casefold() == html.stem.casefold()
                      and Path(p).expanduser().is_file()}, key=str)
    siblings = [p for p in matches if p.parent == html.parent]
    matches = siblings or matches
    if len(matches) > 1:
        raise ValueError(f'Ambiguous audio for {html.name}: ' + ', '.join(str(p) for p in matches))
    return matches[0] if matches else None


def build_audio_filter_chain(audio: AudioConfig, output_duration: float) -> str:
    """Map a soundtrack onto the *output* timeline, including video holds.

    Positive offsets insert silence; negative offsets discard source samples.
    Reset timestamps after trimming, fade in the audible start (not the leading
    silence), and always pad/trim to the frame-rounded video duration. Fades out
    are anchored to the video end. Even an exhausted source becomes silence.
    """
    from models import strict_float

    audio.validate()
    duration = strict_float(output_duration)
    if duration <= 0:
        raise ValueError('Audio output duration must be positive.')
    offset = float(audio.offset_seconds)
    delay = max(0.0, offset)
    filters = ['asetpts=PTS-STARTPTS']
    if offset < 0:
        filters.extend([f'atrim=start={-offset:.9f}', 'asetpts=PTS-STARTPTS'])
    if audio.normalize_loudness:
        # Optional single-pass EBU R128 normalization, before user gain/fades.
        filters.append('loudnorm=I=-16:TP=-1.5:LRA=11')
    if abs(float(audio.volume) - 1.0) > 1e-9:
        filters.append(f'volume={float(audio.volume):.9f}')
    fade_in = min(float(audio.fade_in_seconds), max(0.0, duration - delay))
    if fade_in > 0:
        filters.append(f'afade=t=in:st=0:d={fade_in:.9f}')
    if delay > 0:
        filters.append(f'adelay={delay * 1000:.3f}:all=1')
    filters.extend(['apad', f'atrim=duration={duration:.9f}'])
    fade_out = min(float(audio.fade_out_seconds), duration)
    if fade_out > 0:
        filters.append(f'afade=t=out:st={duration - fade_out:.9f}:d={fade_out:.9f}')
    return ','.join(filters)


def build_audio_output_args(audio: AudioConfig, profile: OutputProfile) -> list[str]:
    """Container-safe codecs with bounded, explicit rate/channel overrides."""
    audio.validate()
    if profile.audio_codec == 'libopus' and int(audio.sample_rate_hz) == 44100:
        raise ValueError('WebM/Opus requires 48000 Hz; choose Profile default or 48000 Hz.')
    args = list(profile.audio_args)

    def set_option(flag: str, value: str) -> None:
        if flag in args:
            index = args.index(flag)
            args[index + 1] = value
        else:
            args.extend([flag, value])

    if profile.audio_codec == 'aac':
        set_option('-profile:a', 'aac_low')
    if audio.bitrate_kbps and profile.audio_codec in {'aac', 'libopus'}:
        set_option('-b:a', f'{int(audio.bitrate_kbps)}k')
    if audio.sample_rate_hz or '-ar' not in args:
        set_option('-ar', str(int(audio.sample_rate_hz) or 48000))
    if audio.channels or '-ac' not in args:
        set_option('-ac', str(int(audio.channels) or 2))
    return args


def validate_audio_stream(executable: str, value: str) -> None:
    """Fail before encoding video frames for corrupt files or containers without audio.

    FFprobe is not required: packaged installations may only bundle FFmpeg.
    Decode one frame to a bounded raw mono sample, and require actual samples.
    """
    path = audio_source_path(value)
    try:
        result = subprocess.run(
            [executable, '-hide_banner', '-loglevel', 'error', '-nostdin',
             '-i', str(path), '-map', '0:a:0', '-frames:a', '1',
             '-ac', '1', '-ar', '8000', '-f', 's16le', 'pipe:1'],
            capture_output=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f'Could not read audio file {path.name}: {exc}') from exc
    if result.returncode or not result.stdout:
        detail = result.stderr.decode('utf-8', errors='replace').strip()[-1500:]
        raise ValueError(f'Audio file has no decodable audio stream: {path.name}'
                         + (f'\n{detail}' if detail else ''))
''', encoding='utf-8')
section('renderer.py', '    def _validate_profile_and_filters(', '    def _build_ffmpeg_command(', '''    def _validate_profile_and_filters(self, job: JobConfig, width: int, height: int, filter_chain: str) -> None:
        profile = OUTPUT_PROFILES.get(job.render.output_profile_key)
        if profile is None:
            raise ExportError(f'Unknown output profile: {job.render.output_profile_key}')
        if profile.requires_even_dimensions and (width % 2 or height % 2):
            raise ExportError(f'{profile.label} requires even output dimensions; got {width}×{height}.')
        if job.capture.transparent_background and not profile.supports_alpha:
            raise ExportError('This output profile cannot preserve transparency.')
        if job.capture.transparent_background and filter_chain:
            raise ExportError('Transparent masters currently require No processing to preserve alpha exactly.')
        from media_pipeline import (select_ffmpeg, filter_names, color_pipeline,
                                    audio_source_path, build_audio_filter_chain,
                                    build_audio_output_args, validate_audio_stream)
        effective_chain = color_pipeline(filter_chain, profile.video_encoder == 'libx264rgb')
        required_filters = filter_names(effective_chain)
        required_encoders = {profile.video_encoder}
        audio = job.render.audio
        if audio.mode != AudioMode.NONE:
            try:
                audio_source_path(audio.path)
                build_audio_output_args(audio, profile)
                required_filters.update(filter_names(build_audio_filter_chain(audio, 1.0)))
            except ValueError as exc:
                raise ExportError(str(exc)) from exc
            required_encoders.add(profile.audio_codec)
            required_filters.add('aresample')
            if audio.fade_in_seconds > 0 or audio.fade_out_seconds > 0:
                required_filters.add('afade')
        self._ffmpeg_exe = select_ffmpeg(required_encoders, required_filters)
        if audio.mode != AudioMode.NONE:
            try:
                validate_audio_stream(self._ffmpeg_exe, audio.path)
            except ValueError as exc:
                raise ExportError(str(exc)) from exc
''')
section('renderer.py', "        if has_audio:\n            audio_filters: list[str] = ['asetpts=PTS-STARTPTS']", '        command.extend(["-t",', '''        if has_audio:
            from media_pipeline import build_audio_filter_chain, build_audio_output_args
            command.extend(["-af", build_audio_filter_chain(audio, output_duration)])
            command.extend(build_audio_output_args(audio, profile))
        else:
            command.append("-an")
''')
edit('presets.py', '    fresh.render.cpu_threads = job.render.cpu_threads\n',
     '    fresh.render.cpu_threads = job.render.cpu_threads\n'
     '    import copy\n'
     '    fresh.render.audio = copy.deepcopy(job.render.audio)\n')
edit('cli.py', '    parser.add_argument("--overwrite", action="store_true")', '''    from models import AUDIO_BITRATES, AUDIO_SAMPLE_RATES, AUDIO_CHANNELS
    sound = parser.add_argument_group('External soundtrack')
    inputs = sound.add_mutually_exclusive_group()
    inputs.add_argument('--audio', type=Path, metavar='FILE',
                        help='Attach one audio file to every input (WAV/M4A/MP3 and more).')
    inputs.add_argument('--audio-map', nargs=2, action='append', metavar=('HTML', 'AUDIO'),
                        help='Match a track to one input; repeat for a batch. Unmapped inputs stay silent.')
    inputs.add_argument('--audio-dir', type=Path, metavar='DIR',
                        help='Match each local HTML to a unique same-basename track in DIR.')
    sound.add_argument('--audio-mode', choices=('none', 'trim', 'loop'),
                       help='Use once and pad/trim (default with a track), loop, or disable audio.')
    sound.add_argument('--audio-volume', type=float, default=1.0, metavar='GAIN',
                       help='Linear gain: 0=mute, 1=original, 0.5=half amplitude.')
    sound.add_argument('--audio-offset', type=float, default=0.0, metavar='SECONDS',
                       help='Signed sync: + delays audio; - skips its beginning. Video length is unchanged.')
    sound.add_argument('--audio-fade-in', type=float, default=0.0, metavar='SECONDS',
                       help='Fade from the audible start, after any leading silence.')
    sound.add_argument('--audio-fade-out', type=float, default=0.0, metavar='SECONDS',
                       help='Fade ending at the video end.')
    sound.add_argument('--audio-bitrate', type=int, choices=AUDIO_BITRATES, default=0,
                       help='AAC/Opus kbps; 0=profile default. PCM masters ignore bitrate.')
    sound.add_argument('--audio-sample-rate', type=int, choices=AUDIO_SAMPLE_RATES, default=0,
                       help='Output Hz; 0=profile default. WebM/Opus requires 48000.')
    sound.add_argument('--audio-channels', type=int, choices=AUDIO_CHANNELS, default=0,
                       help='0=profile default, 1=mono, 2=stereo.')
    sound.add_argument('--audio-normalize', action='store_true',
                       help='Optional single-pass loudness normalization to -16 LUFS before gain/fades.')
    parser.add_argument("--overwrite", action="store_true")''')
edit('cli.py', 'def make_job(source: str, args: argparse.Namespace):', '''def _audio_source_key(value: str) -> str:
    import os
    return value if value.startswith(('http://', 'https://')) else os.path.normcase(str(Path(value).expanduser().resolve()))


def _configure_audio(job, source: str, args: argparse.Namespace) -> None:
    from media_pipeline import audio_source_path, find_matching_audio
    from models import AudioConfig, AudioMode

    path = args.audio
    mappings = {}
    for html, track in args.audio_map or []:
        key = _audio_source_key(html)
        if key in mappings:
            raise ValueError(f'Duplicate --audio-map for {html}.')
        mappings[key] = track
    if mappings:
        path = mappings.get(_audio_source_key(source))
    if args.audio_dir is not None:
        directory = args.audio_dir.expanduser()
        if not directory.is_dir():
            raise ValueError(f'Audio directory not found: {directory}')
        path = find_matching_audio(job.source, directory.iterdir())
        if path is None:
            raise ValueError(f'No matching audio for {job.source.display_name} in {directory}.')
    has_input_option = bool(args.audio or args.audio_map or args.audio_dir)
    has_settings = (args.audio_mode not in (None, 'none') or args.audio_volume != 1.0
                    or args.audio_offset != 0.0 or args.audio_fade_in != 0.0
                    or args.audio_fade_out != 0.0 or args.audio_bitrate != 0
                    or args.audio_sample_rate != 0 or args.audio_channels != 0
                    or args.audio_normalize)
    if not has_input_option and has_settings:
        raise ValueError('Audio settings require --audio, --audio-map, or --audio-dir.')
    job.render.audio = AudioConfig(
        path=str(audio_source_path(str(path))) if path else '',
        mode=AudioMode(args.audio_mode or 'trim') if path else AudioMode.NONE,
        volume=args.audio_volume, offset_seconds=args.audio_offset,
        fade_in_seconds=args.audio_fade_in, fade_out_seconds=args.audio_fade_out,
        bitrate_kbps=args.audio_bitrate, sample_rate_hz=args.audio_sample_rate,
        channels=args.audio_channels, normalize_loudness=args.audio_normalize,
    )
    job.render.audio.validate()


def make_job(source: str, args: argparse.Namespace):''')
edit('cli.py', '    job.render.overwrite = args.overwrite',
     '    _configure_audio(job, source, args)\n    job.render.overwrite = args.overwrite')
edit('cli.py', '    args = build_parser().parse_args(argv)\n', '''    args = build_parser().parse_args(argv)
    known_sources = {_audio_source_key(source) for source in args.sources}
    mapped_sources = set()
    for source, _track in args.audio_map or []:
        key = _audio_source_key(source)
        if key not in known_sources or key in mapped_sources:
            print(f"Invalid --audio-map: {source} is not an input or is mapped more than once.", file=sys.stderr)
            return 2
        mapped_sources.add(key)
''')
edit('app.py', '        self.geometry("820x700")\n        self.minsize(740, 620)',
     '        self.geometry("820x740")\n        self.minsize(740, 680)')
anchor = '        self.audio_fade_out_var = DoubleVar(value=config.render.audio.fade_out_seconds)'
edit('app.py', anchor, anchor + '''
        self.audio_bitrate_var = IntVar(value=config.render.audio.bitrate_kbps)
        self.audio_sample_rate_var = IntVar(value=config.render.audio.sample_rate_hz)
        self.audio_channels_var = IntVar(value=config.render.audio.channels)
        self.audio_normalize_var = BooleanVar(value=config.render.audio.normalize_loudness)
        self.audio_encoding_var = StringVar(value="")''')
section('app.py', '    def _build_audio_tab(', '    def _toggle_output(', '''    def _build_audio_tab(self, tab: ttk.Frame) -> None:
        from models import AUDIO_BITRATES, AUDIO_SAMPLE_RATES, AUDIO_CHANNELS
        self._row(tab, 0, "Audio mode", ttk.Combobox(tab, state="readonly", values=tuple(AUDIO_LABELS), textvariable=self.audio_mode_var))
        audio_row = ttk.Frame(tab)
        audio_row.columnconfigure(0, weight=1)
        ttk.Entry(audio_row, textvariable=self.audio_path_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(audio_row, text="Browse…", command=self._browse_audio).grid(row=0, column=1, padx=(6, 0))
        self._row(tab, 1, "Matched soundtrack", audio_row, "WAV / M4A / MP3 and other audio. This track belongs only to this HTML job; browser audio is not recorded.")
        self._row(tab, 2, "Volume (gain)", ttk.Spinbox(tab, from_=0.0, to=4.0, increment=0.05, textvariable=self.audio_volume_var), "0 = mute; 1 = original; 0.5 = half amplitude.")
        self._row(tab, 3, "Sync offset (sec)", ttk.Spinbox(tab, from_=-86400, to=86400, increment=0.01, textvariable=self.audio_offset_var), "+ delays audio; − skips its beginning. Relative to the output video, including holds.")
        self._row(tab, 4, "Fade in (sec)", ttk.Entry(tab, textvariable=self.audio_fade_in_var), "Starts with the audible track, after any positive delay.")
        self._row(tab, 5, "Fade out (sec)", ttk.Entry(tab, textvariable=self.audio_fade_out_var), "Ends with the video. Short tracks are padded with silence.")
        self.audio_bitrate_widget = ttk.Combobox(tab, state="readonly", values=AUDIO_BITRATES, textvariable=self.audio_bitrate_var)
        self._row(tab, 6, "Bitrate (kbps)", self.audio_bitrate_widget, "0 = profile default; 96 = voice; 192 = balanced; 256 = high quality. PCM ignores bitrate.")
        self._row(tab, 7, "Sample rate (Hz)", ttk.Combobox(tab, state="readonly", values=AUDIO_SAMPLE_RATES, textvariable=self.audio_sample_rate_var), "0 = profile default (48000). Opus requires 48000.")
        self._row(tab, 8, "Channels", ttk.Combobox(tab, state="readonly", values=AUDIO_CHANNELS, textvariable=self.audio_channels_var), "0 = profile default; 1 = mono; 2 = stereo.")
        ttk.Checkbutton(tab, text="Normalize loudness to −16 LUFS before gain/fades (optional)", variable=self.audio_normalize_var).grid(row=9, column=1, columnspan=2, sticky="w", padx=(12, 8), pady=5)
        ttk.Label(tab, textvariable=self.audio_encoding_var, wraplength=620).grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))
''')
anchor = '        self.profile_description_var.set(OUTPUT_PROFILES[key].description if key else "")'
edit('app.py', anchor, anchor + '''
        if key and hasattr(self, "audio_encoding_var"):
            profile = OUTPUT_PROFILES[key]
            codec = {"aac": "AAC-LC", "libopus": "Opus", "pcm_s24le": "24-bit PCM"}.get(profile.audio_codec, profile.audio_codec)
            self.audio_encoding_var.set(
                f"Audio codec follows the video profile: {codec}. "
                "Social delivery defaults to AAC-LC, 256 kbps, 48 kHz stereo in fast-start MP4. "
                "Use Social delivery for phone/social uploads; master profiles remain specialist formats.")
            self.audio_bitrate_widget.configure(state="disabled" if profile.audio_codec.startswith("pcm_") else "readonly")''')
anchor = '        self.processing_var.set(PROCESSING_PRESETS[fresh.render.processing.preset_key].label)'
edit('app.py', anchor, anchor + '\n        self._update_profile_description()')
anchor = '            config.render.audio.fade_out_seconds = float(self.audio_fade_out_var.get())'
edit('app.py', anchor, anchor + '''
            config.render.audio.bitrate_kbps = int(self.audio_bitrate_var.get())
            config.render.audio.sample_rate_hz = int(self.audio_sample_rate_var.get())
            config.render.audio.channels = int(self.audio_channels_var.get())
            config.render.audio.normalize_loudness = bool(self.audio_normalize_var.get())
            if config.render.audio.mode != AudioMode.NONE:
                from media_pipeline import audio_source_path
                config.render.audio.path = str(audio_source_path(config.render.audio.path))''')
anchor = '        self.filter_queue_var.trace_add("write", lambda *_: self._refresh_tree())'
edit('app.py', anchor, anchor + '''

        audio_bar = ttk.Frame(tab)
        audio_bar.pack(fill="x", pady=(0, 8))
        ttk.Button(audio_bar, text="Attach Audio…", command=self._attach_audio).pack(side="left", padx=(0, 5))
        ttk.Button(audio_bar, text="Match by Filename…", command=self._match_audio).pack(side="left", padx=(0, 5))
        ttk.Button(audio_bar, text="Remove Audio", command=self._remove_audio).pack(side="left")''')
edit('app.py', '            "source", "recipe", "dimensions", "duration", "timeline",',
     '            "source", "audio", "recipe", "dimensions", "duration", "timeline",')
edit('app.py', '            "source": "Source",', '            "source": "Source",\n            "audio": "Soundtrack",')
edit('app.py', '            "source": 260, "recipe": 145,', '            "source": 260, "audio": 230, "recipe": 145,')
edit('app.py', '[source_text, job.status.value, job.phase, job.config.recipe_key, job.output_path]',
     '[source_text, job.config.render.audio.path, job.status.value, job.phase, job.config.recipe_key, job.output_path]')
edit('app.py', '            values = (\n                source_text,', '''            audio = job.config.render.audio
            soundtrack = (f"{Path(audio.path).name} · {audio.mode.value} · {audio.offset_seconds:+.3f}s"
                          if audio.mode != AudioMode.NONE else "No audio")
            values = (
                source_text,
                soundtrack,''')
edit('app.py', '    def _edit_selected(self) -> None:', r'''    def _editable_audio_jobs(self, *, single: bool = False) -> list[QueueJob]:
        jobs = self._selected_jobs()
        if not jobs or (single and len(jobs) != 1):
            messagebox.showinfo("Select jobs", "Select exactly one HTML job to attach a soundtrack." if single else "Select one or more HTML jobs.", parent=self)
            return []
        if self.update_in_progress or any(self._is_busy(job) for job in jobs):
            messagebox.showinfo("Jobs are busy", "Finish or stop the queue/analysis before changing soundtracks.", parent=self)
            return []
        return jobs

    def _set_job_audio(self, job: QueueJob, path: Optional[Path]) -> None:
        job.config.render.audio.path = str(path) if path else ""
        if path and job.config.render.audio.mode == AudioMode.NONE:
            job.config.render.audio.mode = AudioMode.TRIM
        if path is None:
            job.config.render.audio.mode = AudioMode.NONE
        job.status = JobStatus.QUEUED
        job.progress = 0.0
        job.phase = "Queued"
        job.probe = None
        job.error = ""
        job.output_path = ""
        job.elapsed_seconds = 0.0
        job.eta_seconds = None
        self.project_dirty = True

    def _attach_audio(self) -> None:
        jobs = self._editable_audio_jobs(single=True)
        if not jobs:
            return
        path = filedialog.askopenfilename(parent=self, title=f"Soundtrack for {jobs[0].config.source.display_name}",
            filetypes=[("Audio files", "*.wav *.m4a *.mp3 *.WAV *.M4A *.MP3 *.aac *.flac *.ogg *.opus"), ("All files", "*.*")])
        if not path or self._is_busy(jobs[0]) or self.update_in_progress:
            return
        from media_pipeline import audio_source_path
        try:
            resolved = audio_source_path(path)
        except ValueError as exc:
            messagebox.showerror("Invalid audio", str(exc), parent=self)
            return
        self._set_job_audio(jobs[0], resolved)
        self._refresh_tree()
        self._update_inspector()

    def _match_audio(self) -> None:
        jobs = self._editable_audio_jobs()
        if not jobs:
            return
        paths = filedialog.askopenfilenames(parent=self, title="Match soundtracks by HTML filename",
            filetypes=[("Audio files", "*.wav *.m4a *.mp3 *.WAV *.M4A *.MP3 *.aac *.flac *.ogg *.opus"), ("All files", "*.*")])
        if not paths or any(self._is_busy(job) for job in jobs) or self.update_in_progress:
            return
        from media_pipeline import find_matching_audio
        matched = 0
        notes = []
        for job in jobs:
            if job.config.render.audio.path.strip():
                notes.append(f"Kept existing soundtrack: {job.config.source.display_name}")
                continue
            try:
                path = find_matching_audio(job.config.source, [Path(p) for p in paths])
            except ValueError as exc:
                notes.append(str(exc))
                continue
            if path is None:
                notes.append(f"No filename match: {job.config.source.display_name}")
                continue
            self._set_job_audio(job, path)
            matched += 1
        self._refresh_tree()
        self._update_inspector()
        messagebox.showinfo("Audio matching", f"Matched {matched} soundtrack(s)." + ("\n\n" + "\n".join(notes) if notes else ""), parent=self)

    def _remove_audio(self) -> None:
        jobs = self._editable_audio_jobs()
        for job in jobs:
            self._set_job_audio(job, None)
        if jobs:
            self._refresh_tree()
            self._update_inspector()

    def _edit_selected(self) -> None:''')
edit('.github/workflows/tests.yml',
     '        run: python -m unittest discover -s tests -p test_core.py -v',
     '        run: python -m unittest discover -s tests -p test_core.py -v\n'
     '      - name: Portable audio settings and pairing\n'
     '        run: python -m unittest discover -s tests -p test_audio_core.py -v')
for name, link in [('README.md', 'docs/AUDIO.md'), ('docs/CLI.md', 'AUDIO.md'), ('docs/USER_GUIDE.md', 'AUDIO.md')]:
    path = ROOT / name
    path.write_text(path.read_text(encoding='utf-8').rstrip() +
        f'\n\n## External soundtracks\n\nMatch WAV/M4A/MP3 files to individual HTML jobs, use signed audio sync, '
        f'and adjust gain, looping, fades, encoding quality, or optional normalization. '
        f'See the [soundtrack guide]({link}) for desktop controls, CLI batch mapping, timing semantics, '
        f'and social-delivery defaults.\n', encoding='utf-8')
path = ROOT / 'docs/AUDIO.md'
text = path.read_text(encoding='utf-8').replace('Before frame capture, the renderer', 'Before encoding video frames, the renderer')
path.write_text(text, encoding='utf-8')
for name in ('models.py', 'media_pipeline.py', 'renderer.py', 'presets.py', 'cli.py', 'app.py'):
    ast.parse((ROOT / name).read_text(encoding='utf-8'), filename=name)
print('Applied audio settings, rendering, CLI, desktop, documentation, and CI changes.')
