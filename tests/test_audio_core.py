"""Portable audio settings, pairing, persistence, CLI, and command regressions."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import build_parser, make_job, main
from media_pipeline import (audio_source_path, build_audio_filter_chain,
                            build_audio_output_args, find_matching_audio,
                            snapshot_for_export)
from models import (AudioConfig, AudioMode, SourceKind, SourceSpec, QueueJob,
                    audio_from_dict, dataclass_to_dict)
from presets import OUTPUT_PROFILES, RECIPES, apply_recipe
from project_io import ProjectDocument, save_project, load_project
from renderer import HtmlVideoRenderer


class AudioCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.html = self.root / 'Example.html'
        self.html.write_text('<html></html>', encoding='utf-8')
        self.wav = self.root / 'example.WAV'
        self.wav.write_bytes(b'pairing-only fixture; decoding is tested separately')
        self.source = SourceSpec(str(self.html))

    def audio(self, **kwargs):
        return AudioConfig(path=str(self.wav), mode=AudioMode.TRIM, **kwargs)

    def test_signed_offsets_and_strict_finite_validation(self):
        for offset in (-86400, -1.25, 0, 0.125, 86400):
            with self.subTest(offset=offset):
                self.audio(offset_seconds=offset).validate()
        for field in ('volume', 'offset_seconds', 'fade_in_seconds', 'fade_out_seconds'):
            for value in (True, False, float('nan'), float('inf'), -float('inf')):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.audio(**{field: value}).validate()
        for field in ('volume', 'fade_in_seconds', 'fade_out_seconds'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.audio(**{field: -0.01}).validate()
        for value in (-86400.01, 86400.01):
            with self.assertRaises(ValueError):
                self.audio(offset_seconds=value).validate()

    def test_encoding_options_reject_invalid_values(self):
        for field, values in {'bitrate_kbps': (True, 1.5, -1, 320),
                              'sample_rate_hz': (False, 44100.5, 96000),
                              'channels': (True, -1, 3, 5.1),
                              'normalize_loudness': ('true', 1, None)}.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.audio(**{field: value}).validate()
        with self.assertRaises(ValueError):
            AudioConfig(path='track.wav', mode='trim').validate()
        with self.assertRaises(ValueError):
            AudioConfig(mode=AudioMode.TRIM).validate()
        AudioConfig().validate()

    def test_legacy_defaults_and_all_fields_round_trip(self):
        self.assertEqual(audio_from_dict({}), AudioConfig())
        legacy = audio_from_dict({'path': 'song.mp3', 'mode': 'trim', 'volume': 0.8})
        self.assertEqual((legacy.bitrate_kbps, legacy.sample_rate_hz, legacy.channels), (0, 0, 0))
        audio = self.audio(offset_seconds=-0.35, fade_in_seconds=0.2, fade_out_seconds=0.4,
                           volume=0.6, bitrate_kbps=192, sample_rate_hz=44100,
                           channels=1, normalize_loudness=True)
        self.assertEqual(audio_from_dict(dataclass_to_dict(audio)), audio)
        with self.assertRaises(ValueError):
            audio_from_dict({'channels': 1.5})
        with self.assertRaises(ValueError):
            audio_from_dict({'normalize_loudness': 'false'})

    def test_local_audio_paths_and_supported_extensions(self):
        self.assertEqual(audio_source_path(str(self.wav)), self.wav.resolve())
        for suffix in ('.wav', '.m4a', '.mp3', '.AAC', '.flac', '.ogg', '.opus'):
            path = self.root / ('valid' + suffix)
            path.touch()
            self.assertEqual(audio_source_path(str(path)), path)
        for path in (self.html, self.root / 'missing.wav', self.root):
            with self.subTest(path=path), self.assertRaises(ValueError):
                audio_source_path(str(path))

    def test_pairing_is_case_insensitive_not_positional(self):
        other = self.root / 'other.mp3'
        other.touch()
        self.assertEqual(find_matching_audio(self.source, [other, self.wav, self.wav]), self.wav)
        self.assertIsNone(find_matching_audio(SourceSpec(str(self.root / 'unknown.html')), [self.wav]))
        self.assertIsNone(find_matching_audio(SourceSpec('https://example.com/Example.html', SourceKind.URL), [self.wav]))

    def test_pairing_never_guesses_ambiguous_tracks(self):
        duplicate = self.root / 'EXAMPLE.mp3'
        duplicate.touch()
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            find_matching_audio(self.source, [self.wav, duplicate])
        other_folder = self.root / 'elsewhere'
        other_folder.mkdir()
        distant = other_folder / 'example.mp3'
        distant.touch()
        self.assertEqual(find_matching_audio(self.source, [self.wav, distant]), self.wav)

    def test_negative_offset_resets_timestamps_before_padding(self):
        chain = build_audio_filter_chain(self.audio(offset_seconds=-0.25), 1.2)
        self.assertIn('atrim=start=0.250000000,asetpts=PTS-STARTPTS', chain)
        self.assertNotIn('adelay', chain)
        self.assertTrue(chain.endswith('apad,atrim=duration=1.200000000'))

    def test_positive_delay_preserves_fade_in_and_clamps_long_fades(self):
        chain = build_audio_filter_chain(self.audio(offset_seconds=0.25, fade_in_seconds=10,
                                                   fade_out_seconds=10), 1.0)
        self.assertIn('afade=t=in:st=0:d=0.750000000,adelay=250.000:all=1', chain)
        self.assertTrue(chain.endswith('afade=t=out:st=0.000000000:d=1.000000000'))
        for value in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(duration=value), self.assertRaises(ValueError):
                build_audio_filter_chain(self.audio(), value)

    def test_normalization_precedes_user_gain_and_fades(self):
        chain = build_audio_filter_chain(self.audio(normalize_loudness=True, volume=0.5,
                                                   fade_in_seconds=0.1), 1.0)
        self.assertLess(chain.index('loudnorm='), chain.index('volume='))
        self.assertLess(chain.index('volume='), chain.index('afade='))

    def test_audio_codecs_follow_container_and_social_defaults(self):
        for profile in OUTPUT_PROFILES.values():
            with self.subTest(profile=profile.key):
                args = build_audio_output_args(self.audio(), profile)
                self.assertEqual(args[args.index('-c:a') + 1], profile.audio_codec)
                self.assertEqual(args[args.index('-ar') + 1], '48000')
                self.assertEqual(args[args.index('-ac') + 1], '2')
                if profile.audio_codec == 'aac':
                    self.assertEqual(args[args.index('-profile:a') + 1], 'aac_low')
        args = build_audio_output_args(self.audio(), OUTPUT_PROFILES['h264_420_mp4'])
        self.assertEqual(args[args.index('-b:a') + 1], '256k')

    def test_encoding_overrides_and_pcm_bitrate_handling(self):
        audio = self.audio(bitrate_kbps=128, sample_rate_hz=44100, channels=1)
        args = build_audio_output_args(audio, OUTPUT_PROFILES['h264_420_mp4'])
        for option, value in (('-b:a', '128k'), ('-ar', '44100'), ('-ac', '1')):
            self.assertEqual(args.count(option), 1)
            self.assertEqual(args[args.index(option) + 1], value)
        with self.assertRaisesRegex(ValueError, 'Opus'):
            build_audio_output_args(audio, OUTPUT_PROFILES['vp9_webm'])
        pcm = build_audio_output_args(audio, OUTPUT_PROFILES['prores_hq_mov'])
        self.assertNotIn('-b:a', pcm)

    def test_commands_map_only_the_selected_audio_stream(self):
        renderer = HtmlVideoRenderer.__new__(HtmlVideoRenderer)
        renderer._ffmpeg_exe = 'ffmpeg'
        renderer.log_callback = None
        job = RECIPES['social_delivery'].create_job(str(self.html))
        job.render.cpu_threads = 2
        for mode in (AudioMode.NONE, AudioMode.TRIM, AudioMode.LOOP):
            with self.subTest(mode=mode):
                job.render.audio = self.audio(offset_seconds=-0.125)
                job.render.audio.mode = mode
                command = renderer._build_ffmpeg_command(job, self.root / 'out.mp4', 1.0, '')
                self.assertEqual(command[command.index('-map') + 1], '0:v:0')
                self.assertNotIn('-shortest', command)
                self.assertIn('+faststart', command)
                if mode == AudioMode.NONE:
                    self.assertIn('-an', command)
                    self.assertNotIn('1:a:0', command)
                    self.assertNotIn('-af', command)
                else:
                    self.assertIn('1:a:0', command)
                    self.assertIn('-af', command)
                    self.assertEqual('-stream_loop' in command, mode == AudioMode.LOOP)

    def test_capability_preflight_includes_audio_processing(self):
        renderer = HtmlVideoRenderer.__new__(HtmlVideoRenderer)
        job = RECIPES['social_delivery'].create_job(str(self.html))
        job.render.audio = self.audio(offset_seconds=-0.2, normalize_loudness=True,
                                      fade_in_seconds=0.1, volume=0.5)
        with patch('media_pipeline.select_ffmpeg', return_value='ffmpeg') as choose, \
             patch('media_pipeline.validate_audio_stream') as validate:
            renderer._validate_profile_and_filters(job, 64, 96, '')
        encoders, filters = choose.call_args.args
        self.assertTrue({'aac', 'libx264'} <= encoders)
        self.assertTrue({'atrim', 'apad', 'asetpts', 'afade', 'volume', 'loudnorm', 'aresample'} <= filters)
        validate.assert_called_once_with('ffmpeg', str(self.wav))

    def test_cli_manual_maps_common_audio_and_filename_matching(self):
        parser = build_parser()
        common = parser.parse_args([str(self.html), '--audio', str(self.wav), '--audio-offset', '-0.15',
                                    '--audio-mode', 'loop', '--audio-bitrate', '192', '--audio-normalize'])
        job = make_job(str(self.html), common)
        self.assertEqual(job.render.audio.path, str(self.wav))
        self.assertEqual(job.render.audio.offset_seconds, -0.15)
        self.assertEqual(job.render.audio.mode, AudioMode.LOOP)
        self.assertTrue(job.render.audio.normalize_loudness)
        directory = parser.parse_args([str(self.html), '--audio-dir', str(self.root)])
        self.assertEqual(make_job(str(self.html), directory).render.audio.path, str(self.wav))
        mapped = parser.parse_args([str(self.html), 'other.html', '--audio-map', str(self.html), str(self.wav)])
        self.assertEqual(make_job(str(self.html), mapped).render.audio.path, str(self.wav))
        self.assertEqual(make_job('other.html', mapped).render.audio.mode, AudioMode.NONE)

    def test_cli_invalid_or_missing_pairings_are_actionable(self):
        parser = build_parser()
        for flags in (['--audio-offset', '0.2'], ['--audio-mode', 'loop'],
                      ['--audio', str(self.root / 'missing.mp3')],
                      ['--audio-dir', str(self.root / 'missing')],
                      ['--audio-map', str(self.html), str(self.wav), '--audio-map', str(self.html), str(self.wav)]):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                make_job(str(self.html), parser.parse_args([str(self.html), *flags]))
        with patch('cli.HtmlVideoRenderer') as renderer, patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(main([str(self.html), '--audio-map', 'typo.html', str(self.wav)]), 2)
            renderer.assert_not_called()

    def test_project_persists_pairing_and_audio_settings(self):
        config = RECIPES['social_delivery'].create_job(str(self.html))
        config.render.audio = self.audio(offset_seconds=-0.21, volume=0.5, bitrate_kbps=192,
                                         sample_rate_hz=44100, channels=1, normalize_loudness=True)
        path = self.root / 'saved.json'
        save_project(ProjectDocument([QueueJob('one', config)]), path)
        restored = load_project(path).jobs[0].config
        self.assertEqual(restored.render.audio, config.render.audio)
        payload = json.loads(path.read_text(encoding='utf-8'))
        payload['jobs'][0]['source']['value'] = self.html.name
        payload['jobs'][0]['render']['audio'] = {'path': self.wav.name, 'mode': 'trim', 'offset_seconds': -0.1}
        path.write_text(json.dumps(payload), encoding='utf-8')
        restored = load_project(path).jobs[0].config
        self.assertEqual(restored.render.audio.path, str(self.wav))
        self.assertEqual(restored.render.audio.offset_seconds, -0.1)
        self.assertEqual(restored.render.audio.bitrate_kbps, 0)

    def test_video_recipe_preserves_audio_without_sharing_mutable_state(self):
        job = RECIPES['social_delivery'].create_job(str(self.html))
        job.render.audio = self.audio(offset_seconds=-0.2, bitrate_kbps=192)
        fresh = apply_recipe(job, 'exact_source_master')
        self.assertEqual(fresh.render.audio, job.render.audio)
        fresh.render.audio.path = 'changed.mp3'
        self.assertEqual(job.render.audio.path, str(self.wav))

    def test_queue_snapshot_preserves_independent_soundtracks(self):
        job = RECIPES['social_delivery'].create_job(str(self.html))
        job.render.audio = self.audio(offset_seconds=-0.25, channels=1)
        snapshot = snapshot_for_export(job, 2)
        snapshot.render.audio.path = 'another.mp3'
        snapshot.render.audio.offset_seconds = 1.0
        self.assertEqual(job.render.audio.path, str(self.wav))
        self.assertEqual(job.render.audio.offset_seconds, -0.25)
        self.assertEqual(snapshot.render.audio.channels, 1)


if __name__ == '__main__':
    unittest.main()
