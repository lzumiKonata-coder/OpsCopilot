"""应用层 - ReAct Think 节点：预算限制与防死循环熔断守卫"""

import hashlib
import logging
import time
from typing import Any, Dict, Optional, Tuple

from application.react.observation import normalize_observation_payload
from domain.policies.intent import is_inspection_command

logger = logging.getLogger("smartops.application.react.nodes.think.guards")


def _budget_result(
    step_idx: int,
    conclusion: str,
    title: str,
    llm_call_count: int,
    total_tokens: int,
) -> Dict[str, Any]:
    """构造预算超限统一收敛返回"""
    thought_ev = {
        "type": "thought",
        "step": step_idx,
        "thought": conclusion,
        "content": conclusion,
        "title": title,
        "status": "completed",
    }
    return {
        "current_thought": conclusion,
        "final_answer": conclusion,
        "final_conclusion": conclusion,
        "budget_exceeded": True,
        "is_success": False,
        "should_stop": True,
        "llm_call_count": llm_call_count,
        "total_tokens": total_tokens,
        "emitted_events": [thought_ev],
    }


def check_step_limit(state: Dict[str, Any], step_idx: int) -> Optional[Dict[str, Any]]:
    """检查是否步数超限"""
    max_steps = state.get("max_steps")
    if max_steps is not None and step_idx > max_steps:
        return {
            "should_stop": True,
            "llm_call_count": state.get("llm_call_count", 0),
            "total_tokens": state.get("total_tokens", 0),
            "emitted_events": [],
        }
    return None


def check_budget(
    state: Dict[str, Any],
    step_idx: int,
    executed_steps: list,
) -> Optional[Dict[str, Any]]:
    """软预算边界校验 (Soft Budgets Check: max_run_seconds / max_llm_calls / max_tokens)"""
    llm_call_count = state.get("llm_call_count", 0)
    total_tokens = state.get("total_tokens", 0)

    start_time = state.get("start_time") or time.time()
    max_run_seconds = state.get("max_run_seconds") or 600.0
    elapsed_seconds = time.time() - start_time
    if elapsed_seconds > max_run_seconds:
        logger.warning(
            "ReAct execution exceeded max_run_seconds budget: %.1fs > %.1fs",
            elapsed_seconds,
            max_run_seconds,
        )
        conclusion = (
            f"[提示] 运维任务执行耗时已达时间软预算上限 ({int(max_run_seconds)}s，实际运行 {int(elapsed_seconds)}s)。"
            f"为保障系统安全并杜绝后台长期空转，状态机已主动收敛终止。当前已执行 {len(executed_steps)} 个推演步骤。"
        )
        return _budget_result(step_idx, conclusion, "运行超时收敛", llm_call_count, total_tokens)

    max_llm_calls = state.get("max_llm_calls") or 30
    if llm_call_count >= max_llm_calls:
        logger.warning("ReAct execution exceeded max_llm_calls budget: %d >= %d", llm_call_count, max_llm_calls)
        conclusion = (
            f"[提示] 运维任务推演已达大模型调用预算上限 ({max_llm_calls} 次)。"
            f"为防止资源过度消耗与无限推演，状态机已主动收敛终止。当前已执行 {len(executed_steps)} 个推演步骤。"
        )
        return _budget_result(step_idx, conclusion, "LLM调用超限收敛", llm_call_count, total_tokens)

    max_tokens = state.get("max_tokens") or 100_000
    if total_tokens >= max_tokens:
        logger.warning("ReAct execution exceeded max_tokens budget: %d >= %d", total_tokens, max_tokens)
        conclusion = (
            f"[提示] 运维任务推演 Token 消耗已达预算上限 ({max_tokens} tokens)。"
            f"流程已主动收敛终止以保护配额。"
        )
        return _budget_result(step_idx, conclusion, "Token超限收敛", llm_call_count, total_tokens)

    return None


def _loop_result(
    step_idx: int,
    conclusion: str,
    title: str,
    llm_call_count: int,
    total_tokens: int,
    is_stalled: bool = False,
    is_success: Optional[bool] = False,
) -> Dict[str, Any]:
    """构造防死循环熔断统一返回"""
    thought_ev = {
        "type": "thought",
        "step": step_idx,
        "thought": conclusion,
        "content": conclusion,
        "title": title,
        "command": "",
        "status": "completed",
    }
    result = {
        "current_thought": conclusion,
        "final_answer": conclusion,
        "final_conclusion": conclusion,
        "should_stop": True,
        "llm_call_count": llm_call_count,
        "total_tokens": total_tokens,
        "emitted_events": [thought_ev],
    }
    if is_stalled:
        result["is_stalled"] = True
    if is_success is not None:
        result["is_success"] = is_success
    return result


def check_loop_guard(
    state: Dict[str, Any],
    step_idx: int,
    executed_steps: list,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """核心防死循环与进展指纹机制，返回 (熔断结果, 反重复警告文本)"""
    llm_call_count = state.get("llm_call_count", 0)
    total_tokens = state.get("total_tokens", 0)
    anti_loop_warning = ""
    cmd_list = [s.get("command", "").strip() for s in executed_steps if s.get("command")]

    # 1. 周期性交替循环检测 (A-B-A-B 往复震荡且回显无新信息)
    if len(cmd_list) >= 4:
        if cmd_list[-1] == cmd_list[-3] and cmd_list[-2] == cmd_list[-4] and cmd_list[-1] != cmd_list[-2]:
            # 检查最近 4 步物理回显指纹：只有当交替执行的两条命令两次回显均无新信息（完全一致）时才熔断
            recent_steps = executed_steps[-4:]
            norm_obs_a1 = normalize_observation_payload(recent_steps[0].get("observation", ""))
            norm_obs_b1 = normalize_observation_payload(recent_steps[1].get("observation", ""))
            norm_obs_a2 = normalize_observation_payload(recent_steps[2].get("observation", ""))
            norm_obs_b2 = normalize_observation_payload(recent_steps[3].get("observation", ""))
            if norm_obs_a1 == norm_obs_a2 and norm_obs_b1 == norm_obs_b2:
                logger.warning(
                    "Detected alternating 2-step cycle with identical observations: %s <-> %s",
                    cmd_list[-2],
                    cmd_list[-1],
                )
                conclusion = (
                    f"状态机安全熔断：检测到指令往复交替振荡 `{cmd_list[-2]}` 与 `{cmd_list[-1]}`，"
                    f"且两次交替执行的物理回显完全一致、未产生任何新增诊断事实。为杜绝无序消耗与空转，流程已主动收敛终止。"
                )
                return _loop_result(step_idx, conclusion, "交替循环安全熔断", llm_call_count, total_tokens, is_stalled=True), ""

    # 2. 连续 3 次下发完全相同指令：硬性熔断收敛，彻底杜绝无限空转
    if len(executed_steps) >= 3:
        last_cmd_1 = executed_steps[-1].get("command", "").strip()
        last_cmd_2 = executed_steps[-2].get("command", "").strip()
        last_cmd_3 = executed_steps[-3].get("command", "").strip()
        if last_cmd_1 and last_cmd_1 == last_cmd_2 == last_cmd_3:
            logger.warning("Detected 3 consecutive identical commands '%s'; hard loop break triggered", last_cmd_1)
            conclusion = (
                f"状态机安全熔断：已连续 3 次执行完全相同指令 `{last_cmd_1}`，物理状态无任何新增变化。"
                f"为杜绝无序消耗与机器空转，流程已强制收敛终止。建议人工复核现场。"
            )
            return _loop_result(step_idx, conclusion, "循环安全熔断", llm_call_count, total_tokens), ""

    # 3. 累计执行频次熔断：单个只读命令在整场会话中累计执行 >= 3 次且输出无变化
    if len(executed_steps) >= 3:
        cmd_occurrences: Dict[str, list] = {}
        for idx, s in enumerate(executed_steps):
            c = s.get("command", "").strip()
            if c:
                cmd_occurrences.setdefault(c, []).append(idx)
        for c, indices in cmd_occurrences.items():
            if len(indices) >= 3 and is_inspection_command(c):
                obs_list = [executed_steps[i].get("observation", "") for i in indices[-3:]]
                norm_obs = [normalize_observation_payload(o) for o in obs_list]
                if norm_obs[0] == norm_obs[1] == norm_obs[2]:
                    logger.warning(
                        "Command '%s' executed 3 times with identical observations; hard loop break triggered",
                        c,
                    )
                    conclusion = (
                        f"状态机安全熔断：只读指令 `{c}` 在本会话中已累计执行 {len(indices)} 次且物理回显完全一致，"
                        f"未产生任何新增诊断事实。流程已主动收敛终止，建议人工介入排查。"
                    )
                    return _loop_result(step_idx, conclusion, "重复探测安全熔断", llm_call_count, total_tokens, is_stalled=True), ""

    # 4. 连续 K 步无新信息进展判定 (Information Saturation / Stalling Guard: K=4 步)
    if len(executed_steps) >= 4:
        recent_steps = executed_steps[-4:]
        all_recent_inspection = all(is_inspection_command(s.get("command", "")) for s in recent_steps)
        if all_recent_inspection:
            earlier_steps = executed_steps[:-4]
            seen_fps = {
                hashlib.md5(normalize_observation_payload(s.get("observation", "")).encode()).hexdigest()
                for s in earlier_steps
            }
            recent_fps = [
                hashlib.md5(normalize_observation_payload(s.get("observation", "")).encode()).hexdigest()
                for s in recent_steps
            ]
            has_new_info = any(rfp not in seen_fps for rfp in recent_fps) and len(set(recent_fps)) > 1
            if not has_new_info:
                logger.warning("Detected 4 consecutive inspection steps with zero new diagnostic info; stalling break triggered")
                conclusion = (
                    f"状态机安全收敛：连续 4 步只读探测未产生任何物理新增信息（回显特征已饱和重复），"
                    f"排查流程未获得新线索。为杜绝持续消耗 Token 与无序探测，流程已主动收敛终止。建议人工复核现场。"
                )
                return _loop_result(step_idx, conclusion, "无新进展主动收敛", llm_call_count, total_tokens, is_stalled=True), ""

    if len(executed_steps) >= 2:
        last_cmd_1 = executed_steps[-1].get("command", "").strip()
        last_cmd_2 = executed_steps[-2].get("command", "").strip()
        # 连续 2 次下发只读检查命令且回显一致：判定为验证收敛完成
        if last_cmd_1 and last_cmd_1 == last_cmd_2 and is_inspection_command(last_cmd_1):
            logger.info("Detected repeated inspection loop for '%s'; auto-converging to Final_Answer", last_cmd_1)
            conclusion = (
                f"已连续执行验证指令 `{last_cmd_1}`，物理回显状态完全一致且无新变化。"
                f"对于清理或状态探测任务，当前结果已充分确认目标状态达成，流程自动收敛完成。"
            )
            return _loop_result(step_idx, conclusion, "验证收敛完成", llm_call_count, total_tokens, is_success=None), ""

        # 连续 2 次执行任意相同指令：发出死循环严正警告
        if last_cmd_1 and last_cmd_1 == last_cmd_2:
            anti_loop_warning = (
                f"\n\n【死循环严正警告与指令拦截】：\n"
                f"你上一轮已经执行过完全相同的指令 `{last_cmd_1}`，且物理状态未发生预期变化！\n"
                f"严禁在当前步骤再次输出相同指令！\n"
                f"若目标软件已安装/就绪，请立即输出 Final_Answer 总结完成任务；\n"
                f"若需要继续排查，必须更换为全新的诊断指令（例如：检查二进制文件是否存在 `find / -name docker 2>/dev/null`，或检查服务状态 `systemctl status <service>`，或通过包管理器 `rpm -ql`/`rpm -V` 验证完整性），严禁无脑重复修改 PATH 或重复操作！"
            )

    return None, anti_loop_warning
