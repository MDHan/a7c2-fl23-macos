"""One-shot WBI completion for the exact settings interruption in issue #2.

No firmware installer, CP/AP write, setting-save call or global counter
whitelist change. Historical evidence and fresh device state must both match.
"""
from datetime import datetime
import json
from pathlib import Path
import struct

from .diagnostic import DiagnosticJob, DiagnosticSession
from .session import app, core
from .workflow import MacJob, write_new
from . import ntsc

MENU_SPANS = {0x59892b8: 40, 0x5989370: 40}
SPANS = {0x3528804: 112, 0x4b2ed19: 2, **MENU_SPANS, 0x4b4a5e4: 12, 0x4b3b544: 12}
KEY = 0x02770015
ERRORS = {'ValueError: 發現額外設定變動：chip0/0x2770015，請保存紀錄後檢查',
          'ValueError: 发现额外设置变动：chip0/0x2770015，请保存记录后检查'}
MARKER = 'finish-attempt.private.json'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def review_records(before, after, stored, expected):
    """Reject every unaccounted change; scope the observed +60 to this review."""
    observed = False
    for chip in (0, 1):
        require(set(before[chip]) == set(after[chip]) == set(stored[chip]), '历史设置键集合不一致')
        for key, old in before[chip].items():
            new = after[chip][key]
            if (chip, key) in expected:
                saved = stored[chip][key]
                require(new.data == saved.data == expected[chip, key]
                        and old.attr == new.attr == saved.attr, '历史目标保存值或属性不匹配')
                continue
            if old.data == new.data and old.attr == new.attr:
                continue
            if core.runtime_counter(chip, key, old, new):
                continue
            if chip == 0 and key == KEY:
                require(old.attr == new.attr == 2 and len(old.data) == len(new.data) == 4
                        and int.from_bytes(new.data, 'little') - int.from_bytes(old.data, 'little') == 60,
                        '历史额外变化不是已报告的固定 +60 模式')
                observed = True
                continue
            raise ValueError('存在其他历史设置变化，拒绝收尾')
    require(observed, '历史记录不是本专项处理的停止点')


def evidence_file(root, suffix, start, end):
    paths = []
    for p in (root / 'evidence').glob('*-' + suffix):
        stamp = p.name[:-len('-' + suffix)]
        try:
            when = datetime.strptime(stamp, '%Y%m%d-%H%M%S-%f')
        except ValueError:
            continue
        if start <= when <= end:
            paths.append(p)
    require(len(paths) == 1, '历史证据缺失或不唯一：' + suffix)
    return paths[0]


def review_diagnostic_recovery(source, failed_at):
    """Use the latest ordered session, never an arbitrary successful summary."""
    sessions = []
    try:
        for directory in source.root.glob('diagnostic-*'):
            # Missing/broken logs cannot be ordered safely; do not skip them.
            journal = json.loads((directory / 'diagnostic.private.json').read_text())['journal']
            require(isinstance(journal, list) and bool(journal), '诊断日志为空')
            times = [datetime.fromisoformat(row['time']) for row in journal]
            require(all(a <= b for a, b in zip(times, times[1:])), '诊断日志时间倒退')
            sessions.append((times[0], times[-1], directory, journal))
        require(bool(sessions), '缺少后续诊断证据')
        sessions.sort(key=lambda item: item[0])
        require(all(a[1] < b[0] for a, b in zip(sessions, sessions[1:])), '诊断会话时间重叠或不唯一')
        start, end, directory, rows = sessions[-1]
        require(start > failed_at, '诊断早于本次失败安装')
        require([r['step'] for r in rows] == [
            'diagnostic_source_bound', 'diagnostic_camera_verified',
            'host_full_file_readback_verified', 'native_about_to_run',
            'native_verified', 'mac_usb_session_exit'], '最近诊断不完整或缺少来源绑定')
        snapshot = directory / 'source-state.private.json'
        original_state = source.state_path.read_bytes()
        require(snapshot.read_bytes() == original_state
                and rows[0].get('source_state_sha256') == core.sha(original_state)
                and rows[0].get('identity') == rows[1].get('identity') == source.state['identity'],
                '诊断不属于当前失败状态或机身')
        require(rows[-1].get('result') == {
            'service_exit_request': 'sent', 'normal_usb_returned': True, 'errors': []},
            '最近诊断未正常退出并返回 MSC')
        summary_file = directory / 'diagnostic-summary.json'
        summary = json.loads(summary_file.read_text())
        require(isinstance(summary, dict) and summary.get('format') == 1
                and summary.get('purpose') == 'interrupted_install_diagnosis'
                and summary.get('status') == 'diagnosed'
                and summary.get('source_phase') == 'needs_review'
                and summary.get('firmware_or_settings_or_wbi_writes') is False
                and all(summary.get(k) is True for k in (
                    'body_completed', 'source_state_unchanged', 'normal_usb_returned',
                    'programs_match_final', 'runtime_disk_matches_candidate')),
                '最近诊断摘要未确认完整采集和正常返回')
        require(rows[2].get('hashes') == core.FINAL and all(
            (r.get('module'), r.get('mode')) == ('runtime', None) for r in rows[3:5]),
            '诊断不是完整程序读回和只读运行检查')
        evidence = [snapshot, summary_file, directory / 'diagnostic.private.json']
        for name, expected in zip(core.PROGRAMS, core.FINAL):
            paths = list((directory / 'readback').glob('*-' + name))
            require(len(paths) == 1 and core.sha(paths[0].read_bytes()) == expected,
                    '诊断完整程序读回证据缺失、不唯一或不匹配')
            evidence.extend(paths)
        runtime_path = evidence_file(directory, 'runtime.bin', datetime.fromisoformat(rows[3]['time']), end)
        runtime = core.parse_native('runtime', runtime_path.read_bytes())
        require(runtime == rows[4].get('result'), '诊断运行报告与日志不符')
        spans = runtime['spans']
        require(len(spans) == len(SPANS) and {r['offset']: r['length'] for r in spans} == SPANS,
                '诊断运行区域清单不符')
        candidate = (source.root / 'candidate/appFw.so').read_bytes()
        require(all(r['disk'] == candidate[r['offset']:r['offset'] + r['length']].hex() for r in spans),
                '诊断磁盘窗口不匹配')
        evidence.append(runtime_path)
        return {'directory': directory.name, 'evidence_hashes': {
            p.relative_to(directory).as_posix(): core.sha(p.read_bytes()) for p in evidence}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError('后续诊断证据不足；保留原记录，不能据摘要放行收尾') from exc


def review_history(source):
    source.validate_candidates()
    from . import integrated
    require(not integrated.enabled(source.state) and not ntsc.enabled(source.state),
            '收尾仅支持旧版 FL2/FL3，不支持新版或 NTSC 组合')
    state = source.state
    require((state.get('phase'), state.get('failed_action'), state.get('failed_during')) ==
            ('needs_review', 'install', 'writing_settings'), '不符合本专项的原始失败阶段')
    require(state.get('last_error') in ERRORS, '原始错误不是已报告的设置保持性检查')
    require(not (source.root / MARKER).exists(), '已有收尾尝试记录；结果未知时不允许重复执行')
    identity = state.get('identity', {})
    require(identity.get('model') == core.MODEL and len(identity.get('serial_sha256', '')) == 64,
            '原始机身身份无效')
    journal = state.get('journal', [])
    starts = [i for i, row in enumerate(journal) if row.get('step') == 'native_about_to_run'
              and row.get('module') == 'installer' and row.get('mode') == 1]
    require(bool(starts), '没有安装器开始证据')
    start = starts[-1]
    rows = journal[start:]
    require([(r.get('module'), r.get('mode')) for r in rows if r.get('step') == 'native_about_to_run']
            == [('installer', 1)], '安装后存在其他原生写入尝试，拒绝收尾')
    native = [r for r in rows if r.get('step') == 'native_verified']
    require(len(native) == 1 and native[0].get('module') == 'installer' and native[0].get('mode') == 1,
            '原安装器完成记录不唯一')
    require(any(r.get('step') == 'host_full_file_readback_verified' and r.get('hashes') == core.FINAL
                for r in rows), '没有完整程序读回通过的历史证据')
    exit_res = rows[-1].get('result', {}) if rows[-1].get('step') == 'mac_usb_session_exit' else {}
    start_time = datetime.fromisoformat(rows[0]['time'])
    end_time = datetime.fromisoformat(rows[-1]['time'])
    direct_return = (exit_res.get('service_exit_request') == 'sent'
                     and exit_res.get('normal_usb_returned') is True
                     and exit_res.get('errors') == [])
    timed_out = (exit_res.get('service_exit_request') == 'sent'
                 and exit_res.get('normal_usb_returned') is None
                 and exit_res.get('errors') == ['normal USB return: Expected USB identity did not appear: 0e8a'])
    require(direct_return or timed_out, '原失败会话不是正常返回或单一 MSC 等待超时')
    recovery = review_diagnostic_recovery(source, end_time) if timed_out else None
    require(not any(r.get('step') in ('settings_storage_verified', 'temporary_cleanup_pending') for r in rows),
            '原失败记录与本专项不符')
    installer = evidence_file(source.root, 'installer.bin', start_time, end_time)
    parsed = core.parse_native('installer', installer.read_bytes(), 1)
    require(parsed == native[0].get('result') and parsed.get('window_writes') == 40,
            '原安装器二进制结果与记录不匹配')
    paths = [evidence_file(source.root, 'settings-' + name + '.private.bin', start_time, end_time)
             for name in ('before', 'after', 'stored')]
    records = [core.backup_records(p.read_bytes()) for p in paths]
    _, merged = source.units()
    expected = {(1, k): v for k, v in merged.items()}
    expected.update({(0, k): bytes([10 + i]) for i, k in enumerate(core.AP_KEYS)})
    review_records(*records, expected)
    reviewed = {'scoped_review_key': '0x02770015', 'observed_delta': 60,
                'evidence_hashes': {p.name: core.sha(p.read_bytes()) for p in [installer, *paths]}}
    if recovery is not None:
        reviewed['diagnostic_recovery'] = recovery
    return reviewed


class FinishSession(DiagnosticSession):
    TEMP_NAMES = DiagnosticSession.TEMP_NAMES | {'a7c2.wbi.request', 'a7c2.wbi.result'}

    def __init__(self, session):
        super().__init__(session)
        self.wbi_mode = None
        self._wbi = (core.BASE / 'assets/wbi.so').read_bytes()

    def temp(self, name, data=None, remove=False):
        if self.wbi_mode is None:
            require(name not in {'a7c2.wbi.request', 'a7c2.wbi.result'}, 'WBI 尚未获准')
            return super().temp(name, data, remove)
        require(name in self.TEMP_NAMES, '收尾不允许安装器文件')
        if not remove:
            if name == 'a7c2.wbi.request':
                require(data == struct.pack('<8sII', b'A7WBI001', self.wbi_mode, 0), 'WBI 请求模式不符')
            else:
                require(name in {'libJit.so', 'a7c2-preview-staged.so'} and data == self._wbi,
                        '收尾只允许固定 WBI 模块')
        return self._session.temp(name, data, remove)


class FinishJob(DiagnosticJob):
    def __init__(self, root, source):
        super().__init__(root, source)
        self.wbi_permitted = False
        self.write_started = False
        self.wbi_preflight_verified = False
        self.wbi_completed = False

    def module(self, session, name, mode=None):
        if name == 'runtime':
            return super().module(session, name, mode)
        require(name == 'wbi' and mode in (0, 1) and self.wbi_permitted, '收尾检查尚未通过，或请求了安装器')
        if mode == 1:
            require(self.write_started and not self.wbi_completed, '收尾写入尚未登记或已经完成')
        session.wbi_mode = mode
        try:
            return app.Job.module(self, session, name, mode)
        finally:
            session.wbi_mode = None

    def check_current(self, session):
        self.inspect(session)
        summary = self.summary
        require(summary.get('programs_match_final') is True and summary.get('image_mode') == '2'
                and summary.get('image_backend_expected') is True, '当前程序或快照起始状态不符')
        checks = summary.get('target_checks', [])
        require(len(checks) == 4 and all(all(row.get(k) is True for k in
                ('direct_matches', 'live_matches', 'stored_matches', 'attributes_match_original')) for row in checks),
                '当前四项目标参数或属性不匹配')
        paths = sorted((self.root / 'evidence').glob('*-runtime.bin'))
        runtime = core.parse_native('runtime', paths[-1].read_bytes())
        require(len(runtime['spans']) == 6 and {r['offset']: r['length'] for r in runtime['spans']} == SPANS,
                '运行区域清单不匹配')
        original = (self.source.root / 'original/appFw.so.bin').read_bytes()
        candidate = (self.source.root / 'candidate/appFw.so').read_bytes()
        for span in runtime['spans']:
            offset, length = span['offset'], span['length']
            old, new = original[offset:offset + length].hex(), candidate[offset:offset + length].hex()
            require(span['disk'] == new, '运行诊断中的磁盘窗口不匹配')
            require(span['memory'] == new or (offset in MENU_SPANS and span['memory'] == old),
                    '存在未识别的运行内存，拒绝收尾')
        self.wbi_permitted = True
        self.module(session, 'wbi', 0)
        self.wbi_preflight_verified = True

    def complete_snapshot(self, session, reviewed):
        require(self.wbi_permitted and self.wbi_preflight_verified, '当前状态检查未完成')
        marker = json.dumps({'format': 1, 'operation': 'finish-install', 'reviewed': reviewed}, ensure_ascii=False).encode()
        write_new(self.source.root / MARKER, marker)
        # Both files are saved before any WBI mutation. No automatic second try.
        self.write_started = True
        self.source.audit('reviewed_finish_about_to_change_wbi', review=reviewed, evidence_directory=self.root.name)
        self.source.save('finishing_snapshot')
        self.module(session, 'wbi', 1)
        self.wbi_completed = True
        self.audit('reviewed_wbi_finish_completed')


def run_finish(workspace):
    import os
    import subprocess
    import sys
    import tempfile
    import traceback
    from .cli import camera_lock, return_ownership, save_failure
    from .messages import simplify_message
    from .session import MacSession, NormalReturnTimeout

    with camera_lock():
        source = MacJob(workspace)
        reviewed = review_history(source)  # No USB activity before this passes.
        original_state = source.state_path.read_bytes()
        previous_umask, previous_audit = os.umask(0o077), MacSession.audit
        job = wake = None
        status, result, exit_report = 'incomplete', 1, {}
        try:
            root = Path(tempfile.mkdtemp(prefix='finish-', dir=workspace))
            write_new(root / 'source-state.private.json', original_state)
            job = FinishJob(root, source)
            def audit(row):
                exit_report.update(row)
                job.audit('mac_usb_session_exit', result=row)
            MacSession.audit = audit
            wake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())])
            print('实验性收尾：尚未真机验证；仅处理已核对的 +60 停止点，不重刷程序或保存参数。', flush=True)
            print('将重新检查现状；通过后执行上游 WBI 收尾。开始后保持连接，不自行断电。', flush=True)
            with MacSession(source.state['identity']) as session:
                guarded = FinishSession(session)
                job.check_current(guarded)
                job.complete_snapshot(guarded, reviewed)
            require(job.wbi_completed and exit_report.get('service_exit_request') == 'sent'
                    and exit_report.get('normal_usb_returned') is True and exit_report.get('errors') == [],
                    '收尾主体或正常 USB 返回未完整确认')
            status, result = 'installed_pending_cold_start', 0
        except NormalReturnTimeout as exc:
            if (job is not None and job.wbi_completed and exit_report.get('service_exit_request') == 'sent'
                    and len(exit_report.get('errors', [])) == 1):
                status, result = 'installed_pending_cold_start_usb_unconfirmed', 2
            else:
                if job is not None and job.write_started:
                    save_failure(source, 'finish-install', exc)
                print('STOPPED: 收尾完成状态未确认，不允许重试', file=sys.stderr)
        except BaseException as exc:
            if job is not None:
                (job.root / 'error.private.txt').write_text(traceback.format_exc())
                if job.write_started:
                    save_failure(source, 'finish-install', exc)
            print('STOPPED: ' + simplify_message(exc), file=sys.stderr)
        finally:
            MacSession.audit = previous_audit
            try:
                if wake is not None:
                    wake.terminate()
                    wake.wait(timeout=5)
                if job is not None:
                    if result in (0, 2):
                        source.audit('reviewed_finish_completed', evidence_directory=job.root.name,
                                     normal_usb_returned=exit_report.get('normal_usb_returned') is True)
                        source.save(status, failed_action=None, failed_during=None, last_error=None,
                                    normal_usb_returned=exit_report.get('normal_usb_returned') is True,
                                    exit_warning=None if result == 0 else 'normal_usb_return_unconfirmed',
                                    functional_test='manual_check_required')
                    job.summary.update(purpose='reviewed_snapshot_finish', status=status,
                                       wbi_finish_verified=job.wbi_completed,
                                       normal_usb_returned=exit_report.get('normal_usb_returned') is True,
                                       firmware_or_settings_writes=False,
                                       wbi_write_attempted=job.write_started,
                                       source_state_unchanged=source.state_path.read_bytes() == original_state,
                                       next_action='cold_start_and_verify' if result in (0, 2) else 'stop_no_retry')
                    # The inherited field belongs to diagnose, whose WBI writes
                    # are forbidden. This operation intentionally changes WBI.
                    job.summary.pop('firmware_or_settings_or_wbi_writes', None)
                    core.save_json(job.root / 'finish-summary.json', job.summary)
                    print('RESULT: ' + status, flush=True)
                    print(json.dumps(job.summary, ensure_ascii=False, indent=2))
                    if result in (0, 2):
                        print('确认相机无错误、存取灯熄灭后，按文档正常关机、取电池约10秒，再开机并验证。')
                    else:
                        print('未确认完成；不重跑收尾、不手改状态，保留原工作目录。')
            finally:
                return_ownership([workspace])
                os.umask(previous_umask)
        return result
