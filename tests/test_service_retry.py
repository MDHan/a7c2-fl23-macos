"""Service-entry retry regressions. Every USB operation is replaced offline."""
import contextlib
import copy
from datetime import datetime, timedelta
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

from macfl23 import cli, integrated, service_retry as retry, session as sm, workflow as w


def traceback_fixture():
    return '''Traceback (most recent call last):
  File "/offline/macfl23/cli.py", line 200, in run_camera
    job.install(args.battery_percent)
    ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
  File "/offline/.upstream/a7c2_fl23_preview/app.py", line 164, in install
    with Session(self.state['identity']) as session:
         ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "/offline/macfl23/session.py", line 127, in __enter__
    self.dev = wait_pid(0x0336, self.location)
               ~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "/offline/macfl23/session.py", line 75, in wait_pid
    raise UsbWaitTimeout('Expected USB identity did not appear: %04x' % pid)
macfl23.session.UsbWaitTimeout: Expected USB identity did not appear: 0336
'''.encode()


def fixture(root, features=None):
    for name in ('original', 'candidate', 'evidence'):
        (root / name).mkdir()
    job = w.MacJob(root)
    names = [name + '.bin' for name in w.core.PROGRAMS] + [
        'backup.private.bin', 'backup-stored.private.bin'] + [
        'unit-%08x.bin' % key for key in w.core.CP_KEYS] + [
        'ap-%08x.bin' % key for key in w.core.AP_KEYS]
    hashes = {}
    for name in names:
        raw = b'offline-stock'
        (root / 'original' / name).write_bytes(raw)
        hashes[name] = w.core.sha(raw)
    tick = datetime.now() - timedelta(hours=1)
    rows = []
    def row(step, **values):
        nonlocal tick
        tick += timedelta(seconds=1)
        rows.append(dict(time=tick.isoformat(), step=step, **values))
    for name in names:
        row('backup_verified', name=name, sha256=hashes[name])
    for module, mode in (('probe', None), ('installer', 0), ('wbi', 0)):
        row('native_about_to_run', module=module, mode=mode)
        row('native_verified', module=module, mode=mode, result={})
    row('mac_usb_session_exit', result=copy.deepcopy(retry.NORMAL_EXIT))
    row('mac_usb_session_exit', result=copy.deepcopy(retry.EMPTY_EXIT))
    path = root / 'evidence' / ((tick + timedelta(seconds=1)).strftime('%Y%m%d-%H%M%S-%f') + '-error.txt')
    path.write_bytes(traceback_fixture())
    job.state = dict(format=1, model=w.core.MODEL, identity=dict(model=w.core.MODEL, serial_sha256='offline'),
                     phase='needs_review', failed_during='prepared', failed_action='install',
                     last_error=retry.ERROR, backup_files=hashes, journal=rows)
    if features is not None:
        job.state['features'] = features
    job.save('needs_review')
    return job, path


class ServiceRetryTests(unittest.TestCase):
    def stock(self):
        return patch.object(w.core, 'ORIGINAL', [w.core.sha(b'offline-stock')] * 2)

    def test_legacy_and_both_current_profiles_accept_the_reported_python314_trace(self):
        for features in (None, ['fl23'], integrated.BASE.FEATURES, integrated.COMBINED.FEATURES):
            with self.subTest(features=features), tempfile.TemporaryDirectory() as temp, self.stock():
                job, path = fixture(Path(temp), features)
                before = job.state_path.read_bytes()
                args = cli.parser().parse_args(['install', '--workspace', temp, '--battery-percent', '61',
                                               '--retry-service-entry'])
                with patch.object(job, 'validate_candidates') as validate:
                    review = cli.check_phase(args, job)
                    validate.assert_called_once()
                self.assertEqual(review['entry_failures'], 1)
                self.assertEqual(review['error_files'], {path.name: w.core.sha(path.read_bytes())})
                self.assertEqual(before, job.state_path.read_bytes())
                with patch.object(job, 'validate_candidates'), self.assertRaises(ValueError):
                    cli.check_phase(NS(action='install', retry_service_entry=False), job)
                job.state['journal'][-2]['result'] = {'service_exit_request': 'sent', 'errors': [
                    'normal USB return: Expected USB identity did not appear: 0e8a']}
                for phase in ('prepared', 'prepared_usb_unconfirmed'):
                    job.state['failed_during'] = phase
                    self.assertEqual(retry.review_entry_failure(job)['failed_during'], phase)

    def test_write_stages_other_actions_and_similar_errors_are_not_retryable(self):
        changes = [dict(failed_during=p) for p in ('writing_programs', 'programs_written', 'writing_settings',
                   'settings_verified', 'needs_review')] + [dict(failed_action=a) for a in (
                       'restore', 'upgrade', 'verify')] + [dict(last_error=e) for e in (
                           retry.ERROR.replace('0336', '0e8a'), 'TimeoutError: unknown authentication')]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp, self.stock():
                job, _ = fixture(Path(temp))
                job.state.update(change)
                with patch.object(sm, 'normal_capture') as usb, self.assertRaises(ValueError):
                    retry.review_entry_failure(job)
                usb.assert_not_called()

    def test_missing_ambiguous_stale_and_in_body_evidence_is_rejected_before_usb(self):
        for problem in ('missing_error', 'duplicate_error', 'newer_error', 'error_body', 'auth_frame',
                        'extra_frame', 'no_backup', 'stock', 'preflight_write', 'extra_write',
                        'exit_error', 'exit_stop', 'time', 'marker', 'diagnosis', 'empty_rows'):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as temp, self.stock():
                job, path = fixture(Path(temp)); rows = job.state['journal']
                if problem == 'missing_error': path.unlink()
                if problem == 'duplicate_error':
                    other = path.with_name((datetime.now() - timedelta(minutes=1)).strftime('%Y%m%d-%H%M%S-%f') + '-error.txt')
                    other.write_bytes(path.read_bytes())
                if problem == 'newer_error':
                    (path.parent / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-error.txt')).write_text('another failure')
                if problem == 'error_body': path.write_bytes(path.read_bytes().replace(b'with Session', b'self.module'))
                if problem == 'auth_frame': path.write_bytes(path.read_bytes().replace(b'self.dev = wait_pid(0x0336, self.location)', b'first.authenticate()'))
                if problem == 'extra_frame': path.write_bytes(path.read_bytes().replace(b'    self.dev = ', b'  File "/offline/tool.py", line 1, in body\n    fn()\n    self.dev = '))
                if problem == 'no_backup': job.state['backup_files'].pop('backup-stored.private.bin')
                if problem == 'stock': (job.root / 'original/appFw.so.bin').write_bytes(b'changed')
                if problem == 'preflight_write': rows[10]['mode'] = 1
                if problem == 'extra_write': rows.insert(-1, dict(rows[-1], step='about_to_save_ap'))
                if problem == 'exit_error': rows[-1]['result']['errors'] = ['unknown']
                if problem == 'exit_stop': rows[-1]['result']['service_exit_request'] = 'sent'
                if problem == 'time': rows[-1]['time'] = rows[0]['time']
                if problem == 'marker': (job.root / 'finish-attempt.private.json').write_text('{}')
                if problem == 'diagnosis': (job.root / 'diagnostic-incomplete').mkdir()
                if problem == 'empty_rows': job.state['journal'] = []
                before = job.state_path.read_bytes()
                with patch.object(sm, 'normal_capture') as usb, self.assertRaises(ValueError):
                    retry.review_entry_failure(job)
                usb.assert_not_called()
                self.assertEqual(before, job.state_path.read_bytes())

    @contextlib.contextmanager
    def cli_environment(self, job, outcome='entry_timeout'):
        """Use real CLI, session lifecycle, wait_pid and traceback generation."""
        normal = MagicMock(bus=0, port_numbers=(1,))
        service = MagicMock(bus=0, port_numbers=(1,))
        service.is_kernel_driver_active.return_value = False
        camera = MagicMock()
        camera.readHasp.return_value = bytes(88)
        camera.getBackupPresetData.return_value = b'current'
        records = [dict.fromkeys(w.core.AP_KEYS, NS(data=b'\0')), dict.fromkeys(w.core.CP_KEYS, NS(data=b'cp'))]
        if outcome == 'changed_settings':
            records[0][w.core.AP_KEYS[0]] = NS(data=b'\x01')
        def files(session, hashes):
            readback = job.root / 'readback'
            readback.mkdir(exist_ok=True)
            for name in w.core.PROGRAMS:
                (readback / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + name)).write_bytes(
                    b'offline-stock' if hashes == w.core.ORIGINAL else b'offline-final')
            job.audit('host_full_file_readback_verified', hashes=hashes)
            if outcome == 'changed_stock':
                raise ValueError('stock mismatch')
        def module(session, name, mode):
            job.audit('native_about_to_run', module=name, mode=mode)
            if outcome == 'write_error' and name == 'installer':
                raise TimeoutError('unknown write')
            job.audit('native_verified', module=name, mode=mode, result={})
        with contextlib.ExitStack() as stack:
            for obj, name, value in [
                (cli.platform, 'system', lambda: 'Darwin'), (cli.os, 'geteuid', lambda: 0),
                (cli, 'camera_lock', contextlib.nullcontext), (cli, 'return_ownership', lambda paths: None),
                (cli.subprocess, 'Popen', MagicMock()), (w, 'MacJob', lambda root: job),
                (job, 'validate_candidates', MagicMock()), (job, 'load', MagicMock()),
                (job, 'files', MagicMock(side_effect=files)), (job, 'module', MagicMock(side_effect=module)),
                (job, 'units', lambda: ({key: b'cp' for key in w.core.CP_KEYS}, {})),
                (job, 'settings', MagicMock(side_effect=lambda session: job.audit('settings_storage_verified', restore=False))),
                (w.core, 'backup_records', lambda raw: records),
                (sm, 'normal_capture', lambda: (normal, True, True)),
                (sm, 'read_identity', lambda dev: (
                    dict(job.state['identity'], serial_sha256='different') if outcome == 'identity' else job.state['identity'], object())),
                (sm, 'BorrowedBackend', lambda *a: MagicMock()),
                (sm.usb.backend.libusb1, 'get_backend', lambda **kw: object()),
                (sm.usb.core, 'find', lambda **kw: [service] if outcome == 'service_present' else []),
                (sm.usb.util, 'claim_interface', MagicMock()),
                (sm.usb.util, 'release_interface', MagicMock()),
                (sm.usb.util, 'dispose_resources', MagicMock()),
                (sm.transport, 'GenericUsbDriver', lambda x: x),
                (sm.transport, 'SonySenserAuthDevice', MagicMock()),
                (sm.transport, 'SonySenserDevice', lambda x: object()),
                (sm.transport, 'SonySenserCamera', lambda x: camera),
                (sm.time, 'sleep', lambda n: None),
            ]:
                stack.enter_context(patch.object(obj, name, value))
            getter = stack.enter_context(patch.object(sm.transport, 'get_device'))
            if outcome == 'entry_timeout':
                getter.side_effect = RuntimeError('not found')
                stack.enter_context(patch.object(sm.time, 'monotonic', side_effect=[0, 20]))
            elif outcome == 'return_timeout':
                getter.side_effect = [service, RuntimeError('not found')]
                stack.enter_context(patch.object(sm.time, 'monotonic', side_effect=[0, 0, 70]))
            else:
                getter.side_effect = [service, normal]
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            yield getter

    def args(self, temp):
        return ['install', '--workspace', temp, '--battery-percent', '61', '--retry-service-entry']

    def test_repeated_entry_timeout_is_reviewable_without_resetting_phase_or_deleting_errors(self):
        with tempfile.TemporaryDirectory() as temp, self.stock():
            job, path = fixture(Path(temp)); before = job.state_path.read_bytes()
            for count in (2, 3):
                with self.cli_environment(job) as getter:
                    self.assertEqual(cli.main(self.args(temp)), 1)
                    getter.assert_called_once_with(0x0336)
                    job.module.assert_not_called(); job.files.assert_not_called(); job.settings.assert_not_called()
                self.assertEqual(job.state['phase'], 'needs_review')
                self.assertEqual(job.state['failed_during'], 'prepared')
                self.assertEqual(retry.review_entry_failure(job)['entry_failures'], count)
            snapshots = sorted((job.root / 'evidence').glob('*-install-entry-retry-state.private.json'))
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(snapshots[0].read_bytes(), before)
            self.assertEqual(path.read_bytes(), traceback_fixture())
            snapshots[0].write_text('{}')
            with self.assertRaises(ValueError): retry.review_entry_failure(job)

    def test_success_runs_original_checks_and_installer_once_and_clears_only_current_failure(self):
        for features in (None, integrated.BASE.FEATURES, integrated.COMBINED.FEATURES):
            with self.subTest(features=features), tempfile.TemporaryDirectory() as temp, self.stock():
                job, path = fixture(Path(temp), features)
                original = {p.name: p.read_bytes() for p in (job.root / 'original').iterdir()}
                with self.cli_environment(job, 'success'):
                    self.assertEqual(cli.main(self.args(temp)), 0)
                    self.assertEqual([c.args[1:] for c in job.files.call_args_list], [(w.core.ORIGINAL,), (w.core.FINAL,)])
                    self.assertEqual([c.args[1:] for c in job.module.call_args_list], [('wbi', 0), ('installer', 1), ('wbi', 1)])
                    job.settings.assert_called_once()
                self.assertEqual(job.state['phase'], 'installed_pending_cold_start')
                self.assertIsNone(job.state['failed_action']); self.assertIsNone(job.state['last_error'])
                self.assertEqual(original, {p.name: p.read_bytes() for p in (job.root / 'original').iterdir()})
                self.assertTrue(path.exists())
                with self.assertRaises(ValueError): retry.review_entry_failure(job)

    def test_current_program_mismatch_and_partial_write_cannot_be_retried(self):
        for outcome in ('changed_stock', 'changed_settings', 'identity', 'service_present', 'write_error'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temp, self.stock():
                job, _ = fixture(Path(temp))
                with self.cli_environment(job, outcome):
                    self.assertEqual(cli.main(self.args(temp)), 1)
                    if outcome != 'write_error': job.module.assert_not_called()
                    else:
                        self.assertEqual(job.state['failed_during'], 'writing_programs')
                        self.assertEqual([c.args[1:] for c in job.module.call_args_list], [('wbi', 0), ('installer', 1)])
                    job.settings.assert_not_called()
                before = job.state_path.read_bytes()
                with self.cli_environment(job, 'success') as getter:
                    self.assertEqual(cli.main(self.args(temp)), 1)
                    getter.assert_not_called()
                self.assertEqual(before, job.state_path.read_bytes())

    def test_successful_body_with_normal_return_timeout_preserves_completed_classification(self):
        with tempfile.TemporaryDirectory() as temp, self.stock():
            job, _ = fixture(Path(temp))
            with self.cli_environment(job, 'return_timeout'), patch.object(w.core, 'FINAL',
                    [w.core.sha(b'offline-final')] * 2):
                self.assertEqual(cli.main(self.args(temp)), 2)
            self.assertEqual(job.state['phase'], 'installed_pending_cold_start_usb_unconfirmed')
            self.assertIsNone(job.state['failed_action'])
            with self.assertRaises(ValueError): retry.review_entry_failure(job)

    def test_candidate_failure_battery_and_missing_opt_in_block_before_usb(self):
        with tempfile.TemporaryDirectory() as temp, self.stock():
            job, _ = fixture(Path(temp))
            for change in ('candidate', 'battery', 'opt_in'):
                with self.subTest(change=change), self.cli_environment(job, 'success') as getter:
                    args = self.args(temp)
                    if change == 'candidate': job.validate_candidates.side_effect = ValueError('candidate mismatch')
                    if change == 'battery': args[args.index('61')] = '49'
                    if change == 'opt_in': args.remove('--retry-service-entry')
                    self.assertEqual(cli.main(args), 1)
                    getter.assert_not_called(); job.module.assert_not_called()
                self.assertEqual(job.state['phase'], 'needs_review')


if __name__ == '__main__':
    unittest.main()
