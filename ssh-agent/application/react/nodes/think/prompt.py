"""应用层 - ReAct Think 节点：上下文与提示词拼装"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from application.prompts import REACT_SYSTEM_PROMPT
from application.react.observation import detect_interactive_prompt, is_empty_observation
from domain.policies.intent import is_mutation_task


def build_system_prompt(
    host: Any,
    is_root: bool,
    os_family: str,
    terminal_buffer: str,
    core_memories_text: str,
    step_idx: int,
) -> str:
    """构建 ReAct 系统提示词（含终端快照与核心记忆）"""
    if step_idx == 1:
        term_snapshot = terminal_buffer.strip() if terminal_buffer else "当前终端暂无输出快照。"
    else:
        term_snapshot = f"已进入第 {step_idx} 步闭环推演，请参考下方客观执行与物理回显记录推进。"

    sys_prompt = REACT_SYSTEM_PROMPT.format(
        host_ip=host.host,
        login_user=host.username,
        is_root=str(is_root),
        os_info=os_family,
        terminal_buffer=term_snapshot,
    )
    if core_memories_text:
        sys_prompt += core_memories_text
    return sys_prompt


def build_user_prompt(
    user_prompt: str,
    step_idx: int,
    history_text: str,
    anti_loop_warning: str,
    has_encountered_error: bool,
    last_error_detail: Optional[str],
    os_family: str,
    is_root: bool,
    react_history: Sequence[Any],
    executed_steps: List[Dict[str, Any]],
    is_plan_confirmed: bool,
    permission_mode: str,
    steering_texts: List[str],
    images: List[str],
) -> Tuple[str, Optional[str]]:
    """构建本轮用户提示词，返回 (提示词正文, 终端交互确认提示)"""
    prompt_content = (
        f"【运维排障目标】: {user_prompt}\n\n"
        f"【当前已推进的执行与观察记录】:\n{history_text}"
    )
    if anti_loop_warning:
        prompt_content += anti_loop_warning
    if has_encountered_error and last_error_detail:
        prompt_content += (
            f"\n\n【紧急自愈指示】：\n"
            f"前序执行遭遇物理报错：{last_error_detail}！\n"
            f"原定方案在当前目标物理机上失效。请根据终端提示符特征（注意识别是否为 CentOS/RHEL/openEuler 发行版，包管理器应使用 yum/dnf，且 root 用户严禁加 sudo 前缀）"
            f"与前序物理回显，进行深度自我纠错推演，给出下一步有效的自愈排查或修复 Action！"
        )

    # 检测前序执行是否正停留在终端交互确认等待提示（如 y/n、是否删除...？等）
    last_obs = ""
    if react_history:
        last_entry = react_history[-1]
        if "Observation:" in last_entry:
            last_obs = last_entry.split("Observation:")[-1].split("Reflection:")[0].strip()

    interactive_hint = detect_interactive_prompt(last_obs)
    if interactive_hint:
        prompt_content += (
            f"\n\n【紧急终端交互确认指示】：\n"
            f"终端物理回显当前停留在交互确认提示符：'{interactive_hint}'！\n"
            f"系统正在等待你的键盘确认。请在当前步骤 Action 中直接输出 'y'（确认同意执行）或 'n'（拒绝取消），绝对禁止下发新命令！\n"
            f"示例：\nThought: 终端提示确认操作，直接输入 y 确认继续。\nAction: y\nStep_Title: 确认执行"
        )
    elif executed_steps:
        last_step = executed_steps[-1]
        last_cmd = last_step.get("command", "")
        last_obs = last_step.get("observation", "")
        if ("grep" in last_cmd or "find" in last_cmd or "ls" in last_cmd) and is_empty_observation(last_cmd, last_obs):
            prompt_content += (
                f"\n\n【验证完成提示】：\n"
                f"前一步执行了验证指令 `{last_cmd}`，终端未检索到任何目标（回显为空）。这证明前序清理/删除操作已成功生效，目标已不存在！\n"
                f"请直接输出【格式 B：Final_Answer】总结完成任务，切勿重复下发该验证命令！"
            )

    if is_mutation_task(user_prompt) and step_idx == 1 and not is_plan_confirmed and permission_mode != "full_access":
        prompt_content += (
            f"\n\n【高危/变更任务前置方案确认铁律（极度重要）】：\n"
            f"当前任务【{user_prompt}】属于涉及系统状态变更、软件安装或文件删除的操作！\n"
            f"在正式下发具有变更或不可逆影响的指令之前，你必须先向用户说明操作影响、给出严密的分步执行思路并进行风险提示。\n"
            f"输出格式规范（必须严格遵循）：\n"
            f"Thought: 简述系统影响与变更风险分析（可使用英文或中文推演）\n"
            f"Step_Title: 方案确认\n"
            f"Plan: 明确即将操作的目标与路径、分步执行思路（例如：1. 先确认目标状态与环境 2. 执行安装/变更操作 3. 再次检查确认目标状态已正常达成）、明确指出该操作具有系统状态变更或不可逆风险，最后以“是否按这个思路开始执行？”作为询问结语。\n"
            f"当前第 1 步严禁输出 Action，不要填写任何执行命令，也不要输出 Final_Answer。\n"
        )
    elif is_mutation_task(user_prompt) and executed_steps:
        prompt_content += (
            f"\n\n【单步推进铁律】：\n"
            f"当前任务【{user_prompt}】属于系统配置/安装变更任务。每一次交互只能且必须输出当前这一步（Thought 与单行 Action）！\n"
            f"严禁在尚未实际执行并验证变更结果前输出 Final_Answer 或带有 [待执行] 等占位符的虚假结论！"
        )

    if steering_texts:
        joined_steering = "\n".join(f"- {s}" for s in steering_texts)
        prompt_content += (
            f"\n\n【用户最新即时插话/方向纠偏指令（最高优先级，请立即响应调整推演方向）】：\n"
            f"{joined_steering}\n"
        )

    if images:
        prompt_content += (
            f"\n\n【多模态图片输入】：用户本轮随消息附带了 {len(images)} 张图片（已随本条多模态消息直接提供给你）。\n"
            "若用户的意图是描述、识别或分析图片内容，请直接输出 Final_Answer 回答，不要为此执行任何终端命令；\n"
            "若图片与服务器排障相关（如截图报错、界面状态），再结合终端回显与你已执行的步骤进行分析。"
        )

    prompt_content += f"\n\n请严格按格式给出第 {step_idx} 步的 Thought 和 Action（若已查明或完成则给出 Final_Answer）："
    return prompt_content, interactive_hint


def build_conversation(
    sys_prompt: str,
    prompt_content: str,
    images: List[str],
    clean_history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """构建本轮对话消息：系统提示 + 历史轮次 + 当前用户消息（含多模态图片块）"""
    conversation: List[Dict[str, Any]] = [{"role": "system", "content": sys_prompt}]
    # 融入多轮历史对话上下文，消除上下文丢失断层并彻底剥离长篇排障报告污染
    if clean_history:
        for m in clean_history:
            conversation.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    if images:
        conversation.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_content},
                    *[
                        {"type": "image_url", "image_url": {"url": img_url}}
                        for img_url in images
                    ],
                ],
            }
        )
    else:
        conversation.append({"role": "user", "content": prompt_content})
    return conversation
