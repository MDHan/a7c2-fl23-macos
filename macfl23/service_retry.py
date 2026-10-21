"""Explicit install retry only after a proven service-entry timeout.

Old workspaces have no entry marker, so inspect the original traceback as well
as the complete journal. Never infer a safe retry from needs_review alone.
"""
from datetime import datetime
import json
import re

from .session import core, MacSession
from .workflow import write_new

ERROR = 'UsbWaitTimeout: Expected USB identity did not appear: 0336'
RETRY_STEP = 'explicit_service_entry_install_retry'
EMPTY_EXIT = {'errors': []}
NORMAL_EXIT = {'service_exit_request': 'sent', 'normal_usb_returned': True, 'errors': []}


def require(condition, message):
    if not condition:
        raise ValueError('不能重试安装：' + message)


def entry_traceback(raw):
    """Recognize only the four-frame entry failure, including Python 3.14 carets."""
    text = raw.decode('utf-8')
    lines = text.strip().splitlines()
    require(text.count('Traceback (most recent call last):') == 1 and
            lines[-1] == 'macfl23.session.' + ERROR, '报错文件不是进入服务模式超时')
    frames = []
    for i, line in enumerate(lines):
        match = re.fullmatch(r'\s*File "([^"]+)", line \d+, in (\S+)\s*', line)
        if match:
            require(i + 1 < len(lines), '报错堆栈不完整')
            code = re.sub(r'\s+', '', lines[i + 1]).replace('"', "'")
            frames.append((match[1].replace('\\', '/'), match[2], code))
    require(len(frames) == 4, '报错不在已知的安装入口')
    first, install, enter, wait = frames
    require(first[0].endswith('/macfl23/cli.py') and first[1] == 'run_camera' and first[2] in (
            'job.install(args.battery_percent)', 'install_after_entry_timeout(job,args.battery_percent)'),
            '缺少安装命令的原始堆栈')
    legacy = (install[0].endswith('/a7c2_fl23_preview/app.py') and install[1] == 'install' and
              install[2] == "withSession(self.state['identity'])assession:")
    retry = (install[0].endswith('/macfl23/service_retry.py') and
             install[1] == 'install_after_entry_timeout' and
             install[2] == "withMacSession(job.state['identity'])assession:")
    require(legacy or retry, '安装主体已经开始或堆栈不匹配')
    require(enter[0].endswith('/macfl23/session.py') and enter[1:] == (
            '__enter__', 'self.dev=wait_pid(0x0336,self.location)') and
            wait[0].endswith('/macfl23/session.py') and wait[1] == 'wait_pid' and
            wait[2].startswith('raiseUsbWaitTimeout('), '不是服务入口的等待超时')


def review_entry_failure(job):
    """Offline gate. Caller has already validated backup and candidate hashes."""
    try:
        state = job.state
        require(state.get('phase') == 'needs_review' and state.get('failed_action') == 'install' and
                state.get('failed_during') in ('prepared', 'prepared_usb_unconfirmed') and
                state.get('last_error') == ERROR, '仅支持准备完成后、尚未进入安装主体的 0336 超时')
        names = {name + '.bin' for name in core.PROGRAMS} | {
            'backup.private.bin', 'backup-stored.private.bin'} | {
            'unit-%08x.bin' % key for key in core.CP_KEYS} | {
            'ap-%08x.bin' % key for key in core.AP_KEYS}
        require(set(state['backup_files']) == names, '原始备份清单不完整')
        require(all(core.sha((job.root / 'original' / (name + '.bin')).read_bytes()) == expected
                    for name, expected in zip(core.PROGRAMS, core.ORIGINAL)), '备份程序不是完整原版')
        require(not list(job.root.glob('*attempt.private.json')) and
                not list(job.root.glob('diagnostic-*')), '已有写入或专项处理记录，需单独核对')
        rows = state['journal']
        require(isinstance(rows, list) and bool(rows), '缺少操作日志')
        times = [datetime.fromisoformat(row['time']) for row in rows]
        require(all(a <= b for a, b in zip(times, times[1:])), '日志时间顺序不明')
        # Preparation is read-only except for temporary native modules. Its
        # exact prefix excludes earlier installs/restores and hand-reset phases.
        require(len(rows) >= len(names) + 8, '准备与失败记录不完整')
        backups = rows[:len(names)]
        require({row.get('name') for row in backups} == names and all(
            row.get('step') == 'backup_verified' and row.get('sha256') == state['backup_files'][row['name']]
            for row in backups), '原始备份审计不匹配')
        preflight = rows[len(names):len(names) + 6]
        require([(r.get('step'), r.get('module'), r.get('mode')) for r in preflight] == [
            (step, module, mode) for module, mode in (('probe', None), ('installer', 0), ('wbi', 0))
            for step in ('native_about_to_run', 'native_verified')], '准备期间存在其他操作或预检查未完成')
        boundary = len(names) + 6
        prepared_exit = rows[boundary]
        require(prepared_exit.get('step') == 'mac_usb_session_exit', '缺少准备结束记录')
        result = prepared_exit.get('result')
        known_timeout = (result == {
            'service_exit_request': 'sent', 'errors': [
                'normal USB return: Expected USB identity did not appear: 0e8a']})
        require(result == NORMAL_EXIT or known_timeout, '准备会话退出结果未知')
        tail = rows[boundary + 1:]
        # First failure came from the old install command. Each later attempt
        # must have its explicit review and an entry failure, with no body I/O.
        exits = [boundary + 1]
        previous_reviews = []
        require(tail[0].get('step') == 'mac_usb_session_exit' and tail[0].get('result') == EMPTY_EXIT,
                '首次失败并非服务模式进入前停止')
        require(len(tail) % 2 == 1, '上次重试未完整结束')
        for offset in range(1, len(tail), 2):
            marker, exit_row = tail[offset:offset + 2]
            require(marker.get('step') == RETRY_STEP and exit_row.get('step') == 'mac_usb_session_exit'
                    and exit_row.get('result') == EMPTY_EXIT, '存在安装主体操作或其他后续失败')
            review = marker['review']
            snapshot = job.root / review['state_snapshot']
            require(snapshot.parent == job.root / 'evidence' and
                    core.sha(snapshot.read_bytes()) == review['state_sha256'], '上次重试的原状态归档不匹配')
            saved = json.loads(snapshot.read_bytes())
            require(saved['journal'] == rows[:boundary + 1 + offset] and
                    saved.get('phase') == 'needs_review' and
                    saved.get('failed_during') == state['failed_during'] and
                    saved.get('identity') == state.get('identity') and
                    saved.get('features') == state.get('features') and
                    saved.get('backup_files') == state['backup_files'], '上次重试的记录与当前目录不一致')
            previous_reviews.append((review, len(exits)))
            exits.append(boundary + 1 + offset + 1)
        errors = []
        for path in (job.root / 'evidence').glob('*-error.txt'):
            stamp = datetime.strptime(path.name.removesuffix('-error.txt'), '%Y%m%d-%H%M%S-%f')
            # An earlier preparation error is inconsistent with this prefix.
            require(stamp >= times[boundary], '存在未核对的早期失败记录')
            errors.append((stamp, path))
        require(len(errors) == len(exits), '失败日志缺失或另有失败，不能只选旧报错')
        evidence = {}
        for index in exits:
            upper = times[index + 1] if index + 1 < len(rows) else datetime.max
            matches = [path for stamp, path in errors if times[index] <= stamp <= upper]
            require(len(matches) == 1, '失败记录与会话时间不匹配')
            path = matches[0]
            raw = path.read_bytes()
            entry_traceback(raw)
            evidence[path.name] = core.sha(raw)
        for prior, count in previous_reviews:
            require(prior.get('entry_failures') == count and prior.get('error_files') ==
                    dict(list(evidence.items())[:count]), '上次审查采用的失败记录已改变')
        return {'failed_during': state['failed_during'], 'previous_error': state['last_error'],
                'error_files': evidence, 'entry_failures': len(exits)}
    except (KeyError, TypeError, IndexError, OSError, UnicodeError) as exc:
        raise ValueError('不能重试安装：记录缺失或损坏') from exc


def archive_review(job, review):
    """Preserve the complete previous state before appending a retry audit."""
    raw = job.state_path.read_bytes()
    require(json.loads(raw) == job.state, '原状态在核对后发生变化')
    name = datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-install-entry-retry-state.private.json'
    write_new(job.root / 'evidence' / name, raw)
    job.audit(RETRY_STEP, review={**review, 'state_snapshot': 'evidence/' + name,
                                 'state_sha256': core.sha(raw)})
    print('上次仅在进入服务模式时超时，原状态已归档；本次只尝试连接一次，通过原版核对后才安装。', flush=True)


def install_after_entry_timeout(job, battery):
    """Pinned install sequence with an extra stock readback before any write.

    Keep needs_review until writing_programs: never persist a fake prepared
    phase. No retry loop; each invocation makes exactly one entry attempt.
    """
    with MacSession(job.state['identity']) as session:
        job.files(session, core.ORIGINAL)
        originals, _ = job.units()
        current = core.backup_records(session.call(session.camera.getBackupPresetData, True))
        require(not any(current[1][k].data != v for k, v in originals.items()) and
                not any(current[0][k].data != b'\0' for k in core.AP_KEYS), '机身目标设置已改变')
        job.module(session, 'wbi', 0)
        job.save('writing_programs')
        print('正在写入固定窗口；请勿拔线或关机。', flush=True)
        job.module(session, 'installer', 1)
        job.save('programs_written')
        job.files(session, core.FINAL)
        job.save('writing_settings')
        job.settings(session)
        job.save('settings_verified')
        job.module(session, 'wbi', 1)
    job.save('installed_pending_cold_start')
    print('安装及读回完成。按说明正常关机、取电池冷启动，再验证。', flush=True)
