"""Offline diagnostics tests; USB/session entry points are always replaced."""
import contextlib
import copy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from macfl23 import cli, diagnostic as d, session as sm, workflow as w


class DiagnosticTests(unittest.TestCase):
    def test_guard_rejects_parameter_writes_saves_and_non_runtime_modules(self):
        raw = MagicMock()
        guarded = d.DiagnosticSession(raw)
        for method, args in [(guarded.cp_write, (1, b'x')), (guarded.ap_write, (1, b'x')),
                             (guarded.call, (raw.camera.saveBackup, 0x02a4)),
                             (guarded.call, (raw.camera.writeBackup, 1, b'x')),
                             (guarded.temp, ('a7c2.wbi.request', b'x')),
                             (guarded.temp, ('libJit.so', b'unverified-module')),
                             (guarded.read, ('/tmp/jiritsu/../../etc/shadow',))]:
            with self.assertRaises(ValueError):
                method(*args)
        raw.cp_write.assert_not_called()
        raw.ap_write.assert_not_called()
        raw.call.assert_not_called()
        raw.temp.assert_not_called()
        raw.read.assert_not_called()
        with TemporaryDirectory() as tmp:
            job = d.DiagnosticJob(tmp, SimpleNamespace(state={'phase': 'needs_review'}))
            for name, mode in [('wbi', 0), ('wbi', 1), ('installer', 0), ('installer', 1), ('runtime', 1)]:
                with self.assertRaises(ValueError):
                    job.module(guarded, name, mode)
            with self.assertRaises(ValueError):
                job.save('installed_pending_cold_start')

    def inspect_fixture(self, directory, bad_target=False, bad_program=False):
        root = Path(directory)
        source_root = root / 'source'
        (source_root / 'original').mkdir(parents=True)
        (source_root / 'candidate').mkdir()
        (source_root / 'original/backup.private.bin').write_bytes(b'original')
        (source_root / 'candidate/appFw.so').write_bytes(b'abcdef')
        merged = dict(zip(d.core.CP_KEYS, (b'cp1', b'cp2')))
        desired = {(1, k): v for k, v in merged.items()}
        desired.update({(0, k): bytes([10 + i]) for i, k in enumerate(d.core.AP_KEYS)})
        records = [{}, {}]
        for (chip, key), value in desired.items():
            records[chip][key] = SimpleNamespace(data=value, attr=96)
        saved = copy.deepcopy(records)
        if bad_target:
            saved[0][d.core.AP_KEYS[0]].data = b'\0'
        source = SimpleNamespace(root=source_root, state={'phase': 'needs_review'}, units=lambda: ({}, merged))
        output = root / 'diagnostic'
        output.mkdir()
        job = d.DiagnosticJob(output, source)
        raw = MagicMock()
        raw.read.side_effect = lambda path, missing=False: (b'wrong' if bad_program else b'cp-program') if path.endswith('cpapp-b.bin') else b'abcdef'
        raw.cp_read.side_effect = lambda k: merged[k]
        raw.camera.getBackupPresetData.side_effect = lambda live: b'live' if live else b'stored'
        raw.camera.readBackup.side_effect = lambda k: desired[0, k]
        raw.call.side_effect = lambda fn, *args: fn(*args)
        def runtime(session, name, mode=None):
            self.assertEqual((name, mode), ('runtime', None))
            folder = job.root / 'evidence'
            folder.mkdir()
            report = bytearray(1832)
            report[1608:1614] = b'*wbi4\n'
            (folder / 'offline-runtime.bin').write_bytes(report)
            return {'image_mode': '2\n', 'spans': [{'offset': 1, 'length': 2,
                                                   'memory': b'old'.hex(), 'disk': b'bc'.hex()}]}
        return job, raw, records, saved, runtime

    def test_report_exposes_mismatches_without_claiming_installation_success(self):
        with TemporaryDirectory() as tmp:
            job, raw, live, saved, runtime = self.inspect_fixture(tmp, bad_target=True)
            with patch.object(d.core, 'FINAL', [d.core.sha(b'abcdef'), d.core.sha(b'cp-program')]), \
                 patch.object(d.core, 'backup_records', side_effect=lambda data: saved if data == b'stored' else live), \
                 patch.object(job, 'module', side_effect=runtime), contextlib.redirect_stdout(io.StringIO()):
                job.inspect(d.DiagnosticSession(raw))
            summary = job.finish('diagnosed', True, True)
            self.assertFalse(summary['runtime_memory_matches_candidate'])
            self.assertTrue(summary['runtime_disk_matches_candidate'])
            self.assertEqual(summary['image_mode'], '2')
            self.assertTrue(summary['image_backend_expected'])
            self.assertTrue(any(not c['stored_matches'] for c in summary['target_checks']))
            self.assertEqual(summary['next_action'], 'maintainer_review_required')
            self.assertFalse(summary['firmware_or_settings_or_wbi_writes'])
            public = json.dumps(summary)
            for private in (tmp, 'serial', 'memory":', 'data":', 'cp1', 'old'):
                self.assertNotIn(private, public)
            self.assertFalse((job.root / 'state.json').exists())
            raw.cp_write.assert_not_called()
            raw.ap_write.assert_not_called()
            raw.camera.saveBackup.assert_not_called()

    def test_wrong_program_stops_before_runtime_upload_or_settings_reads(self):
        with TemporaryDirectory() as tmp:
            job, raw, _, _, _ = self.inspect_fixture(tmp, bad_program=True)
            with patch.object(d.core, 'FINAL', [d.core.sha(b'abcdef'), d.core.sha(b'cp-program')]), \
                 patch.object(job, 'module') as module, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    job.inspect(d.DiagnosticSession(raw))
            module.assert_not_called()
            raw.call.assert_not_called()
            raw.temp.assert_not_called()
            self.assertNotIn('programs_match_final', job.summary)

    def test_success_timeout_and_failure_preserve_original_state_bytes(self):
        for outcome in ('success', 'timeout', 'failure'):
            with self.subTest(outcome=outcome), TemporaryDirectory() as tmp:
                root = Path(tmp)
                state_file = root / 'state.json'
                original = b'{"phase":"needs_review","original_failure":"keep verbatim"}\n'
                state_file.write_bytes(original)
                source = MagicMock(root=root, state_path=state_file)
                source.state = {'phase': 'needs_review', 'failed_action': 'install',
                                'failed_during': 'writing_settings',
                                'identity': {'model': d.core.MODEL, 'serial_sha256': 'a' * 64}}
                factory = MagicMock()
                connection = factory.return_value
                connection.__enter__.return_value.identity = source.state['identity']
                def exit_session(*args):
                    factory.audit({'service_exit_request': 'sent', 'normal_usb_returned': outcome != 'timeout',
                                   'errors': ['normal USB timeout'] if outcome == 'timeout' else []})
                    if outcome == 'timeout':
                        raise sm.NormalReturnTimeout('normal USB timeout')
                    return False
                connection.__exit__.side_effect = exit_session
                def inspect(job, session):
                    if outcome == 'failure':
                        raise ValueError('offline failure')
                    job.summary['body_completed'] = True
                with patch.object(w, 'MacJob', return_value=source), patch.object(sm, 'MacSession', factory), \
                     patch.object(cli, 'camera_lock', contextlib.nullcontext), \
                     patch('subprocess.Popen', return_value=MagicMock()), \
                     patch.object(d.DiagnosticJob, 'inspect', inspect), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    result = d.run_diagnosis(root)
                self.assertEqual(state_file.read_bytes(), original)
                self.assertEqual(result, {'success': 0, 'timeout': 2, 'failure': 1}[outcome])
                summary = json.loads(next(root.glob('diagnostic-*/diagnostic-summary.json')).read_text())
                self.assertEqual(summary['status'], {'success': 'diagnosed', 'timeout': 'diagnosed_usb_unconfirmed',
                                                     'failure': 'incomplete'}[outcome])
                self.assertTrue(summary['source_state_unchanged'])
                directory = next(root.glob('diagnostic-*'))
                self.assertEqual((directory / 'source-state.private.json').read_bytes(), original)
                journal = json.loads((directory / 'diagnostic.private.json').read_text())['journal']
                self.assertEqual(journal[0]['step'], 'diagnostic_source_bound')
                self.assertEqual(journal[0]['source_state_sha256'], d.core.sha(original))
                self.assertEqual(journal[1]['identity'], source.state['identity'])
                self.assertNotIn('serial_sha256', json.dumps(summary))
                source.save.assert_not_called()
                source.audit.assert_not_called()

    def test_wrong_workspace_phase_is_rejected_before_usb(self):
        source = MagicMock(state={'phase': 'needs_review', 'failed_action': 'prepare',
                                 'failed_during': 'reading_backups'})
        with patch.object(w, 'MacJob', return_value=source), patch.object(sm, 'MacSession') as factory, \
             patch.object(cli, 'camera_lock', contextlib.nullcontext):
            with self.assertRaises(ValueError):
                d.run_diagnosis(Path('/unused'))
            factory.assert_not_called()

    def test_low_battery_rejected_before_any_session_or_lock(self):
        args = cli.parser().parse_args(['diagnose', '--workspace', '/unused', '--battery-percent', '49'])
        with patch.object(cli.platform, 'system', return_value='Darwin'), \
             patch.object(cli.os, 'geteuid', return_value=0), patch.object(cli, 'camera_lock') as lock:
            with self.assertRaises(ValueError):
                cli.run_camera(args)
            lock.assert_not_called()
