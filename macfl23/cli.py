"""Explicit CLI phases. No default action and no automatic write retries."""
import argparse
import contextlib
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import traceback

from . import __version__
from .engine import ROOT, setup, load
from .messages import simplify_message


@contextlib.contextmanager
def camera_lock():
    # /var/run is system-owned, so another user cannot plant the lock path.
    fd = os.open('/var/run/a7c2-fl23-macos.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            raise RuntimeError('Invalid camera lock file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('已有此工具的相机操作运行中；不会并行操作') from None
        yield
    finally:
        os.close(fd)


def private_path(value, existing=False):
    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('不能使用符号链接作为工作目录')
    path = path.resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('机身工作目录和备份必须放在项目仓库之外，防止误上传')
    if existing:
        if not path.is_dir() or path.stat().st_mode & 0o077:
            raise ValueError('工作目录必须存在，且权限仅限所属用户（0700）')
        if any(p.is_symlink() for p in path.rglob('*')):
            raise ValueError('工作目录内不允许符号链接')
    return path


def return_ownership(paths):
    if os.geteuid() != 0 or not os.environ.get('SUDO_UID'):
        return
    uid, gid = int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID'])
    for directory in paths:
        if not directory.exists() or directory.is_symlink():
            continue
        for path in [directory, *directory.rglob('*')]:
            if path.is_symlink():
                continue
            if path.stat().st_uid == 0:
                os.chown(path, uid, gid, follow_symlinks=False)


def parser():
    result = argparse.ArgumentParser(description='A7C II 2.01 FL2/FL3 · macOS experimental adapter')
    result.add_argument('--version', action='version', version=__version__)
    commands = result.add_subparsers(dest='action', required=True)
    for name in ('setup', 'check', 'usb-status'):
        commands.add_parser(name)
    for name in ('status', 'prepare', 'prepare-upgrade', 'upgrade', 'install', 'verify', 'restore', 'diagnose', 'finish-install', 'finish-upgrade'):
        command = commands.add_parser(name)
        command.add_argument('--workspace', required=True, type=Path)
        if name == 'prepare-upgrade':
            command.add_argument('--source-workspace', required=True, type=Path,
                                 help='已完成冷启动验证的原 FL2/FL3 目录；只读保留原目录')
        if name == 'upgrade':
            command.add_argument('--accept-experimental-upgrade', action='store_true',
                                 help='明确接受整合升级尚未实机验证；仅支持原 FL2/FL3 精确指纹')
        if name == 'prepare':
            command.add_argument('--backup-copy', type=Path, help='可选：另存一份副本；默认只保存工作目录中的一份备份')
        if name in ('prepare', 'prepare-upgrade'):
            command.add_argument('--disable-ntsc-warning', action='store_true',
                                 help='实验性选项：同时取消 NTSC 开机提示；仅单台组合版实测通过，默认只安装 FL2/FL3')
        if name in ('install', 'upgrade', 'restore', 'diagnose', 'finish-install', 'finish-upgrade'):
            command.add_argument('--battery-percent', required=True, type=int,
                                 help='相机屏幕显示的实际百分比，50–100；不能估算或填示例值')
        if name in ('verify', 'upgrade', 'restore'):
            command.add_argument('--cold-start-confirmed', action='store_true',
                                 help='已正常关机、等存取灯灭、取出电池约10秒、重新开机')
        if name == 'finish-install':
            command.add_argument('--accept-experimental-finish', action='store_true',
                                 help='明确接受未实机验证的专项收尾及已报告 +60 差异审查风险')
        if name == 'finish-upgrade':
            command.add_argument('--accept-reviewed-upgrade-finish', action='store_true',
                                 help='显式接受升级后未保存+60差异的有限审查及实验性WBI收尾')
            command.add_argument('--accept-unconfirmed-live-time-field', action='store_true',
                                 help='另行接受未知字段风险；仅容许当前字段增量与经过秒数相符，保存设置仍须完全一致')
        if name == 'restore':
            command.add_argument('--st-selected', action='store_true',
                                 help='冷启动前已将外观选为ST')
        if name == 'install':
            command.add_argument('--retry-service-entry', action='store_true',
                                 help='上次安装仅进入服务模式超时：核对原记录后明确重试一次，不改阶段或覆盖备份')
    return result


def check_phase(args, job):
    if args.action == 'prepare':
        return
    job.validate_candidates()
    phase = job.state['phase']
    if args.action == 'install' and getattr(args, 'retry_service_entry', False):
        from .service_retry import review_entry_failure
        return review_entry_failure(job)
    if args.action == 'verify' and phase == 'needs_review':
        review = job.review_failed_verify_before_cold_start()
        job.audit('explicit_read_only_reverify_after_cold_start', review=review)
        return
    allowed = {
        'install': {'prepared', 'prepared_usb_unconfirmed'},
        'upgrade': {'upgrade_prepared'},
        'verify': {'installed_pending_cold_start', 'installed_pending_cold_start_usb_unconfirmed',
                   'verified_after_cold_start', 'verified_after_cold_start_usb_unconfirmed'},
        'restore': {'installed_pending_cold_start', 'installed_pending_cold_start_usb_unconfirmed',
                    'verified_after_cold_start', 'verified_after_cold_start_usb_unconfirmed'},
    }
    if args.action == 'verify':
        from . import integrated
        if integrated.enabled(job.state):
            allowed['verify'] |= {'restored_pending_cold_start', 'restored_pending_cold_start_usb_unconfirmed',
                                  'restored_verified_after_cold_start', 'restored_verified_after_cold_start_usb_unconfirmed'}
    if phase not in allowed[args.action]:
        raise ValueError('当前阶段不允许此操作：' + phase + '；未知结果不允许自动续写')
    if args.action == 'install' and phase == 'prepared_usb_unconfirmed':
        # Only an explicitly invoked install can continue; identity and native
        # original hashes are rechecked by the normal upstream install path.
        job.audit('explicit_install_after_verified_preflight_exit_timeout')
        job.save('prepared')


def run_camera(args):
    if platform.system() != 'Darwin':
        raise RuntimeError('相机操作只支持 macOS')
    if os.geteuid() != 0:
        raise RuntimeError('请在自己的终端使用 sudo 运行相机操作')
    if args.action in ('install', 'upgrade', 'restore', 'diagnose', 'finish-install', 'finish-upgrade') and not 50 <= args.battery_percent <= 100:
        raise ValueError('相机实际电量必须至少50%；未开始USB操作')
    if args.action in ('verify', 'upgrade', 'restore') and not args.cold_start_confirmed:
        raise ValueError('请先按文档完成冷启动，再使用 --cold-start-confirmed')
    if args.action == 'restore' and not args.st_selected:
        raise ValueError('恢复前须选择ST，再完成冷启动；需要 --st-selected')
    if args.action == 'upgrade' and not args.accept_experimental_upgrade:
        raise ValueError('整合升级仍为实验流程；需要 --accept-experimental-upgrade')
    workspace = private_path(args.workspace, existing=args.action != 'prepare')
    if args.action == 'finish-upgrade':
        if not args.accept_reviewed_upgrade_finish:
            raise ValueError('需明确接受升级差异审查及实验性WBI收尾')
        from .finish_upgrade import run_finish_upgrade
        return run_finish_upgrade(workspace, accept_live_time_field=args.accept_unconfirmed_live_time_field)
    if args.action == 'finish-install':
        if not args.accept_experimental_finish:
            raise ValueError('专项收尾尚未真机验证；需显式指定 --accept-experimental-finish')
        from .finish import run_finish
        return run_finish(workspace)
    if args.action == 'diagnose':
        from .diagnostic import run_diagnosis
        return run_diagnosis(workspace)
    second = private_path(args.backup_copy) if args.action == 'prepare' and args.backup_copy else None
    from .session import MacSession, NormalReturnTimeout, transport
    from .workflow import MacJob
    if args.action == 'upgrade':
        from .upgrade import UpgradeJob
        job = UpgradeJob(workspace)
    else:
        job = MacJob(workspace)
    owned = [workspace] + ([second] if second else [])
    with camera_lock():
        previous_umask = os.umask(0o077)
        wake = None
        started = False
        original = transport.Session
        try:
            entry_retry = check_phase(args, job)
            if args.action == 'install':
                from . import ntsc
                if ntsc.enabled(job.state):
                    print('按已保存选择安装 FL2/FL3 + 取消 NTSC 开机提示；组合方案尚未实机验证。', flush=True)
                else:
                    print('按已保存选择仅安装 FL2/FL3。', flush=True)
            wake = subprocess.Popen(['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid())])
            transport.Session = MacSession
            MacSession.audit = lambda row: job.audit('mac_usb_session_exit', result=row)
            if args.action == 'install' and entry_retry is not None:
                from .service_retry import archive_review
                archive_review(job, entry_retry)
                job._entry_retry_phase = entry_retry['failed_during']
            started = True
            if args.action == 'prepare':
                if second and workspace.parent.stat().st_dev == second.parent.stat().st_dev:
                    print('提示：两份备份位于同一文件系统，不能抵御该磁盘故障；建议第二份放外置磁盘。')
                if getattr(args, 'disable_ntsc_warning', False):
                    print('已选择 FL2/FL3 + 取消 NTSC 开机提示；此组合尚未实机验证。', flush=True)
                    job.prepare(second, disable_ntsc_warning=True)
                else:
                    print('已选择仅 FL2/FL3；保留原有 NTSC 开机提示。', flush=True)
                    job.prepare(second)
            elif args.action == 'install':
                if entry_retry is not None:
                    from .service_retry import install_after_entry_timeout
                    install_after_entry_timeout(job, args.battery_percent)
                else:
                    job.install(args.battery_percent)
            elif args.action == 'upgrade':
                print('实验性升级：按所选版本追加固定 AP 窗口，不重写拍摄设置；完成后必须冷启动验证。', flush=True)
                job.upgrade(args.battery_percent)
            elif args.action == 'verify':
                restored = job.state['phase'].startswith('restored_')
                job.verify()
                job.save('restored_verified_after_cold_start' if restored else 'verified_after_cold_start',
                         functional_test='manual_check_required', failed_action=None, failed_during=None, last_error=None)
            else:
                job.restore(args.battery_percent)
            cleared = dict(failed_action=None, failed_during=None, last_error=None) if (
                args.action == 'install' and entry_retry is not None) else {}
            job.save(job.state['phase'], normal_usb_returned=True, exit_warning=None, **cleared)
            print('RESULT: ' + job.state['phase'], flush=True)
            if args.action == 'prepare':
                print('备份和机内预检查完成；尚未写入固件或保存拍摄设置。')
            return 0
        except NormalReturnTimeout as exc:
            # The exception can only arise after the complete with-body and a
            # successful auth.stop(). Any body/stop/cleanup error uses the other
            # branch, which never permits continuation.
            try:
                phase = job.confirm_completed_body(args.action)
            except Exception:
                save_failure(job, args.action, exc)
                raise
            cleared = dict(failed_action=None, failed_during=None, last_error=None) if (
                args.action == 'install' and entry_retry is not None) else {}
            job.save(phase, normal_usb_returned=False, exit_warning=str(exc), **cleared)
            print('RESULT: ' + phase, flush=True)
            print('操作主体已验证，但普通USB返回未确认。没有自动重试写入。')
            if args.action in ('install', 'upgrade', 'restore'):
                print('相机无错误且存取灯熄灭后，按文档正常关机并取电池冷启动；不要重跑安装。')
            elif args.action == 'prepare':
                print('尚未写入固件。先确认相机回到影像传输（MSC），再明确运行 install。')
            else:
                print('程序、参数和运行区域核验已完成；仍需确认相机连接/菜单/拍摄状态。')
            return 2
        except BaseException as exc:
            if started and hasattr(job, 'state'):
                save_failure(job, args.action, exc)
            raise
        finally:
            MacSession.audit = None
            transport.Session = original
            if wake:
                wake.terminate()
                wake.wait(timeout=5)
            return_ownership(owned)
            os.umask(previous_umask)


def save_failure(job, action, exc):
    phase = job.state.get('phase')
    if action == 'install' and phase == 'needs_review' and '_entry_retry_phase' in job.__dict__:
        phase = job._entry_retry_phase
    if action == 'verify' and phase == 'needs_review' and job.state.get('failed_action') == 'verify':
        phase = job.state.get('failed_during')
    job.save('needs_review', failed_during=phase, failed_action=action,
             last_error=type(exc).__name__ + ': ' + str(exc))
    evidence = job.root / 'evidence'
    evidence.mkdir(exist_ok=True, mode=0o700)
    (evidence / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-error.txt')).write_text(traceback.format_exc())


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == 'setup':
            if os.geteuid() == 0:
                raise RuntimeError('setup 不需要 sudo；请以普通用户安装环境')
            setup()
            print('固定版本上游下载与全部文件哈希核验通过；未连接相机。')
        elif args.action == 'check':
            _, core, _ = load()
            core.assets()
            from . import ntsc
            ntsc.assets()
            from . import integrated
            integrated.BASE.assets()
            integrated.COMBINED.assets()
            print('套件源码和资产完整；未连接相机。')
        elif args.action == 'prepare-upgrade':
            from .upgrade import prepare_upgrade
            source = private_path(args.source_workspace, existing=True)
            target = private_path(args.workspace)
            if source == target or source in target.parents or target in source.parents:
                raise ValueError('升级目录须与原目录分开，不能互相包含')
            prepare_upgrade(source, target, args.disable_ntsc_warning)
        elif args.action == 'usb-status':
            if platform.system() != 'Darwin':
                raise RuntimeError('USB系统信息仅支持macOS')
            from .host import camera_media
            _, media = camera_media()
            print(json.dumps({'target': '054c:0e8a', 'media': media, 'camera_commands_sent': False}, indent=2))
        elif args.action == 'status':
            path = private_path(args.workspace, existing=True)
            state = json.loads((path / 'state.json').read_text())
            from . import ntsc
            ntsc.enabled(state)
            state.setdefault('features', ['fl23'])
            keys = ('phase', 'features', 'failed_during', 'failed_action', 'last_error', 'exit_warning', 'functional_test')
            print(json.dumps({key: state[key] for key in keys if key in state}, ensure_ascii=False, indent=2))
        else:
            return run_camera(args)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print('STOPPED: ' + simplify_message(exc), file=sys.stderr)
        if args.action in ('prepare', 'upgrade', 'install', 'verify', 'restore', 'diagnose', 'finish-install', 'finish-upgrade'):
            print('如已开始相机操作，请保留当前状态和工作目录，先检查结果，不要盲目重跑、断线或重启。', file=sys.stderr)
        return 1
