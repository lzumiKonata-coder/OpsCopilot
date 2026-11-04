"""应用层 - 结构化运维事实提取"""

import re
from typing import Any, Dict, List


def extract_semantic_step_fact(step: Dict[str, Any]) -> str:
    """
    结构化提炼单个执行步骤的运维核心事实：
    摒弃盲目的字符串切片截断（如 [:120]），优先提取：
    1. 异常签名（命令不存在、权限拒绝、连接超时、端口冲突等）；
    2. 核心运维实体（IP、端口、服务名、PID、关键退出状态）；
    3. 结构化推演分析结论。
    """
    step_num = step.get("step", "?")
    cmd = (step.get("command") or "").strip()
    title = (step.get("title") or f"步骤 {step_num}").strip()
    analysis = (step.get("analysis") or "").strip()
    observation = (step.get("observation") or "").strip()
    is_err = step.get("is_error", False)
    status_tag = "[异常自愈]" if is_err else "[正常确认]"

    # 1. 如果有明确的分析结论，提炼核心结论句
    extracted_conclusion = ""
    if analysis:
        # 去除多余空行
        clean_analysis = " ".join(line.strip() for line in analysis.splitlines() if line.strip())
        # 寻找结论性前缀
        match = re.search(r"(?:结论|发现|原因|识别为|确认|结果)[:：\s]*(.*?)(?:。|；|$)", clean_analysis)
        if match and len(match.group(1).strip()) > 5:
            extracted_conclusion = match.group(1).strip()
        else:
            # 取第一句完整句
            sentences = re.split(r"[。；\n]", clean_analysis)
            first_sentence = sentences[0].strip() if sentences else ""
            extracted_conclusion = first_sentence if first_sentence else clean_analysis[:100]

    # 2. 如果是异常步骤，提取关键报错签名
    error_fact = ""
    if is_err:
        lower_obs = observation.lower()
        if "command not found" in lower_obs or "未找到命令" in observation:
            error_fact = "命令未安装或未加入 PATH"
        elif "permission denied" in lower_obs or "权限不够" in observation:
            error_fact = "权限不足/需要 root 提权"
        elif "connection refused" in lower_obs or "拒绝连接" in observation:
            error_fact = "目标端口未监听或连接被拒"
        elif "timed out" in lower_obs or "超时" in observation:
            error_fact = "网络超时或主机不可达"
        elif "no space left on device" in lower_obs or "设备上没有空间" in observation:
            error_fact = "磁盘或 Inode 已耗尽"
        elif "failed" in lower_obs or "error" in lower_obs or "fatal" in lower_obs or "critical" in lower_obs:
            # 捕获真实报错行，严格排除类似 "0 errors", "no error", "0 failed" 等误报行
            for line in observation.splitlines():
                line_lower = line.lower()
                if re.search(r"\b(?:error|failed|fatal|critical)\b", line_lower):
                    if not re.search(r"\b(?:0|no|without|zero)\s+(?:errors?|failed|failures?)\b|\berrors?\s*:\s*0\b|\bfailures?\s*:\s*0\b", line_lower):
                        error_fact = line.strip()[:80]
                        break

    # 3. 提取关键系统实体（监听端口、服务名、PID 等）
    entity_facts: List[str] = []
    ports = re.findall(r"(?:0\.0\.0\.0|127\.0\.0\.1|\*):(\d{2,5})", observation)
    if ports:
        entity_facts.append(f"监听端口:{','.join(set(ports[:3]))}")

    pids = re.findall(r"\bpid\s*[:=]?\s*(\d+)\b", observation, re.IGNORECASE)
    if pids:
        entity_facts.append(f"PID:{pids[0]}")

    service_match = re.search(r"([\w\-]+)\.service\s*-\s*([^\n]+)", observation)
    if service_match:
        entity_facts.append(f"服务:{service_match.group(1)}")

    # 4. 组合最终语义事实
    fact_parts = []
    if extracted_conclusion:
        fact_parts.append(extracted_conclusion)
    if error_fact and error_fact not in extracted_conclusion:
        fact_parts.append(f"报错特征: {error_fact}")
    if entity_facts:
        fact_parts.append("; ".join(entity_facts))

    final_fact = "；".join(fact_parts) if fact_parts else (analysis[:100] or "执行完成")
    return f"  * 步骤 {step_num} {status_tag} ({title}): 执行 `{cmd}` -> {final_fact}"
