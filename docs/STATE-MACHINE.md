# 阶段、退出与失败处理

`prepare` → `prepared` → `install` → `installed_pending_cold_start` → 人工完整断电 → `verify` → `verified_after_cold_start`。

准备创建新的机身目录，默认在 original/ 中保存一份备份，逐文件 fsync、读回核验；即使后续失败，已经保存的文件也保留。部分备份或预检查失败不能当作可安装工作目录。

`install` 仅接受完成的预检查；`verify` 仅接受已完成安装/验证；`restore` 仅接受已完成安装/验证及实际电量、ST/冷启动确认。参数/阶段不合规时先拒绝，不接管USB。

## 普通USB返回超时

`NormalReturnTimeout` 只有在以下条件同时满足时才能从会话退出抛出：

1. 操作主体没有抛出异常。
2. 第二次认证已经完成，会话通信仍健康。
3. 服务停止命令正常完成，句柄关闭没有异常。
4. 等待相同物理端口的普通USB身份超时，且没有其他退出错误。

CLI 再根据当前阶段和已保存证据确认完整结果，记录 `prepared_usb_unconfirmed`、`installed_pending_cold_start_usb_unconfirmed`、`restored_pending_cold_start_usb_unconfirmed` 或 `verified_after_cold_start_usb_unconfirmed`，退出码2。没有盲目重试写入，也不把未知身份当作正常相机。

这不同于相机操作途中 USB 超时：任何这种超时或 KeyboardInterrupt 都使会话不健康，禁止进一步设备命令，包括停止认证。不会强制复位USB、关机或尝试修复未知镜像。

## needs_review

已开始的操作发生其他异常，保留当前阶段、错误、完整日志和读回。没有通用强制重试、强制推进阶段或自动恢复命令。请先排查，不要删除 state.json 或把阶段手改成 prepared。

0.2.3 增加首次安装服务入口超时的明确重试：`install --retry-service-entry` 只接受准备已完成、原始堆栈证明尚未进入主体的精确 `0336` 等待超时。离线核对备份、候选、完整日志及每次错误，归档原状态后重试一次连接；进入服务模式后先核对当前完整原版程序和目标设置，再执行原安装流程。重试不把阶段回退为 prepared；相同入口超时仍保留原失败阶段，主体开始后失败则保留实际阶段。安装或已确认主体完整完成后才清除当前失败标识。适用范围和实机反馈见 [INSTALL-RETRY.md](INSTALL-RETRY.md)。

## 恢复

`restore` 对工作目录所选版本执行完整哈希检查及原文件恢复，成功为 `restored_pending_cold_start`。如果主体完成但普通USB返回超时，会带 `_usb_unconfirmed` 后缀。新版两类配置冷启动后可用 `verify` 核验原版完整程序、目标设置和7/9运行区域，成功为 `restored_verified_after_cold_start`；旧目录仍保留原行为，不自动迁移。

默认一份备份；只有显式指定可选的 `--backup-copy` 才额外保存副本。为兼容未修改的上游加载器，单副本状态中的 `backup_copy` 指向 `original/` 自身，`backup_copies: 1` 记录实际份数。该字段不代表存在第二份独立备份。

## 旧版 FL2/FL3 追加升级

`prepare-upgrade` 离线读取已验证的旧目录，创建独立新目录并保存所选两类版本之一，成功为 `upgrade_prepared`。不使用首次 install、不修改源目录。`upgrade` 仅接受此阶段、实际电量、冷启动确认及显式实验选项。

核对完整源程序、当前/保存设置、运行区域及 WBI 后，先创建单次尝试标记，再进入 `writing_upgrade_programs` → `upgrade_programs_written` → 完整程序读回和设置保持性检查 → `upgrade_settings_verified` → WBI收尾 → `installed_pending_cold_start`。冷启动 verify 沿用新目录。升级不提供 CP/AP 设置写入/保存接口。未知结果进入 needs_review，不删标记、不重跑。

0.2.1 在本地保持性检查中仅为本次观察到的当前时间相关字段增加有界检查，保存设置必须逐项完全一致；规则见 [UPGRADE.md](UPGRADE.md)。符合条件时继续现有阶段和WBI流程，不在失败后自动重试；已有 `needs_review` 目录也不会自动迁移或续写。固定上游白名单与程序模块不变。

升级主体完成而普通USB返回超时，只在完整读回、设置保持性及最后 WBI 完成证据均匹配时记为 `installed_pending_cold_start_usb_unconfirmed`。恢复后 verify 的同类超时阶段为 `restored_verified_after_cold_start_usb_unconfirmed`。

## 已知 writing_settings 停止点的专项收尾

`finish-install --accept-experimental-finish` 仅处理完整历史证据与实时状态均匹配的原FL2/FL3、固定 +60 失败。详见 [适用条件与风险](INTERRUPTED-INSTALL.md#可选已知停止点的实验性-wbi-收尾)。它不修改计数白名单、不重写程序或保存参数。写WBI之前创建持久化尝试标记并进入 finishing_snapshot；重复尝试一律拒绝。完成后才进入 installed_pending_cold_start（或已完成主体的USB未确认变体）；WBI结果不明则 needs_review / failed_action=finish-install，不允许再次自动续写。此新增路径仅有离线测试，尚无实机结果。

## 新版追加升级的专项收尾与只读重验

`finish-upgrade --accept-reviewed-upgrade-finish` 只处理新版本 `upgrade_programs_written` 的历史固定+60停止点；条件和未知字段风险见 [UPGRADE-FINISH.md](UPGRADE-FINISH.md)。写WBI前创建独立的 `upgrade-finish-attempt.private.json`，经审查后才进入 `finishing_upgrade_snapshot`。成功状态为 `installed_pending_cold_start`；WBI结果未知仍停止，不允许重复。额外时间例外必须经使用者另外明确选择，不能默认启用。

0.2.2 将受控重验扩展到旧版默认FL23（包括无features的目录），与新版两类共用同一入口。仅 `needs_review / verify` 且原阶段为 `installed_pending_cold_start` 或其已完成主体的USB未确认变体、精确报错为本版本旧运行内容时适用。先离线核对前次完整目标读回、唯一runtime与审计一致、磁盘全为目标、内存仅为本版本已知原始/目标内容且至少一处旧内容、正常MSC返回，再由使用者确认真实取电池后明确运行原有verify。不根据运行时间或image_mode推断使用者没有取电池，不额外以这些元数据阻挡只读重验。详见 [VERIFY-RETRY.md](VERIFY-RETRY.md)。

再次verify失败保留原失败阶段、记录最新错误；对于旧工具已将失败阶段覆盖成needs_review的情况，只有存在此前受控重验的有效审查记录，且当前历史证据也完整符合条件，才允许只重验。其他错误、断线、写入失败、未知内容及缺失证据仍停止。原记录保留，不伪造安装阶段；这项只读重验不允许install/restore/upgrade续写，也不再处理WBI。只有新验证完整通过才清除当前失败标识。首次安装尚未进入主体的独立重试例外见上文。
