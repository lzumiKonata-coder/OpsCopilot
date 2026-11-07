"""应用层 - ReAct 模型响应解析与文本清洗"""

import re

from utils.shell import clean_extracted_command
from utils.text import EMOJI_PATTERN, remove_emojis


def extract_from_markdown_report(text: str) -> tuple[str, str | None, str | None]:
    """当模型未严格按 Thought/Action 格式而是输出包含建议指令代码块的排障报告时，智能提取首个建议命令与思考"""
    blocks = re.findall(r"```(?:bash|sh)?\s*\n(.*?)\n```", text, re.DOTALL)
    if not blocks:
        blocks = re.findall(r"```(?:bash|sh)?\s*(.*?)\s*```", text, re.DOTALL)

    action = None
    title = None
    if blocks:
        first_block = blocks[0].strip()
        cmd_lines = [l.strip() for l in first_block.splitlines() if l.strip()]
        for l in cmd_lines:
            if l.startswith("#"):
                comment = l.lstrip("#").strip()
                if comment and not title:
                    title = comment
        action = clean_extracted_command(first_block)

    no_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL).strip()
    thought = ""
    rc_match = re.search(r"(?:根因分析|Diagnosis)[^\n]*\n(.*?)(?=\n###|\Z)", no_code, re.DOTALL | re.IGNORECASE)
    if rc_match:
        thought = rc_match.group(1).strip()
    else:
        for p in no_code.split("\n\n"):
            clean_p = re.sub(r"^#+\s*.*$", "", p, flags=re.MULTILINE).strip()
            if clean_p:
                thought = clean_p
                break
    if not thought:
        thought = no_code[:200].strip()

    return thought, action, title


def parse_react_response(text: str) -> dict[str, str | None]:
    """从模型回复中提取 Thought, Action, Step_Title 或 Final_Answer (兼容 Markdown 加粗、空格与代码块排障报告)"""
    thought_match = re.search(
        r"(?:\*\*Thought:\*\*|Thought:)\s*(.*?)(?=(?:\*\*Plan:\*\*|Plan:|\*\*方案:\*\*|方案:|\*\*Action:\*\*|Action:|\*\*Step[\s_]Title:\*\*|Step[\s_]Title:|Step_Title:|\*\*Final[\s_]Answer:\*\*|Final[\s_]Answer:|\Z))",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    plan_match = re.search(
        r"(?:\*\*Plan:\*\*|Plan:|\*\*方案:\*\*|方案:)\s*(.*?)(?=(?:\*\*Action:\*\*|Action:|\*\*Step[\s_]Title:\*\*|Step[\s_]Title:|Step_Title:|\*\*Final[\s_]Answer:\*\*|Final[\s_]Answer:|\Z))",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    action_match = re.search(
        r"(?:\*\*Action:\*\*|Action:)\s*(.*?)(?=(?:\*\*Step[\s_]Title:\*\*|Step[\s_]Title:|Step_Title:|\*\*Thought:\*\*|Thought:|\*\*Plan:\*\*|Plan:|\*\*方案:\*\*|方案:|\*\*Final[\s_]Answer:\*\*|Final[\s_]Answer:|\Z))",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    title_match = re.search(
        r"(?:\*\*Step[\s_]Title:\*\*|Step[\s_]Title:|Step_Title:)\s*(.*?)(?=(?:\*\*Thought:\*\*|Thought:|\*\*Plan:\*\*|Plan:|\*\*方案:\*\*|方案:|\*\*Action:\*\*|Action:|\*\*Final[\s_]Answer:\*\*|Final[\s_]Answer:|\Z))",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    final_match = re.search(
        r"(?:\*\*Final[\s_]Answer:\*\*|Final[\s_]Answer:|Final\s+Answer:)\s*(.*)",
        text,
        re.DOTALL | re.IGNORECASE,
    )

    thought = thought_match.group(1).strip() if thought_match else ""
    plan = plan_match.group(1).strip() if plan_match else None
    action = action_match.group(1).strip() if action_match else None
    title = title_match.group(1).strip() if title_match else None
    final_answer = final_match.group(1).strip() if final_match else None

    # 清洗可能带有的 markdown code 块标签与反引号
    if action:
        action = clean_extracted_command(action) or None

    # 1. 容错提取：若未找到 Action，但模型输出了带有建议指令或代码块的排障报告格式（例如四段式）
    if not action:
        fb_thought, fb_action, fb_title = extract_from_markdown_report(text)
        if fb_action:
            action = clean_extracted_command(fb_action) or None
            if not thought:
                thought = fb_thought
            if not title:
                title = fb_title

    # 2. 互斥铁律：若提取到了 Action，必须将 final_answer 置空！
    # 因为单步 ReAct 绝不允许在本轮还有 Action 待下发执行时宣告任务终结
    if action:
        final_answer = None

    # 3. 占位符拦截：若 final_answer 包含类似 [待执行...]、[待获取...]、<待等虚假占位符，予以废除
    if final_answer and re.search(r"\[待(?:执行|获取|确认|完成|运行)|待执行\s*Step|<待", final_answer):
        final_answer = None

    # 4. 兜底：若既没有 Action 也解析不出任何代码块命令，且文本包含排障报告标识，才视为最终总结
    if not action and not final_answer:
        if "### 1." in text or "根因分析" in text:
            final_answer = text
        elif thought and ("完成" in thought or "解决" in thought or "成功" in thought or "已查明" in thought or "已安装" in thought or "无需" in thought or "已经是最新" in thought):
            final_answer = thought

    return {
        "thought": thought,
        "plan": plan,
        "action": action,
        "title": title or "运维探测指令",
        "final_answer": final_answer,
    }
