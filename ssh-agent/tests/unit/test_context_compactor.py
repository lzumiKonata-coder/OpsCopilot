"""
上下文压缩与滑动窗口引擎自动化测试 (Phase 3: Context Compaction Tests)
验证：
1. 智能首尾截断 (smart_truncate)
2. Token 预估器 (TokenBudgetEstimator)
3. ReAct 步骤滑动窗口与阶段压缩 (ReActContextCompactor)
4. 多轮跨会话历史压缩 (SessionCompactor)
5. ReAct 状态机引擎与上下文压缩器的端到端联动
"""

import logging

import pytest

from adapter.tools.builtin import build_default_registry
from application.ports import ILLMClient, ISSHGateway
from application.react.compaction import ReActContextCompactor, SessionCompactor, resolve_context_profile
from application.react.engine import LangGraphReActEngine
from domain.entities import AuthType, Command, ExecutionResult, SSHConnection
from utils.text import TokenBudgetEstimator, smart_truncate


def test_smart_truncate_short_text():
    """短文本不触发截断，原样返回"""
    short = "Linux localhost 5.15.0-generic #1 SMP x86_64"
    assert smart_truncate(short, max_chars=100) == short
    assert smart_truncate("", max_chars=100) == ""
    assert smart_truncate(None, max_chars=100) == ""


def test_smart_truncate_long_text_preserves_head_and_tail():
    """长回显智能保留头部与尾部关键报错，中间插入省略指示标记"""
    head_marker = "=== BOOT SEQUENCE START ===\nInitializing hardware...\n"
    tail_marker = "\n[ERROR] CRITICAL_FAIL: Segmentation fault at 0x7fff40.\n=== TRACE END ==="
    middle = "filler log line 1234567890\n" * 200

    long_text = head_marker + middle + tail_marker
    assert len(long_text) > 4000

    truncated = smart_truncate(long_text, max_chars=800, head_ratio=0.3)

    assert "BOOT SEQUENCE START" in truncated
    assert "CRITICAL_FAIL: Segmentation fault" in truncated
    assert "已自动截断: 中间省略" in truncated
    assert len(truncated) < 1200


def test_token_budget_estimator():
    """验证 Token 预估器对英文代码和中文的分词权重计算"""
    estimator = TokenBudgetEstimator()

    en_text = "echo 'hello world' > /tmp/test.txt"
    tokens_en = estimator.estimate_tokens(en_text)
    assert 5 <= tokens_en <= 15

    zh_text = "查看当前服务器系统磁盘与内存水位状态"
    tokens_zh = estimator.estimate_tokens(zh_text)
    assert 10 <= tokens_zh <= 20

    messages = [
        {"role": "system", "content": "You are a Linux sysadmin."},
        {"role": "user", "content": "检查 Nginx 状态"},
    ]
    total_tokens = estimator.estimate_messages_tokens(messages)
    assert total_tokens > 15


def test_react_context_compactor_within_window():
    """步数在滑动窗口内时，不进行前序压缩，保留全部详细推演"""
    compactor = ReActContextCompactor(window_size=3)

    steps = [
        {
            "step": 1,
            "title": "检查磁盘",
            "thought": "先看磁盘",
            "command": "df -h",
            "observation": "/dev/sda1 45%",
            "analysis": "磁盘空间正常",
            "is_error": False,
        },
        {
            "step": 2,
            "title": "检查内存",
            "thought": "再看内存",
            "command": "free -m",
            "observation": "Mem: 16G total, 4G used",
            "analysis": "内存水位安全",
            "is_error": False,
        },
    ]

    summary, recent, _ = compactor.compact_react_history_with_stats(steps)
    assert summary is None
    assert len(recent) == 2

    formatted, _ = compactor.format_prompt_history_with_stats(steps)
    assert "历史排查阶段性小结" not in formatted
    assert "Step 1:" in formatted
    assert "Step 2:" in formatted


def test_react_context_compactor_exceeding_window():
    """步数超出滑动窗口时，前序步骤折叠为小结事实，活跃窗口保留完整回显"""
    compactor = ReActContextCompactor(window_size=2)

    steps = [
        {
            "step": 1,
            "title": "探测包管理器",
            "thought": "尝试 apt",
            "command": "apt-get update",
            "observation": "apt-get: command not found",
            "analysis": "找不到 apt-get，识别为 RPM 发行版",
            "is_error": True,
        },
        {
            "step": 2,
            "title": "自愈安装",
            "thought": "改用 yum",
            "command": "yum install -y nginx",
            "observation": "Complete!",
            "analysis": "Nginx 安装完成",
            "is_error": False,
        },
        {
            "step": 3,
            "title": "启动服务",
            "thought": "启动 systemd 服务",
            "command": "systemctl start nginx",
            "observation": "",
            "analysis": "服务启动成功",
            "is_error": False,
        },
        {
            "step": 4,
            "title": "检查监听端口",
            "thought": "确认 80 端口",
            "command": "ss -tulpn | grep :80",
            "observation": "LISTEN 0 128 *:80",
            "analysis": "80 端口已正常监听",
            "is_error": False,
        },
    ]

    summary, recent, _ = compactor.compact_react_history_with_stats(steps)
    assert summary is not None
    assert "历史排查阶段性小结 (已紧凑压缩前序 2 个步骤)" in summary
    assert "步骤 1 [异常自愈]" in summary
    assert "步骤 2 [正常确认]" in summary
    assert len(recent) == 2  # 活跃窗口为 step 3 与 step 4

    formatted, _ = compactor.format_prompt_history_with_stats(steps)
    assert "历史排查阶段性小结" in formatted
    assert "近期详细推演与物理回显 (活跃窗口)" in formatted
    assert "Step 3:" in formatted
    assert "Step 4:" in formatted
    # 确认活跃窗口内包含完整详细格式
    assert "Thought: 启动 systemd 服务" in formatted
    assert "Thought: 确认 80 端口" in formatted


def test_session_compactor_multi_turn():
    """验证跨多轮历史会话压缩：长历史浓缩为前序小结，保留最近活跃多轮"""
    compactor = SessionCompactor(max_recent_turns=2, max_session_tokens=100)

    session_history = [
        {"role": "user", "content": "排查为什么 80 端口打不开？"},
        {"role": "assistant", "content": "### 智能排障报告 【执行成功】\n\n**最终结论**：\n已确认 iptables 防火墙封锁 80 端口，已添加放行规则。"},
        {"role": "user", "content": "帮我看看磁盘空间"},
        {"role": "assistant", "content": "### 智能排障报告 【执行成功】\n\n**最终结论**：\n根分区占用 45%，空间充足。"},
        {"role": "user", "content": "查看内存"},
        {"role": "assistant", "content": "内存可用 12GB。"},
        {"role": "user", "content": "检查 CPU 负载"},
        {"role": "assistant", "content": "当前负载 0.15，系统空闲。"},
    ]

    compacted = compactor.compact_session_history(session_history, current_user_prompt="现在网络通吗？")

    # 保留最近 2 条 + 1 条前序折叠小结 = 3 条
    assert len(compacted) == 3
    first_msg = compacted[0]
    assert first_msg["role"] == "assistant"
    assert "[前序多轮会话历史小结]" in first_msg["content"]
    assert "80 端口" in first_msg["content"]

    # 验证最近轮次被保留
    recent_contents = [m["content"] for m in compacted[1:]]
    assert any("CPU" in c for c in recent_contents)


class MockMultiStepLLM(ILLMClient):
    def __init__(self):
        self.recorded_prompts: list[str] = []
        self.call_count = 0
        self.responses = [
            "Thought: 查看第一步。\nAction: df -h\nStep_Title: 步骤1",
            "Thought: 查看第二步。\nAction: free -m\nStep_Title: 步骤2",
            "Thought: 查看第三步。\nAction: uptime\nStep_Title: 步骤3",
            "Thought: 查看第四步。\nFinal_Answer: 全部指标正常，排障完成。",
        ]

    async def chat(self, messages: list, temperature: float = 0.2, tools: list | None = None) -> str:
        for m in messages:
            if m.get("role") == "user":
                self.recorded_prompts.append(m.get("content", ""))
        idx = min(self.call_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        resp = await self.chat(messages)
        yield resp


class DummySSHGateway(ISSHGateway):
    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        cmd = command.raw_command
        # 模拟超长日志回显以测试 smart_truncate
        if "free" in cmd:
            output = "HEADER_START\n" + ("line " * 50 + "\n") * 60 + "TAIL_ERROR_FLAG"
        else:
            output = f"Output of {cmd}"
        return ExecutionResult(stdout=output, stderr="", exit_status=0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


@pytest.mark.asyncio
async def test_prompt_prefix_stable_and_append_only():
    """验证静态 system 与初始任务消息跨步骤一致，ReAct 历史只追加不重写"""

    class RecordingLLM(ILLMClient):
        def __init__(self):
            self.calls: list[list] = []
            self.tools: list = []
            self.responses = [
                "Thought: 查看第一步。\nAction: df -h\nStep_Title: 步骤1",
                "Thought: 查看第二步。\nAction: free -m\nStep_Title: 步骤2",
                "Thought: 完成。\nFinal_Answer: 指标正常。",
            ]
            self.call_count = 0

        async def chat(self, messages: list, temperature: float = 0.2, tools: list | None = None) -> str:
            self.calls.append(messages)
            self.tools.append(tools)
            idx = min(self.call_count, len(self.responses) - 1)
            self.call_count += 1
            return self.responses[idx]

        async def stream_chat(self, messages: list):
            yield await self.chat(messages)

    llm = RecordingLLM()
    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=DummySSHGateway())
    host = SSHConnection(
        name="cache-host",
        host="127.0.0.1",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    initial_state = {
        "host": host,
        "user_prompt": "巡检系统综合健康度",
        "max_steps": 3,
        "step_idx": 1,
        "session_history": [],
        "executed_steps": [],
        "react_messages": [],
        "react_compacted_upto_step": 0,
        "planned_steps_queue": [],
        "react_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "emitted_events": [],
    }

    async for _ in engine.build_graph().astream(initial_state, stream_mode="updates"):
        pass

    assert len(llm.calls) >= 2
    first_call, second_call = llm.calls[0], llm.calls[1]

    # 静态 system 与初始任务消息在两次调用中完全一致
    assert first_call[0] == second_call[0]
    assert first_call[1] == second_call[1]
    # 历史只追加：第二次调用在稳定前缀后新增 assistant 与 observation 消息
    assert second_call[:2] == first_call[:2]
    assert second_call[2]["role"] == "assistant"
    assert second_call[3]["role"] == "user"
    assert "Observation:" in second_call[3]["content"]
    # 本任务内工具集合保持稳定
    assert llm.tools[0] == llm.tools[1]


@pytest.mark.asyncio
async def test_react_engine_context_compaction_integration():
    """端到端验证：执行 4 步推演时，滑动窗口 (window_size=2) 触发步骤压缩并截断超长回显"""
    mock_llm = MockMultiStepLLM()
    mock_gateway = DummySSHGateway()

    compactor = ReActContextCompactor(window_size=2, max_observation_chars=500)
    engine = LangGraphReActEngine(
        llm_client=mock_llm,
        ssh_gateway=mock_gateway,
        context_window_size=2,
        context_compactor=compactor,
    )

    host = SSHConnection(
        name="test-compaction-host",
        host="127.0.0.1",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )

    graph_app = engine.build_graph()

    initial_state = {
        "host": host,
        "user_prompt": "巡检系统综合健康度",
        "max_steps": 5,
        "step_idx": 1,
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "planned_steps_queue": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "is_blocked": False,
        "final_answer": None,
        "final_conclusion": None,
        "is_success": True,
        "should_stop": False,
        "emitted_events": [],
    }

    last_state = dict(initial_state)
    async for chunk in graph_app.astream(initial_state, stream_mode="updates"):
        for node_name, update in chunk.items():
            for k, v in update.items():
                if k != "emitted_events":
                    last_state[k] = v

    # 验证 ReAct 状态推进了至少 3 步并成功完成
    assert last_state.get("final_conclusion") is not None
    assert len(last_state.get("executed_steps", [])) >= 3

    # 验证在后续 LLM 调用中，追加式消息历史已被压缩小结折叠
    assert len(mock_llm.recorded_prompts) >= 4
    assert any("历史排查阶段性小结" in p for p in mock_llm.recorded_prompts)
    assert any("近期详细推演与物理回显 (活跃窗口)" in p for p in mock_llm.recorded_prompts)

    # 验证超长回显被智能截断，包含省略提示
    second_step_obs = last_state["executed_steps"][1]["observation"]
    assert "已自动截断: 中间省略" in second_step_obs
    assert "HEADER_START" in second_step_obs
    assert "TAIL_ERROR_FLAG" in second_step_obs


def test_token_budget_driven_compaction_within_window():
    """验证：即使步数未达到 window_size，但步骤 Token 超出预算时，主动触发阶段性压缩"""
    # 设置大窗口 (5 步)，但极低 Token 预算 (150 tokens)
    compactor = ReActContextCompactor(window_size=5, max_history_tokens=150)

    # 3 个步骤，但每个步骤有大段详细推演回显
    steps = [
        {
            "step": 1,
            "title": "查看系统日志",
            "thought": "检查 /var/log/messages 中与 nginx 或网络相关的错误记录",
            "command": "tail -n 100 /var/log/messages",
            "observation": "kernel: [12345.67] Out of memory: Kill process 9999 (nginx) score 850 or sacrifice child\n" * 5,
            "analysis": "发现系统在 10 分钟前发生 OOM，Nginx 主进程被内核杀掉，因此 80 端口停止监听",
            "is_error": True,
        },
        {
            "step": 2,
            "title": "检查可用内存",
            "thought": "确认当前系统是否仍处于高内存压力状态",
            "command": "free -m",
            "observation": "Mem: 16000 total, 15500 used, 200 free, 300 buff/cache\nSwap: 0 total, 0 used",
            "analysis": "当前系统可用内存仅剩 200MB 且未配置 Swap，存在极高 OOM 风险",
            "is_error": False,
        },
        {
            "step": 3,
            "title": "定位高内存占用进程",
            "thought": "使用 ps 按内存降序查看前 5 个进程",
            "command": "ps aux --sort=-%mem | head -n 6",
            "observation": "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND\njava 1234 10.0 65.0 12000000 10400000 ? Sl 10:00 5:00 /usr/bin/java -jar backend.jar",
            "analysis": "发现 Java 后端进程 PID 1234 占用了 65% 内存",
            "is_error": False,
        },
    ]

    # 总步数 3 <= window_size 5，但 Token 显然超过 150
    summary, recent, _ = compactor.compact_react_history_with_stats(steps)

    # 验证 Token 预算机制生效，成功压缩了前序步骤
    assert summary is not None
    assert "历史排查阶段性小结" in summary
    assert "步骤 1 [异常自愈]" in summary
    # 验证活跃窗口保留了最近的步骤
    assert len(recent) >= 1
    assert compactor.last_stats.get("compressed") is True


def test_model_adaptive_context_profiles():
    """验证不同大模型上下文画像的自适应解析与窗口尺寸映射"""
    from application.react.compaction import resolve_context_profile

    # 128k 超长上下文模型：GLM-4-Plus, DeepSeek-V3/R1
    profile_glm = resolve_context_profile("glm-4-plus")
    assert profile_glm["max_history_tokens"] == 16000
    assert profile_glm["default_window_size"] == 8
    assert profile_glm["max_recent_turns"] == 10

    profile_deepseek = resolve_context_profile("deepseek-r1")
    assert profile_deepseek["max_history_tokens"] == 16000
    assert profile_deepseek["default_window_size"] == 8

    # 32k 模型：GLM-4-Flash
    profile_flash = resolve_context_profile("glm-4-flash")
    assert profile_flash["max_history_tokens"] == 8000
    assert profile_flash["default_window_size"] == 5

    # 默认/保底模型
    profile_default = resolve_context_profile(None)
    assert profile_default["max_history_tokens"] == 3500
    assert profile_default["default_window_size"] == 3

    # Compactor 传入 model_name 自动装载自适应画像
    compactor = ReActContextCompactor(model_name="glm-4-plus")
    assert compactor.window_size == 8
    assert compactor.max_history_tokens == 16000


def test_semantic_fact_extraction():
    """验证语义事实提炼：精准捕获核心结论、报错类型与端口/PID实体，而非机械截断"""
    from application.react.compaction import extract_semantic_step_fact

    # 场景 1: 包含命令未找到与分析推断
    step_err = {
        "step": 1,
        "title": "探测包管理器",
        "command": "apt-get update",
        "observation": "bash: apt-get: command not found",
        "analysis": "结论：找不到 apt-get 工具，系统识别为 RHEL/CentOS 系发行版。应切换使用 yum 或 dnf 安装工具包。",
        "is_error": True,
    }
    fact_err = extract_semantic_step_fact(step_err)
    assert "步骤 1 [异常自愈]" in fact_err
    assert "apt-get" in fact_err
    assert "找不到 apt-get 工具" in fact_err
    assert "命令未安装或未加入 PATH" in fact_err

    # 场景 2: 端口监听与服务状态确认
    step_port = {
        "step": 2,
        "title": "验证端口监听",
        "command": "ss -tulpn | grep :8080",
        "observation": "tcp LISTEN 0 128 0.0.0.0:8080 0.0.0.0:* users:((\"python3\",pid=4567,fd=4))",
        "analysis": "确认服务已在 8080 端口启动并正常监听，PID 为 4567",
        "is_error": False,
    }
    fact_port = extract_semantic_step_fact(step_port)
    assert "步骤 2 [正常确认]" in fact_port
    assert "8080" in fact_port
    assert "PID:4567" in fact_port


def test_session_compactor_token_budget_driven():
    """验证 SessionCompactor 多轮会话在 Token 超预算时的主动压缩机制"""
    # 限制 100 tokens (历史消息约 140 tokens，因此必定触发 Token 预算压缩)
    compactor = SessionCompactor(max_recent_turns=6, max_session_tokens=100)

    # 4 轮交互，轮次未超 6，但总字符及 Token 较长
    history = [
        {"role": "user", "content": "帮我看看 Nginx 为什么无法启动？"},
        {
            "role": "assistant",
            "content": "### 智能排障报告 【执行成功】\n\n**最终结论**：\n已确认 /etc/nginx/nginx.conf 第 45 行缺少分号导致的语法错误，已自动修复并成功重启服务。",
        },
        {"role": "user", "content": "再帮我检查一下防火墙规则是否开放了 80 和 443 端口？"},
        {
            "role": "assistant",
            "content": "### 智能排障报告 【执行成功】\n\n**最终结论**：\n已确认 iptables 防火墙已成功放行 80 与 443 端口，外部访问测试畅通无阻。",
        },
        {"role": "user", "content": "现在系统负载高不高？"},
        {"role": "assistant", "content": "系统当前 load average 为 0.05, 0.08, 0.12，CPU 占用低于 3%，处于轻载空闲状态。"},
    ]

    compacted = compactor.compact_session_history(history, current_user_prompt="网络延迟怎样？")

    # 验证前序轮次被紧凑折叠为小结
    assert any("[前序多轮会话历史小结]" in m["content"] for m in compacted)
    assert any("Nginx" in m["content"] or "防火墙" in m["content"] for m in compacted)


def test_react_compactor_hard_fallback_pruning_on_huge_recent_steps():
    """
    终极兜底测试：
    即使仅有 2 步（处于 min_recent_steps=2），但每步物理回显均极大（超过单步预算），
    验证触发最终兜底强制截断 (fallback_pruned=True)，且严格保障 total_tokens <= max_history_tokens。
    """
    compactor = ReActContextCompactor(window_size=3, max_history_tokens=300, min_recent_steps=2)

    # 仅 2 个步骤，但每个步骤有超长 Observation
    huge_obs_1 = "DEBUG LINE LOG: status 200 OK\n" * 100  # 约 3000 字符 / ~800 tokens
    huge_obs_2 = "KERNEL STACK TRACE: memory buffer fault\n" * 100  # 约 4000 字符 / ~1000 tokens

    steps = [
        {
            "step": 1,
            "title": "读取超长应用日志",
            "thought": "查看应用近期详细日志",
            "command": "cat /var/log/app.log",
            "observation": huge_obs_1,
            "analysis": "日志存在大量重复打印",
            "is_error": False,
        },
        {
            "step": 2,
            "title": "读取内核堆栈",
            "thought": "检查 dmesg 报错",
            "command": "dmesg | tail -n 100",
            "observation": huge_obs_2,
            "analysis": "发现内存缓冲区故障",
            "is_error": True,
        },
    ]

    summary, recent, _ = compactor.compact_react_history_with_stats(steps)

    # 验证最终兜底逻辑激活
    assert compactor.last_stats.get("fallback_pruned") is True
    # 验证强制裁剪后，总 Token 严格压制在预算以内 (允许微小分词浮动 +/- 20)
    assert compactor.last_stats["total_tokens"] <= compactor.max_history_tokens + 20

    # 格式化提示词历史
    formatted, _ = compactor.format_prompt_history_with_stats(steps)
    assert "已自动截断: 中间省略" in formatted
    # 验证 Token 估算器计算格式化文本整体不超标
    est_total = TokenBudgetEstimator.estimate_tokens(formatted)
    assert est_total <= compactor.max_history_tokens + 50


def test_session_compactor_hard_fallback_pruning_on_huge_recent_messages():
    """
    终极兜底测试：
    多轮会话在保留最近 2 条活跃消息时，若最近消息本身包含万字长文本，
    验证 SessionCompactor 最终兜底对活跃消息长内容进行强制二次截断。
    """
    compactor = SessionCompactor(max_recent_turns=4, max_session_tokens=150)

    huge_text = "这是一段非常冗长的运维执行日志与输出数据\n" * 40  # 约 900 字符 / ~500 tokens
    history = [
        {"role": "user", "content": "检查日志"},
        {"role": "assistant", "content": f"日志详情如下：\n{huge_text}"},
    ]

    compacted = compactor.compact_session_history(history, current_user_prompt="下一项检查")
    total_tokens = TokenBudgetEstimator.estimate_messages_tokens(compacted)

    # 验证最终严格压制在 max_session_tokens 附近
    assert total_tokens <= compactor.max_session_tokens + 30
    assert any("已自动截断" in m["content"] for m in compacted)


def test_extract_semantic_step_fact_ignores_zero_errors_log():
    """验证：日志回显中包含 '0 errors, 0 failed' 或 'no error' 时，不被误判为报错特征"""
    from application.react.compaction import extract_semantic_step_fact

    step_clean = {
        "step": 1,
        "title": "执行单元测试与编译",
        "command": "make test",
        "observation": (
            "Compiling modules...\n"
            "All unit test suites finished.\n"
            "Build complete with 0 errors, 0 failed, 45 passed.\n"
        ),
        "analysis": "编译与测试完成",
        "is_error": True,  # 哪怕外部由于返回码标记了异常，也不能截取 '0 errors' 当成报错原因
    }

    fact = extract_semantic_step_fact(step_clean)
    assert "0 errors" not in fact
    assert "0 failed" not in fact


def test_format_prompt_history_with_stats_isolation():
    """验证：format_prompt_history_with_stats 返回独立的局部 stats 字典，保障并发无串号"""
    compactor = ReActContextCompactor(window_size=2)
    steps = [
        {"step": 1, "title": "步骤1", "command": "cmd1", "observation": "obs1", "analysis": "ok1"},
        {"step": 2, "title": "步骤2", "command": "cmd2", "observation": "obs2", "analysis": "ok2"},
        {"step": 3, "title": "步骤3", "command": "cmd3", "observation": "obs3", "analysis": "ok3"},
    ]

    history_text, stats = compactor.format_prompt_history_with_stats(steps)
    assert isinstance(stats, dict)
    assert stats["compressed"] is True
    assert stats["recent_count"] == 2
    assert stats["older_count"] == 1
    assert "历史排查阶段性小结" in history_text


def test_max_observation_chars_explicit_override_not_overwritten():
    """验证：显式传入 max_observation_chars=2000 时，不被 128k 模型的画像默认值 6000 覆盖"""
    from application.react.engine import LangGraphReActEngine

    class MockModelClient:
        model = "glm-4-plus"

    engine = LangGraphReActEngine(
        llm_client=MockModelClient(),
        max_observation_chars=2000,
    )

    # 显式配置的 2000 必须被保留，绝不能因为魔法值 != 2000 变成 6000
    assert engine.context_compactor.max_observation_chars == 2000


def test_shared_context_pool_allocated_once_to_history_and_session():
    """验证共享预算池只扣一次工具与输出开销，再按比例分配给两个历史消费方"""
    registry = build_default_registry()

    class MockModelClient:
        model = "deepseek-chat"

    engine = LangGraphReActEngine(llm_client=MockModelClient(), tool_registry=registry)
    profile = resolve_context_profile("deepseek-chat")
    pool = profile["context_pool_tokens"]
    tool_tokens = TokenBudgetEstimator.estimate_tools_tokens(registry.specs())

    available = pool - tool_tokens - profile["max_output_tokens"]
    expected_history = int(available * 2 / 3)

    assert engine.context_compactor.max_history_tokens == expected_history
    assert engine.session_compactor.max_session_tokens == available - expected_history
    # 两个消费方预算 + 固定开销恰好等于共享池，证明固定开销未被重复扣减
    assert (
        engine.context_compactor.max_history_tokens
        + engine.session_compactor.max_session_tokens
        + tool_tokens
        + profile["max_output_tokens"]
    ) == pool


def test_shared_pool_keeps_floor_when_fixed_cost_exceeds_pool():
    """验证工具 schema 过多时共享池仍保留最低预算，不会被压到 0"""
    from application.react.compaction import allocate_context_budgets

    profile = resolve_context_profile("standard")
    pool = profile["context_pool_tokens"]

    history_budget, session_budget = allocate_context_budgets(profile, tool_tokens=pool * 10)

    assert history_budget + session_budget == int(pool * 0.4)
    assert history_budget > 0
    assert session_budget > 0


def test_shared_pool_warns_when_fixed_cost_clamped(caplog):
    """验证固定开销触发 40% 下限时输出告警，避免静默超窗"""
    from application.react.compaction import allocate_context_budgets

    profile = resolve_context_profile("standard")
    pool = profile["context_pool_tokens"]

    with caplog.at_level(logging.WARNING, logger="smartops.application.react.compaction.profiles"):
        allocate_context_budgets(profile, tool_tokens=pool)

    assert "clamped" in caplog.text


def test_allocate_context_budgets_supports_legacy_profile():
    """验证缺少共享池字段的旧画像仍可分配，不抛 KeyError"""
    from application.react.compaction import allocate_context_budgets

    history_budget, session_budget = allocate_context_budgets(
        {"max_history_tokens": 3000, "max_session_tokens": 2000},
        tool_tokens=0,
    )

    assert history_budget + session_budget == 5000


def test_compactors_accept_reserved_tokens_for_backward_compatibility():
    """验证外部调用仍可传入 reserved_tokens，不抛 TypeError 且按旧语义扣减"""
    react_compactor = ReActContextCompactor(max_history_tokens=10000, reserved_tokens=2000)
    session_compactor = SessionCompactor(max_session_tokens=5000, reserved_tokens=1000)

    assert react_compactor.max_history_tokens == 8000
    assert session_compactor.max_session_tokens == 4000


def test_1m_profile_resolution_precedes_128k():
    """验证 1M 画像优先匹配，避免 qwen2.5 等关键词抢先命中 128k 画像"""
    for model in ("gemini-2.5-pro", "qwen2.5-1m", "custom-1m-model"):
        profile = resolve_context_profile(model)
        assert profile["max_history_tokens"] == 32000
        assert profile["max_session_tokens"] == 16000
        assert profile["max_output_tokens"] == 6000

    # 非独立的 1m 片段不得误判为 1M 画像；qwen2.5-72b 仍应落在 128k
    assert resolve_context_profile("custom-10m-model")["context_pool_tokens"] == 5500
    assert resolve_context_profile("qwen2.5-72b")["context_pool_tokens"] == 24000

