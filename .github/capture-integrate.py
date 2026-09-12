"""Apply the reviewed GUI/documentation integration to the repository checkout."""
from pathlib import Path
import subprocess

p=Path('app.py');s=p.read_text(encoding='utf-8')
def replace(old,new):
    global s
    if s.count(old)!=1:raise RuntimeError(f'Unexpected app.py context: {old[:100]}')
    s=s.replace(old,new,1)
replace('    AudioMode,','    AudioMode,\n    MAX_CAPTURE_WORKERS,')
replace('        self.cpu_threads_var = StringVar(value=str(config.render.cpu_threads))',
'''        self.cpu_threads_var = StringVar(value=str(config.render.cpu_threads))
        self.capture_workers_var = StringVar(value=str(config.render.capture_workers))
        self.frame_buffer_var = StringVar(value=str(config.render.frame_buffer_mb))
        self.fast_capture_var = BooleanVar(value=config.render.fast_capture)''')
replace('        audio_tab = ttk.Frame(notebook, padding=14)',
'''        audio_tab = ttk.Frame(notebook, padding=14)
        performance_tab = ttk.Frame(notebook, padding=14)''')
replace('        notebook.add(audio_tab, text="Audio")',
'''        notebook.add(audio_tab, text="Audio")
        notebook.add(performance_tab, text="Performance")''')
replace('        self._build_audio_tab(audio_tab)',
'''        self._build_audio_tab(audio_tab)
        self._build_performance_tab(performance_tab)''')
replace('    def _build_audio_tab(self, tab: ttk.Frame) -> None:',
'''    def _build_performance_tab(self, tab: ttk.Frame) -> None:
        self._row(tab, 0, "Capture workers per export",
                  ttk.Spinbox(tab, from_=0, to=MAX_CAPTURE_WORKERS, textvariable=self.capture_workers_var),
                  "0: auto for declared-safe sources; 1: sequential. 2+ asserts independent seeking.")
        self._row(tab, 1, "PNG frame buffer (MiB)",
                  ttk.Spinbox(tab, from_=16, to=4096, textvariable=self.frame_buffer_var),
                  "Bounds buffered PNG payloads. Browser and encoder RAM are additional.")
        ttk.Checkbutton(tab, text="Faster guarded viewport screenshots",
                        variable=self.fast_capture_var).grid(row=2, column=1, columnspan=2,
                                                             sticky="w", padx=12, pady=8)
        ttk.Label(tab, text=(
            "Capture workers accelerate a SINGLE movie with separate browser instances. "
            "Start with 4 for deterministic, independently seekable animations. "
            "Each instance must produce the same frame for a timestamp after a fresh load. "
            "Random content, external state, simulations and cumulative seek hooks are not safe. "
            "Browser Clock and Realtime remain sequential. Static holds reuse one frame. "
            "Queue workers run separate movies; CPU threads on Output control FFmpeg. "
            "Use the render log to inspect effective worker counts and stage timings."
        ), wraplength=650).grid(row=3, column=0, columnspan=3, sticky="w", pady=16)

    def _build_audio_tab(self, tab: ttk.Frame) -> None:''')
replace('            config.render.cpu_threads = int(self.cpu_threads_var.get())',
'''            config.render.cpu_threads = int(self.cpu_threads_var.get())
            config.render.capture_workers = int(self.capture_workers_var.get())
            config.render.frame_buffer_mb = int(self.frame_buffer_var.get())
            config.render.fast_capture = bool(self.fast_capture_var.get())''')
p.write_text(s,encoding='utf-8')

p=Path('README.md');s=p.read_text(encoding='utf-8')
s+='''\n## Faster single-video capture\n\nJob Settings → **Performance** adds independent browser capture workers. For\na deterministic animation whose seek hook fully reconstructs each timestamp,\nstart with **4 capture workers**, keep CPU threads automatic, and compare a\nshort export with sequential mode. This is separate from queue workers, which\nrun different movies. The guarded viewport screenshot fast path is on by\ndefault, including for sequential capture. See [parallel capture](docs/PARALLEL_CAPTURE.md)\nfor the safety contract, memory budget, timings and end-to-end benchmark.\n'''
p.write_text(s,encoding='utf-8')
p=Path('CHANGELOG.md');s=p.read_text(encoding='utf-8');s=s.replace('# Changelog\n','# Changelog\n\n## Unreleased — browser capture performance\n\n- Add ordered, bounded single-export browser capture workers with independent Playwright ownership.\n- Auto parallelism requires an author declaration; explicit 2+ workers require independently seekable sources. Progressive timelines remain sequential.\n- Use viewport screenshots for geometry-guarded roots only when their live bounds match the viewport; retain the legacy fallback and an opt-out.\n- Add GUI/CLI controls, per-stage render timing, actual browser-render regressions and an end-to-end benchmark.\n',1);p.write_text(s,encoding='utf-8')
subprocess.run(['python','tools/generate_reference.py'],check=True)
