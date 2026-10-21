"""Restricted completion tests: all camera/session calls are fake."""
import contextlib
import copy
import io
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from macfl23 import cli, finish as f, session as sm


def settings():
    expected = {(1, 1): b'new-cp', (0, 2): b'\x0a'}
    before = [{2: NS(attr=96, data=b'\0'), f.KEY: NS(attr=2, data=(120).to_bytes(4, 'little')),
               3: NS(attr=2, data=b'keep')}, {1: NS(attr=8, data=b'old-cp')}]
    after = copy.deepcopy(before)
    for (chip, key), value in expected.items():
        after[chip][key].data = value
    after[0][f.KEY].data = (180).to_bytes(4, 'little')
    return before, after, copy.deepcopy(after), expected


class FinishTests(unittest.TestCase):
    def test_scoped_review_accepts_only_exact_observed_difference(self):
        before, after, stored, expected = settings()
        f.review_records(before, after, stored, expected)
        self.assertFalse(f.core.runtime_counter(0, f.KEY, before[0][f.KEY], after[0][f.KEY]))
        for problem in ('delta', 'attr', 'length', 'other_key', 'missing_key', 'saved_target'):
            with self.subTest(problem=problem):
                b, a, s = copy.deepcopy((before, after, stored))
                if problem == 'delta': a[0][f.KEY].data = (181).to_bytes(4, 'little')
                if problem == 'attr': a[0][f.KEY].attr = 8
                if problem == 'length': a[0][f.KEY].data += b'\0'
                if problem == 'other_key': a[0][3].data = b'changed'
                if problem == 'missing_key': del s[0][3]
                if problem == 'saved_target': s[1][1].data = b'wrong'
                with self.assertRaises(ValueError): f.review_records(b, a, s, expected)

    def history_fixture(self, root):
        (root / 'evidence').mkdir()
        before, after, stored, expected = settings()
        native = {'window_writes': 40, 'readonly_verified': True, 'before': f.core.ORIGINAL, 'after': f.core.FINAL}
        journal = [
            {'step': 'native_about_to_run', 'module': 'installer', 'mode': 1, 'time': '2026-10-18T10:00:00'},
            {'step': 'native_verified', 'module': 'installer', 'mode': 1, 'result': native},
            {'step': 'host_full_file_readback_verified', 'hashes': f.core.FINAL},
            {'step': 'mac_usb_session_exit', 'time': '2026-10-18T10:02:00',
             'result': {'service_exit_request': 'sent', 'normal_usb_returned': True, 'errors': []}}]
        source = MagicMock(root=root)
        source.state = {'phase': 'needs_review', 'failed_action': 'install', 'failed_during': 'writing_settings',
                        'last_error': next(iter(f.ERRORS)), 'journal': journal,
                        'identity': {'model': f.core.MODEL, 'serial_sha256': 'a' * 64}}
        source.units.return_value = ({}, {1: b'new-cp'})
        for i, (name, value) in enumerate([('installer.bin', b'installer'), ('settings-before.private.bin', b'before'),
                                         ('settings-after.private.bin', b'after'), ('settings-stored.private.bin', b'stored')]):
            (root / 'evidence' / ('20261018-1001%02d-000000-' % i + name)).write_bytes(value)
        records = {b'before': before, b'after': after, b'stored': stored}
        return source, native, records

    def test_history_requires_unique_complete_evidence_and_no_previous_attempt(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, native, records = self.history_fixture(root)
            # AP/CP synthetic target keys are patched only for this offline fixture.
            with patch.object(f.core, 'AP_KEYS', [2]), patch.object(f.core, 'parse_native', return_value=native), \
                 patch.object(f.core, 'backup_records', side_effect=lambda raw: records[raw]):
                review = f.review_history(source)
                self.assertEqual(review['observed_delta'], 60)
                self.assertEqual(len(review['evidence_hashes']), 4)
                marker = root / f.MARKER
                marker.write_text('attempt exists')
                with self.assertRaises(ValueError): f.review_history(source)
                marker.unlink()
                source.state['journal'][-1]['result']['normal_usb_returned'] = False
                with self.assertRaises(ValueError): f.review_history(source)
                source.state['journal'][-1]['result'] = {
                    'service_exit_request': 'sent',
                    'normal_usb_returned': None,
                    'errors': ['normal USB return: Expected USB identity did not appear: 0e8a'],
                }
                with self.assertRaises(ValueError): f.review_history(source)
                diag = root / 'diagnostic-sample'
                diag.mkdir()
                (diag / 'diagnostic-summary.json').write_text(json.dumps({
                    'status': 'diagnosed',
                    'source_state_unchanged': True,
                    'normal_usb_returned': True,
                    'programs_match_final': True,
                    'runtime_disk_matches_candidate': True,
                }))
                # A shareable summary alone is not recovery evidence.
                with self.assertRaises(ValueError): f.review_history(source)
                source.state['journal'][-1]['result'] = {'service_exit_request': 'sent', 'normal_usb_returned': True, 'errors': []}
                source.state['features'] = ['fl23', 'no_ntsc_notice']
                with self.assertRaises(ValueError): f.review_history(source)
                source.state.pop('features')
                native['window_writes'] = 39
                with self.assertRaises(ValueError): f.review_history(source)

    def test_wbi_guard_never_exposes_program_installer_or_settings_save(self):
        raw = MagicMock()
        guarded = f.FinishSession(raw)
        with self.assertRaises(ValueError): guarded.temp('a7c2.wbi.request', struct.pack('<8sII', b'A7WBI001', 1, 0))
        guarded.wbi_mode = 0
        with self.assertRaises(ValueError): guarded.temp('a7c2.wbi.request', struct.pack('<8sII', b'A7WBI001', 1, 0))
        with self.assertRaises(ValueError): guarded.temp('a7c2.install.request', b'anything')
        with self.assertRaises(ValueError): guarded.temp('libJit.so', b'unknown-module')
        with self.assertRaises(ValueError): guarded.call(raw.camera.saveBackup, 0x02a4)
        with self.assertRaises(ValueError): guarded.cp_write(1, b'anything')
        with self.assertRaises(ValueError): guarded.ap_write(1, b'anything')
        raw.call.assert_not_called()
        raw.temp.assert_not_called()
        guarded.wbi_mode = 1
        request = struct.pack('<8sII', b'A7WBI001', 1, 0)
        guarded.temp('a7c2.wbi.request', request)
        raw.temp.assert_called_once_with('a7c2.wbi.request', request, False)

    def test_current_gate_rejects_unknown_memory_disk_targets_and_snapshot_mode(self):
        spans = {1: 2, 4: 2, 7: 2, 10: 2, 13: 2, 16: 2}
        for problem in ('none', 'unknown_memory', 'disk', 'targets', 'mode', 'missing_region'):
            with self.subTest(problem=problem), TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'original').mkdir(); (root / 'candidate').mkdir(); (root / 'evidence').mkdir()
                original, candidate = b'o' * 20, b'n' * 20
                (root / 'original/appFw.so.bin').write_bytes(original)
                (root / 'candidate/appFw.so').write_bytes(candidate)
                (root / 'evidence/offline-runtime.bin').write_bytes(b'fake-runtime')
                source = NS(root=root, state={'phase': 'needs_review'})
                job = f.FinishJob(root, source)
                job.summary.update(programs_match_final=True, image_mode='2', image_backend_expected=True,
                                   target_checks=[dict.fromkeys(('direct_matches', 'live_matches', 'stored_matches',
                                                                'attributes_match_original'), True) for _ in range(4)])
                regions = [{'offset': offset, 'length': length,
                            'memory': (original if offset in (7, 10) else candidate)[offset:offset + length].hex(),
                            'disk': candidate[offset:offset + length].hex()} for offset, length in spans.items()]
                if problem == 'unknown_memory': regions[2]['memory'] = b'xx'.hex()
                if problem == 'disk': regions[2]['disk'] = b'xx'.hex()
                if problem == 'targets': job.summary['target_checks'][0]['stored_matches'] = False
                if problem == 'mode': job.summary['image_mode'] = '0'
                if problem == 'missing_region': regions.pop()
                with patch.object(f, 'SPANS', spans), patch.object(f, 'MENU_SPANS', {7: 2, 10: 2}), \
                     patch.object(job, 'inspect'), patch.object(job, 'module') as module, \
                     patch.object(f.core, 'parse_native', return_value={'spans': regions}):
                    if problem == 'none':
                        job.check_current(MagicMock())
                        self.assertTrue(job.wbi_preflight_verified)
                        module.assert_called_once()
                        self.assertEqual(module.call_args.args[1:], ('wbi', 0))
                    else:
                        with self.assertRaises(ValueError): job.check_current(MagicMock())
                        module.assert_not_called()
                        self.assertFalse(job.wbi_preflight_verified)

    def test_success_timeout_write_failure_and_precheck_failure_have_distinct_states(self):
        for outcome in ('success', 'timeout', 'write_failure', 'precheck_failure'):
            with self.subTest(outcome=outcome), TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = f.MacJob(root)
                source.state = {'phase': 'needs_review', 'failed_action': 'install',
                                'failed_during': 'writing_settings', 'last_error': 'original error', 'journal': [],
                                'identity': {'model': f.core.MODEL, 'serial_sha256': 'a' * 64}}
                source.save('needs_review')
                before = source.state_path.read_bytes()
                factory = MagicMock()
                def exit_session(*args):
                    factory.audit({'service_exit_request': 'sent', 'normal_usb_returned': outcome != 'timeout',
                                   'errors': ['normal USB timeout'] if outcome == 'timeout' else []})
                    if outcome == 'timeout': raise sm.NormalReturnTimeout('normal USB timeout')
                    return False
                factory.return_value.__exit__.side_effect = exit_session
                def check(job, session):
                    if outcome == 'precheck_failure': raise ValueError('current targets differ')
                    job.wbi_permitted = job.wbi_preflight_verified = True
                def module(job, session, name, mode=None):
                    self.assertEqual((name, mode), ('wbi', 1))
                    self.assertTrue((root / f.MARKER).is_file())
                    self.assertEqual(json.loads(source.state_path.read_text())['phase'], 'finishing_snapshot')
                    if outcome == 'write_failure': raise RuntimeError('unknown WBI outcome')
                    return {'verified': True}
                with patch.object(f, 'MacJob', return_value=source), patch.object(f, 'review_history', return_value={}), \
                     patch.object(sm, 'MacSession', factory), patch.object(cli, 'camera_lock', contextlib.nullcontext), \
                     patch('subprocess.Popen', return_value=MagicMock()), \
                     patch.object(f.FinishJob, 'check_current', check), patch.object(f.FinishJob, 'module', module), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    result = f.run_finish(root)
                saved = json.loads(source.state_path.read_text())
                if outcome == 'precheck_failure':
                    self.assertEqual(source.state_path.read_bytes(), before)
                    self.assertFalse((root / f.MARKER).exists())
                else:
                    self.assertTrue((root / f.MARKER).exists())
                if outcome == 'write_failure':
                    self.assertEqual(saved['phase'], 'needs_review')
                    self.assertEqual(saved['failed_action'], 'finish-install')
                if outcome in ('success', 'timeout'):
                    self.assertEqual(saved['phase'], 'installed_pending_cold_start' + ('_usb_unconfirmed' if outcome == 'timeout' else ''))
                self.assertEqual(result, {'success': 0, 'timeout': 2, 'write_failure': 1, 'precheck_failure': 1}[outcome])
                copy_file = next(root.glob('finish-*/source-state.private.json'))
                self.assertEqual(copy_file.read_bytes(), before)

    def test_explicit_experimental_flag_and_actual_battery_are_required(self):
        args = cli.parser().parse_args(['finish-install', '--workspace', '/unused', '--battery-percent', '80'])
        with patch.object(cli.platform, 'system', return_value='Darwin'), \
             patch.object(cli.os, 'geteuid', return_value=0), patch.object(cli, 'private_path', return_value=Path('/unused')), \
             patch.object(f, 'run_finish') as run:
            with self.assertRaises(ValueError): cli.run_camera(args)
            run.assert_not_called()
            args.battery_percent = 49
            args.accept_experimental_finish = True
            with self.assertRaises(ValueError): cli.run_camera(args)
            run.assert_not_called()
