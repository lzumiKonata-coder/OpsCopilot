"""
针对大模型中文伪指令穿透终端与重复命令死循环的防护测试
(Anti-Hallucination & Anti-Loop Guard Tests)
"""

import pytest

from application.ports import ILLMClient, ISSHGateway
from application.react.engine import LangGraphReActEngine
from application.react.text_utils import parse_react_response
from domain.policies.intent import (
    is_multi_target_read_only_prompt,
    is_mutation_task,
    is_read_only_query_prompt,
)
from utils.shell import clean_extracted_command, is_valid_shell_command
from domain.entities import AuthType, Command, ExecutionResult, SSHConnection


def test_is_valid_shell_command_rejects_chinese_pseudo_commands():
    """验证严格命令语法边界：坚决拦截以中文字符开头的自然语言陈述"""
    # 非法指令（纯中文自然语言陈述）
    assert is_valid_shell_command("无需执行安装命令") is False
    assert is_valid_shell_command("跳过安装") is False
    assert is_valid_shell_command("已安装并且是最新版本") is False
    assert is_valid_shell_command("查看当前系统") is False
    assert is_valid_shell_command("无须任何处理") is False
    assert is_valid_shell_command("") is False
    assert is_valid_shell_command("   ") is False

    # 合法 Linux Shell 指令
    assert is_valid_shell_command("yum install -y docker-ce") is True
    assert is_valid_shell_command("sudo systemctl status docker") is True
    assert is_valid_shell_command("/usr/bin/docker-ce --version") is True
    assert is_valid_shell_command("export PATH=$PATH:/usr/bin") is True
    assert is_valid_shell_command("echo '无需执行安装命令'") is True
    assert is_valid_shell_command("grep '测试' /var/log/messages") is True


def test_clean_extracted_command_cleans_and_rejects_chinese():
    """验证 clean_extracted_command 剥离标签并清除非法中文伪命令"""
    # 正常命令带 markdown 与引号
    assert clean_extracted_command("`yum install -y docker-ce`") == "yum install -y docker-ce"
    assert clean_extracted_command("```bash\nsystemctl restart nginx\n```") == "systemctl restart nginx"

    # 中文伪命令被彻底清空为 ""
    assert clean_extracted_command("无需执行安装命令") == ""
    assert clean_extracted_command("`无需执行安装命令`") == ""
    assert clean_extracted_command("跳过安装步骤") == ""


def test_parse_react_response_recovers_when_action_is_chinese():
    """验证当模型输出 Action: 无需执行安装命令 时，系统将其清洗为 None 并优雅降级为最终总结"""
    raw_reply = (
        "Thought: 系统中已经安装了最新版本的 Docker，无需再次安装。\n"
        "Action: 无需执行安装命令\n"
    )
    parsed = parse_react_response(raw_reply)
    # Action 被合法性校验拦截为 None，杜绝发送至 bash
    assert parsed["action"] is None
    # 自动将 Thought 沉淀为 final_answer
    assert parsed["final_answer"] is not None
    assert "无需再次安装" in parsed["final_answer"] or "已经安装" in parsed["final_answer"]


@pytest.mark.asyncio
async def test_analyze_observation_recognizes_package_already_installed():
    """验证当 Yum 回显'已安装并且是最新版本'时，系统正确归纳事实并提示正确引导方向"""
    yum_cmd = "yum install -y docker-ce docker-ce-cli containerd.io"
    yum_obs = (
        "已加载插件: fastestmirror, langpacks\n"
        "软件包 3:docker-ce-26.1.4-1.el7.x86_64 已安装并且是最新版本\n"
        "软件包 1:docker-ce-cli-26.1.4-1.el7.x86_64 已安装并且是最新版本\n"
        "无须任何处理\n"
    )

    analysis = await LangGraphReActEngine.analyze_observation(yum_cmd, yum_obs)
    assert "软件包已就绪确认" in analysis
    assert "无需重复下发安装命令" in analysis
    assert "systemctl status" in analysis


class MockLoopingLLM(ILLMClient):
    def __init__(self):
        self.call_count = 0
        # 持续无脑重复下发完全相同的 export 指令
        self.responses = [
            "Thought: 导出环境变量。\nAction: export PATH=$PATH:/usr/bin\nStep_Title: 配置环境",
            "Thought: 再次导出。\nAction: export PATH=$PATH:/usr/bin\nStep_Title: 配置环境",
            "Thought: 第三次导出。\nAction: export PATH=$PATH:/usr/bin\nStep_Title: 配置环境",
            "Thought: 第四次导出。\nAction: export PATH=$PATH:/usr/bin\nStep_Title: 配置环境",
        ]

    async def chat(self, messages: list, temperature: float = 0.2, tools=None) -> str:
        resp = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1
        return resp

    async def stream_chat(self, messages: list):
        yield await self.chat(messages)


class DummySSHGateway(ISSHGateway):
    async def execute(self, host: SSHConnection, command: Command) -> ExecutionResult:
        return ExecutionResult(stdout="", stderr="", exit_status=0)

    async def test_connection(self, host: SSHConnection) -> bool:
        return True


@pytest.mark.asyncio
async def test_anti_loop_guard_breaks_on_3_consecutive_identical_commands():
    """验证防死循环通用机制：连续 3 次执行完全相同指令强制熔断终止"""
    llm = MockLoopingLLM()
    gateway = DummySSHGateway()
    engine = LangGraphReActEngine(llm_client=llm, ssh_gateway=gateway)

    host = SSHConnection(name="test", host="127.0.0.1", port=22, username="root", auth_type=AuthType.PASSWORD, password="pwd")
    initial_state = {
        "host": host,
        "user_prompt": "排查命令环境",
        "session_id": "test_loop_break",
        "max_steps": 10,
        "step_idx": 1,
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "is_blocked": False,
        "final_answer": None,
        "final_conclusion": None,
        "is_success": True,
        "should_stop": False,
        "emitted_events": [],
    }

    graph = engine.build_graph()
    last_state = dict(initial_state)

    async for chunk in graph.astream(initial_state, stream_mode="updates"):
        for node_name, update in chunk.items():
            for k, v in update.items():
                if k != "emitted_events":
                    last_state[k] = v

    # 验证在第 3 次重复时被强制熔断收敛，没有继续空转至 max_steps=10
    assert len(last_state.get("executed_steps", [])) <= 3
    conclusion = last_state.get("final_conclusion") or ""
    assert "状态机安全熔断" in conclusion
    assert "连续 3 次执行完全相同指令" in conclusion


def test_read_only_prompts_are_not_mutation_tasks():
    """验证只读请求即使包含“配置/安装/启动/停止”等词，也不会被误判为变更任务"""
    read_only_prompts = [
        "查看 Nginx 配置",
        "查看已安装的软件",
        "检查服务是否已启动",
        "帮我查看磁盘",
        "显示服务启动状态",
        "列出已安装软件",
        "确认端口是否开放",
        "查看重启记录",
        "检查服务是否需要重启",
        "查看配置修改记录",
        "看下 docker 服务是否停止",
        "查看服务停止原因",
    ]
    for prompt in read_only_prompts:
        assert is_mutation_task(prompt) is False, f"只读请求被误判为变更任务: {prompt}"


def test_explicit_change_prompts_are_still_mutation_tasks():
    """验证只读动词后追加明确变更动作，或直接以变更动作开头的请求，仍被判为变更任务"""
    mutation_prompts = [
        "修改 Nginx 配置",
        "安装 Docker",
        "重启 nginx 服务",
        "删除 /tmp/a.log",
        "帮我安装 docker",
        "创建 deploy 用户",
        "部署 nginx",
        "查看日志后重启服务",
        "检查磁盘告警并清理日志",
        "查看配置后再启动服务",
    ]
    for prompt in mutation_prompts:
        assert is_mutation_task(prompt) is True, f"变更请求未被识别为变更任务: {prompt}"


def test_read_only_query_intent_is_consistent_with_mutation_detection():
    """验证只读查询判断与变更任务判断使用同一套显式只读意图规则"""
    read_only_prompts = [
        "查看已安装的软件",
        "帮我查看磁盘",
        "看下服务是否停止",
        "列出已安装软件",
        "确认端口是否开放",
    ]
    for prompt in read_only_prompts:
        assert is_read_only_query_prompt(prompt) is True, f"只读请求未被识别: {prompt}"
        assert is_mutation_task(prompt) is False, f"只读请求被误判为变更任务: {prompt}"

    assert is_read_only_query_prompt("查看日志后重启服务") is False
    assert is_mutation_task("查看日志后重启服务") is True


def test_multi_target_read_only_prompt_detection():
    """验证多目标只读查询能被识别，避免首条命令成功后过早收敛"""
    assert is_multi_target_read_only_prompt("查看磁盘和内存") is True
    assert is_multi_target_read_only_prompt("查看 nginx 配置和日志") is True
    assert is_multi_target_read_only_prompt("查看磁盘、内存、CPU") is True
    assert is_multi_target_read_only_prompt("查看磁盘 and 内存") is True

    assert is_multi_target_read_only_prompt("查看磁盘") is False
    assert is_multi_target_read_only_prompt("查看日志后重启服务") is False


class StaticCommandLLM(ILLMClient):
    """固定返回下一步命令的测试模型"""

    def __init__(self):
        self.call_count = 0

    async def chat(self, messages: list, temperature: float = 0.2, tools=None):
        self.call_count += 1
        return "Thought: 继续查看内存。\nAction: free -m\nStep_Title: 查看内存"

    async def stream_chat(self, messages: list):
        yield await self.chat(messages)


@pytest.mark.asyncio
async def test_multi_target_query_does_not_converge_after_first_command():
    """验证多目标只读查询在第一条命令成功后不会强制收敛，单目标查询仍会立即收敛"""
    host = SSHConnection(
        name="multi-query-host",
        host="127.0.0.1",
        port=22,
        username="root",
        auth_type=AuthType.PASSWORD,
        password="pwd",
    )
    base_state = {
        "host": host,
        "session_id": "test_multi_query",
        "max_steps": None,
        "step_idx": 2,
        "planned_steps_queue": [],
        "react_history": [],
        "executed_steps": [
            {
                "step": 1,
                "thought": "查看磁盘",
                "title": "查看磁盘",
                "command": "df -h",
                "observation": "Filesystem Size Used Avail Use% Mounted on\n/dev/sda1 50G 20G 30G 40% /",
                "analysis": "已获取磁盘容量",
                "is_error": False,
            }
        ],
        "session_history": [],
        "has_encountered_error": False,
        "consecutive_errors": 0,
        "is_blocked": False,
        "final_answer": None,
        "final_conclusion": None,
        "is_success": False,
        "should_stop": False,
        "emitted_events": [],
    }

    engine = LangGraphReActEngine(llm_client=StaticCommandLLM())

    multi_state = {**base_state, "user_prompt": "查看磁盘和内存"}
    multi_result = await engine._node_think(multi_state)
    assert multi_result.get("should_stop") is not True
    assert multi_result.get("current_command") == "free -m"

    single_engine = LangGraphReActEngine(llm_client=StaticCommandLLM())
    single_state = {**base_state, "user_prompt": "查看磁盘"}
    single_result = await single_engine._node_think(single_state)
    assert single_result.get("should_stop") is True
    assert "查询结果详情" in (single_result.get("final_answer") or "")
    assert single_engine.llm_client.call_count == 1
