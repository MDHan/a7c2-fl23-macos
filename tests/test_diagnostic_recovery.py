"""Synthetic recovery evidence only; never opens a camera or runs WBI."""
from contextlib import contextmanager, nullcontext, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import test_finish
import test_diagnostic
from macfl23 import cli, diagnostic as d, finish as f, session as sm, workflow as w


TIMEOUT = {'service_exit_request': 'sent',
           'errors': ['normal USB return: Expected USB identity did not appear: 0e8a']}
NORMAL = {'service_exit_request': 'sent', 'normal_usb_returned': True, 'errors': []}


def save(path, value):
    path.write_text(json.dumps(value))


@contextmanager
def fixture():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        programs = [b'n' * 32, b'cp-program']
        spans = {i * 4: 2 for i in range(6)}
        with patch.object(f.core, 'FINAL', [f.core.sha(p) for p in programs]), \
             patch.object(f, 'SPANS', spans), patch.object(f.core, 'AP_KEYS', [2]):
            source, installer, records = test_finish.FinishTests().history_fixture(root)
            source.state['journal'][-1]['result'] = dict(TIMEOUT)
            source.state_path = root / 'state.json'
            save(source.state_path, source.state)
            (root / 'candidate').mkdir()
            (root / 'candidate/appFw.so').write_bytes(programs[0])
            runtime = {'image_mode': '2', 'spans': [
                {'offset': offset, 'length': length, 'memory': b'oo'.hex(), 'disk': b'nn'.hex()}
                for offset, length in spans.items()]}
            def parse(name, raw, mode=None):
                return installer if name == 'installer' else json.loads(raw)
            with patch.object(f.core, 'parse_native', side_effect=parse), \
                 patch.object(f.core, 'backup_records', side_effect=lambda raw: records[raw]):
                yield source, programs, runtime


def diagnosis(source, programs, runtime, name='diagnostic-random', minute=10):
    directory = source.root / name
    directory.mkdir()
    (directory / 'source-state.private.json').write_bytes(source.state_path.read_bytes())
    rows = [
        {'step': 'diagnostic_source_bound', 'source_state_sha256': f.core.sha(source.state_path.read_bytes()),
         'identity': source.state['identity']},
        {'step': 'diagnostic_camera_verified', 'identity': source.state['identity']},
        {'step': 'host_full_file_readback_verified', 'hashes': f.core.FINAL},
        {'step': 'native_about_to_run', 'module': 'runtime', 'mode': None},
        {'step': 'native_verified', 'module': 'runtime', 'mode': None, 'result': runtime},
        {'step': 'mac_usb_session_exit', 'result': dict(NORMAL)}]
    start = datetime(2026, 10, 18, 10, minute)
    for i, row in enumerate(rows):
        row['time'] = (start + timedelta(seconds=i)).isoformat()
    save(directory / 'diagnostic.private.json', {'journal': rows})
    save(directory / 'diagnostic-summary.json', {
        'format': 1, 'purpose': 'interrupted_install_diagnosis', 'status': 'diagnosed',
        'source_phase': 'needs_review', 'firmware_or_settings_or_wbi_writes': False,
        **dict.fromkeys(('body_completed', 'source_state_unchanged', 'normal_usb_returned',
                         'programs_match_final', 'runtime_disk_matches_candidate'), True)})
    (directory / 'readback').mkdir()
    for name, program in zip(f.core.PROGRAMS, programs):
        (directory / 'readback' / ('20261018-100002-000000-' + name)).write_bytes(program)
    (directory / 'evidence').mkdir()
    save(directory / 'evidence' / ((start + timedelta(seconds=4)).strftime('%Y%m%d-%H%M%S-%f-runtime.bin')),
         runtime)
    return directory


class DiagnosticRecoveryTests(unittest.TestCase):
    def test_real_diagnosis_writer_produces_reviewable_bound_evidence_with_fake_camera(self):
        with TemporaryDirectory() as tmp:
            template, raw, records, _, _ = test_diagnostic.DiagnosticTests().inspect_fixture(tmp)
            source = template.source
            source.state.update(failed_action='install', failed_during='writing_settings',
                                identity={'model': f.core.MODEL, 'serial_sha256': 'a' * 64})
            source.state_path = source.root / 'state.json'
            save(source.state_path, source.state)
            source.validate_candidates = lambda: None
            raw.identity = source.state['identity']
            factory = MagicMock()
            factory.return_value.__enter__.return_value = raw
            def leave(*args):
                factory.audit(dict(NORMAL))
                return False
            factory.return_value.__exit__.side_effect = leave
            runtime = {'image_mode': '2\n', 'spans': [
                {'offset': 1, 'length': 2, 'memory': '6f6f', 'disk': '6263'}]}
            def module(job, session, name, mode=None):
                self.assertEqual((name, mode), ('runtime', None))
                job.audit('native_about_to_run', module=name, mode=mode)
                folder = job.root / 'evidence'
                folder.mkdir()
                report = bytearray(1832)
                report[1608:1614] = b'*wbi4\n'
                (folder / datetime.now().strftime('%Y%m%d-%H%M%S-%f-runtime.bin')).write_bytes(report)
                job.audit('native_verified', module=name, mode=mode, result=runtime)
                return runtime
            original = source.state_path.read_bytes()
            with patch.object(w, 'MacJob', return_value=source), patch.object(sm, 'MacSession', factory), \
                 patch.object(cli, 'camera_lock', nullcontext), patch.object(cli, 'return_ownership'), \
                 patch('subprocess.Popen', return_value=MagicMock()), \
                 patch.object(d.DiagnosticJob, 'module', module), \
                 patch.object(f.core, 'FINAL', [f.core.sha(b'abcdef'), f.core.sha(b'cp-program')]), \
                 patch.object(f.core, 'backup_records', return_value=records), \
                 patch.object(f.core, 'parse_native', return_value=runtime), patch.object(f, 'SPANS', {1: 2}), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(d.run_diagnosis(source.root), 0)
                review = f.review_diagnostic_recovery(source, datetime.min)
                self.assertEqual(len(review['evidence_hashes']), 6)
            self.assertEqual(source.state_path.read_bytes(), original)
            raw.cp_write.assert_not_called()
            raw.ap_write.assert_not_called()
            raw.camera.saveBackup.assert_not_called()

    def test_bound_success_is_reviewable_without_usb_and_records_evidence_hashes(self):
        with fixture() as (source, programs, runtime), patch.object(sm, 'MacSession') as camera:
            directory = diagnosis(source, programs, runtime)
            original = source.state_path.read_bytes()
            review = f.review_history(source)
            self.assertEqual(review['observed_delta'], 60)
            recovery = review['diagnostic_recovery']
            self.assertEqual(recovery['directory'], directory.name)
            self.assertEqual(len(recovery['evidence_hashes']), 6)
            for name, digest in recovery['evidence_hashes'].items():
                self.assertEqual(digest, f.core.sha((directory / name).read_bytes()))
            self.assertEqual(source.state_path.read_bytes(), original)
            camera.assert_not_called()

    def test_later_failure_blocks_older_success_even_when_directory_names_sort_backwards(self):
        for outcome in ('unknown_session', 'timeout', 'missing_summary', 'invalid_summary',
                        'missing_log', 'broken_log', 'empty_log', 'summary_list', 'unfinished'):
            with self.subTest(outcome=outcome), fixture() as (source, programs, runtime):
                diagnosis(source, programs, runtime, 'diagnostic-z-old', 10)
                latest = diagnosis(source, programs, runtime, 'diagnostic-a-new', 20)
                log_path = latest / 'diagnostic.private.json'
                log = json.loads(log_path.read_text())
                if outcome == 'unknown_session':
                    log['journal'][-1]['result'] = {'service_exit_request': 'not_sent_unknown_session_state',
                                                  'errors': ['Session outcome unknown; no further device commands sent']}
                    save(log_path, log)
                if outcome == 'timeout':
                    log['journal'][-1]['result'] = dict(TIMEOUT)
                    save(log_path, log)
                if outcome == 'missing_summary': (latest / 'diagnostic-summary.json').unlink()
                if outcome == 'invalid_summary': (latest / 'diagnostic-summary.json').write_text('{')
                if outcome == 'summary_list': save(latest / 'diagnostic-summary.json', [])
                if outcome == 'missing_log': log_path.unlink()
                if outcome == 'broken_log': log_path.write_text('{')
                if outcome == 'empty_log': save(log_path, {'journal': []})
                if outcome == 'unfinished': save(log_path, {'journal': log['journal'][:1]})
                original = source.state_path.read_bytes()
                with patch.object(f, 'MacJob', return_value=source), \
                     patch.object(cli, 'camera_lock', nullcontext), patch.object(sm, 'MacSession') as camera:
                    with self.assertRaises(ValueError): f.run_finish(source.root)
                    camera.assert_not_called()
                self.assertEqual(source.state_path.read_bytes(), original)
                self.assertFalse((source.root / f.MARKER).exists())
                self.assertEqual(list(source.root.glob('finish-*')), [])

    def test_latest_success_supersedes_an_earlier_recorded_failure_by_time(self):
        with fixture() as (source, programs, runtime):
            older = diagnosis(source, programs, runtime, 'diagnostic-z-old', 10)
            log = json.loads((older / 'diagnostic.private.json').read_text())
            save(older / 'diagnostic.private.json', {'journal': log['journal'][:1]})
            (older / 'diagnostic-summary.json').unlink()
            latest = diagnosis(source, programs, runtime, 'diagnostic-a-new', 20)
            self.assertEqual(f.review_history(source)['diagnostic_recovery']['directory'], latest.name)

    def test_source_identity_chronology_and_underlying_evidence_are_required(self):
        problems = ('no_diagnosis', 'snapshot', 'state_changed', 'source_hash', 'expected_identity',
                    'actual_identity', 'before_install', 'overlap', 'clock_backwards', 'unbound_legacy',
                    'wrong_program_hash', 'missing_readback', 'duplicate_readback', 'corrupt_readback',
                    'missing_runtime', 'duplicate_runtime', 'runtime_mismatch', 'runtime_disk',
                    'runtime_spans', 'write_attempt', 'cleanup_pending', 'summary_incomplete')
        for problem in problems:
            with self.subTest(problem=problem), fixture() as (source, programs, runtime):
                if problem == 'no_diagnosis':
                    with self.assertRaises(ValueError): f.review_history(source)
                    continue
                directory = diagnosis(source, programs, runtime, minute=0 if problem == 'before_install' else 10)
                log_path = directory / 'diagnostic.private.json'
                log = json.loads(log_path.read_text())
                rows = log['journal']
                report = next((directory / 'evidence').glob('*-runtime.bin'))
                readback = next((directory / 'readback').iterdir())
                if problem == 'snapshot': (directory / 'source-state.private.json').write_bytes(b'other state')
                if problem == 'state_changed': source.state_path.write_bytes(source.state_path.read_bytes() + b'\n')
                if problem == 'source_hash': rows[0]['source_state_sha256'] = '0' * 64
                if problem == 'expected_identity': rows[0]['identity']['serial_sha256'] = 'b' * 64
                if problem == 'actual_identity': rows[1]['identity']['serial_sha256'] = 'b' * 64
                if problem == 'overlap': diagnosis(source, programs, runtime, 'diagnostic-overlap', 10)
                if problem == 'clock_backwards': rows[-1]['time'] = rows[0]['time']
                if problem == 'unbound_legacy': rows.pop(0)
                if problem == 'wrong_program_hash': rows[2]['hashes'] = ['0' * 64] * 2
                if problem == 'missing_readback': readback.unlink()
                if problem == 'duplicate_readback': (readback.parent / ('extra-' + readback.name)).write_bytes(readback.read_bytes())
                if problem == 'corrupt_readback': readback.write_bytes(b'wrong')
                if problem == 'missing_runtime': report.unlink()
                if problem == 'duplicate_runtime': (report.parent / report.name.replace('000000', '500000')).write_bytes(report.read_bytes())
                if problem == 'runtime_mismatch': rows[4]['result']['image_mode'] = '0'
                if problem == 'runtime_disk':
                    rows[4]['result']['spans'][0]['disk'] = '7878'
                    save(report, rows[4]['result'])
                if problem == 'runtime_spans':
                    rows[4]['result']['spans'].pop()
                    save(report, rows[4]['result'])
                if problem == 'write_attempt': rows[3]['module'], rows[3]['mode'] = 'wbi', 1
                if problem == 'cleanup_pending': rows[-1]['step'] = 'temporary_cleanup_pending'
                if problem == 'summary_incomplete':
                    path = directory / 'diagnostic-summary.json'
                    summary = json.loads(path.read_text())
                    summary['status'] = 'incomplete'
                    save(path, summary)
                save(log_path, log)
                with self.assertRaises(ValueError): f.review_history(source)

    def test_recovery_does_not_relax_original_exit_error_or_single_attempt_gates(self):
        for problem in ('not_sent', 'other_error', 'extra_error', 'explicit_false', 'prior_attempt'):
            with self.subTest(problem=problem), fixture() as (source, programs, runtime):
                exit_report = source.state['journal'][-1]['result']
                if problem == 'not_sent': exit_report['service_exit_request'] = 'not_sent_unknown_session_state'
                if problem == 'other_error': exit_report['errors'] = ['normal USB return: USB physical port changed']
                if problem == 'extra_error': exit_report['errors'] = [*TIMEOUT['errors'], 'close service handle: error']
                if problem == 'explicit_false': exit_report['normal_usb_returned'] = False
                if problem == 'prior_attempt': (source.root / f.MARKER).write_text('keep')
                save(source.state_path, source.state)
                diagnosis(source, programs, runtime)
                with self.assertRaises(ValueError): f.review_history(source)
