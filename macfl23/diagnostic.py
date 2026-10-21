"""Inspect a settings-stage interruption without advancing installation state.

Enters service mode and executes the pinned temporary runtime reader. This is
not zero-operation USB inspection. No installer, settings save or WBI mutation
is available through the diagnostic session interface.
"""
from pathlib import Path

from .session import app, core
from .workflow import write_new


class DiagnosticSession:
    TEMP_NAMES = {'libJit.so', 'a7c2-preview-staged.so',
                  'a7c2.runtime.result', 'a7c2.probe.result'}
    READ_PATHS = {'/tmp/jiritsu/../../cp/cpapp-b.bin',
                  '/tmp/jiritsu/../../usr/lib/appFw.so',
                  '/tmp/jiritsu/../../etc/ld.so.preload'}

    def __init__(self, session):
        self._session = session
        self.camera = session.camera
        self._runtime = (core.BASE / 'assets/runtime.so').read_bytes()

    @property
    def healthy(self):
        return self._session.healthy

    def read(self, path, missing=False):
        if path not in self.READ_PATHS | {'/tmp/jiritsu/' + n for n in self.TEMP_NAMES}:
            raise ValueError('诊断不允许读取此路径')
        return self._session.read(path, missing)

    def call(self, method, *args):
        permitted = ((method == self.camera.getBackupPresetData and args in ((True,), (False,)))
                     or (method == self.camera.readBackup and len(args) == 1 and args[0] in core.AP_KEYS))
        if not permitted:
            raise ValueError('诊断不允许此相机命令')
        return self._session.call(method, *args)

    def cp_read(self, key):
        if key not in core.CP_KEYS:
            raise ValueError('诊断不允许此 CP 键')
        return self._session.cp_read(key)

    def temp(self, name, data=None, remove=False):
        if name not in self.TEMP_NAMES:
            raise ValueError('诊断仅允许 runtime 临时文件')
        if not remove and (name not in {'libJit.so', 'a7c2-preview-staged.so'} or data != self._runtime):
            raise ValueError('诊断仅允许固定版本 runtime 模块')
        return self._session.temp(name, data, remove)

    def cp_write(self, *args):
        raise ValueError('诊断禁止写入 CP 参数')

    def ap_write(self, *args):
        raise ValueError('诊断禁止写入 AP 参数')


class DiagnosticJob(app.Job):
    def __init__(self, directory, source):
        super().__init__(directory)
        self.source = source
        self.state = {'journal': []}
        self.state_path = self.root / 'diagnostic.private.json'
        self.summary = {'format': 1, 'purpose': 'interrupted_install_diagnosis',
                        'status': 'incomplete', 'source_phase': source.state['phase'],
                        'source_state_unchanged': None, 'normal_usb_returned': False,
                        'firmware_or_settings_or_wbi_writes': False}

    def save(self, *args, **kwargs):
        raise ValueError('诊断不能推进安装阶段')

    def module(self, session, name, mode=None):
        if name != 'runtime' or mode is not None:
            raise ValueError('诊断只允许 runtime 模块，不允许 installer 或 WBI 模块')
        return super().module(session, name)

    def inspect(self, session):
        # Full fingerprints must match the installed candidate before executing
        # even a temporary runtime reader. A mismatch remains an incomplete run.
        self.files(session, core.FINAL)
        self.summary['programs_match_final'] = True
        live_raw = session.call(session.camera.getBackupPresetData, True)
        stored_raw = session.call(session.camera.getBackupPresetData, False)
        write_new(self.root / 'settings-live.private.bin', live_raw)
        write_new(self.root / 'settings-stored.private.bin', stored_raw)
        live, stored = core.backup_records(live_raw), core.backup_records(stored_raw)
        original = core.backup_records((self.source.root / 'original/backup.private.bin').read_bytes())
        _, merged = self.source.units()
        expected = {(1, k): v for k, v in merged.items()}
        expected.update({(0, k): bytes([10 + i]) for i, k in enumerate(core.AP_KEYS)})
        checks = []
        for (chip, key), value in expected.items():
            current = session.cp_read(key) if chip else session.call(session.camera.readBackup, key)
            before, now, saved = original[chip].get(key), live[chip].get(key), stored[chip].get(key)
            checks.append({'chip': chip, 'key': '0x%08x' % key,
                           'direct_matches': current == value,
                           'live_matches': now is not None and now.data == value,
                           'stored_matches': saved is not None and saved.data == value,
                           'attributes_match_original': (before is not None and now is not None and saved is not None
                                                         and before.attr == now.attr == saved.attr)})
        self.summary['target_checks'] = checks
        runtime = self.module(session, 'runtime')
        candidate = (self.source.root / 'candidate/appFw.so').read_bytes()
        regions = []
        for span in runtime['spans']:
            expected_hex = candidate[span['offset']:span['offset'] + span['length']].hex()
            regions.append({'offset': '0x%x' % span['offset'], 'length': span['length'],
                            'memory_matches_candidate': span['memory'] == expected_hex,
                            'disk_matches_candidate': span['disk'] == expected_hex})
        self.summary['runtime_regions'] = regions
        self.summary['runtime_memory_matches_candidate'] = all(r['memory_matches_candidate'] for r in regions)
        self.summary['runtime_disk_matches_candidate'] = all(r['disk_matches_candidate'] for r in regions)
        # Upstream's parsed result omits image_dev; extract only the fixed ABI
        # fields needed for this diagnostic. Keep the raw report private.
        paths = sorted((self.root / 'evidence').glob('*-runtime.bin'))
        raw = paths[-1].read_bytes()
        mode = runtime['image_mode'].strip()
        backend = raw[1608:1640].split(b'\0')[0]
        self.summary['image_mode'] = mode if mode in ('0', '2') else 'unrecognized'
        self.summary['image_backend_expected'] = backend == b'*wbi4\n'
        self.summary['body_completed'] = True
        # No completion claim: diagnostics cannot approve a resume or recovery.
        self.summary['next_action'] = 'maintainer_review_required'

    def finish(self, status, source_state_unchanged, normal_usb_returned):
        self.summary.update(status=status, source_state_unchanged=source_state_unchanged,
                            normal_usb_returned=normal_usb_returned)
        core.save_json(self.root / 'diagnostic-summary.json', self.summary)
        return self.summary


def run_diagnosis(workspace):
    """CLI entry after macOS/root/battery/path checks; preserve source state."""
    import json
    import os
    import subprocess
    import sys
    import tempfile
    import traceback
    from .cli import camera_lock, return_ownership
    from .messages import simplify_message
    from .session import MacSession, NormalReturnTimeout
    from .workflow import MacJob

    with camera_lock():
        source = MacJob(workspace)
        source.validate_candidates()
        from . import ntsc
        from . import integrated
        if integrated.enabled(source.state) or ntsc.enabled(source.state):
            raise ValueError('专项 diagnose 仅支持旧版 FL2/FL3，暂不支持新版两类配置；未开始USB操作')
        if (source.state.get('phase'), source.state.get('failed_action'), source.state.get('failed_during')) != (
                'needs_review', 'install', 'writing_settings'):
            raise ValueError('专项诊断仅适用于 install 在 writing_settings 停止的原始工作目录')
        identity = source.state.get('identity', {})
        if identity.get('model') != core.MODEL or len(identity.get('serial_sha256', '')) != 64:
            raise ValueError('原始工作目录缺少有效的机身身份摘要')
        saved_state = source.state_path.read_bytes()
        old_umask = os.umask(0o077)
        old_audit = MacSession.audit
        job = wake = None
        status, result = 'incomplete', 1
        exit_report = {}
        try:
            directory = Path(tempfile.mkdtemp(prefix='diagnostic-', dir=workspace))
            job = DiagnosticJob(directory, source)
            # Bind private evidence to the exact failed installation before USB.
            write_new(job.root / 'source-state.private.json', saved_state)
            job.audit('diagnostic_source_bound', source_state_sha256=core.sha(saved_state),
                      identity=identity)
            def audit(row):
                exit_report.update(row)
                job.audit('mac_usb_session_exit', result=row)
            MacSession.audit = audit
            wake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())])
            print('实验性专项诊断：尚未真机验证；将进入服务模式并运行临时 runtime 模块。', flush=True)
            print('不写固件、保存参数或 WBI；诊断不等于安装完成，不自动续写。', flush=True)
            with MacSession(identity) as session:
                job.audit('diagnostic_camera_verified', identity=session.identity)
                job.inspect(DiagnosticSession(session))
            status, result = 'diagnosed', 0
        except NormalReturnTimeout:
            status, result = 'diagnosed_usb_unconfirmed', 2
        except BaseException as exc:
            print('STOPPED: ' + simplify_message(exc), file=sys.stderr)
            if job is not None:
                (job.root / 'error.private.txt').write_text(traceback.format_exc())
        finally:
            MacSession.audit = old_audit
            try:
                if wake is not None:
                    wake.terminate()
                    wake.wait(timeout=5)
                if job is not None:
                    unchanged = source.state_path.read_bytes() == saved_state
                    if not unchanged:
                        status, result = 'incomplete', 1
                    if status == 'diagnosed_usb_unconfirmed' and (not job.summary.get('body_completed')
                                                                 or exit_report.get('service_exit_request') != 'sent'
                                                                 or len(exit_report.get('errors', [])) != 1):
                        status, result = 'incomplete', 1
                    if status == 'diagnosed' and (not job.summary.get('body_completed')
                                                  or exit_report.get('service_exit_request') != 'sent'
                                                  or exit_report.get('normal_usb_returned') is not True
                                                  or exit_report.get('errors')):
                        status, result = 'incomplete', 1
                    summary = job.finish(status, unchanged, exit_report.get('normal_usb_returned') is True)
                    print('RESULT: ' + status, flush=True)
                    print(json.dumps(summary, ensure_ascii=False, indent=2))
                    print('可分享的脱敏摘要：' + str(job.root / 'diagnostic-summary.json'))
                    print('仅分享该摘要；其他诊断文件保留本地。请等待核对，不重跑安装或自行处理 WBI。')
            finally:
                if job is not None:
                    return_ownership([job.root])
                os.umask(old_umask)
        return result
