# OpsCopilot Web Client (前后端分离 React + TypeScript 客户端)

基于 **React 19 + TypeScript + Vite + Xterm.js** 构建的企业级 WebSSH 智能终端与智能排障协同工作台。

---

## 1. 核心特性与架构

- **前后端解耦架构**：
  - 前端：位于 `D:\PythonProject\ssh-client`，由 React 19 + TypeScript + Vite 驱动。
  - 后端：位于 `D:\PythonProject\ssh-agent`，基于 FastAPI + LangGraph + Uvicorn 提供 REST API、SSE 流与全双工 WebSocket PTY。
- **WebSSH 终端双向流转**：
  - 基于 `@xterm/xterm` 与 `@xterm/addon-fit`，提供低延迟交互式终端。
  - 键盘输入、窗口动态 Resize、ANSI 颜色代码解析全通道支持。
- **智能排障 Agent 深度联动**：
  - 自动捕获终端屏幕最新缓冲区，为 Agent 提供真实环境态势感知。
  - 基于 LangGraph 状态机实现的 ReAct 闭环（Thought -> Plan -> Action -> Observation -> Final Answer）。
- **人机协同高危审批门禁（免阻断弹窗）**：
  - 遇到高危或破坏性指令（如 `rm -rf`、磁盘写入、进程强杀、系统重启）时，在聊天流中以结构化【风险提示卡片】呈现。
  - 详细告知破坏性影响，把最高控制权与选择权交给运维用户，提供【拒绝执行】与【批准执行】。

---

## 2. 快速启动

### 2.1 启动后端服务 (FastAPI)
确保 `ssh-agent` 虚拟环境与服务运行在 `8000` 端口：
```bash
cd D:\PythonProject\ssh-agent
.venv\Scripts\python.exe -m uvicorn adapter.web.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2.2 启动前端服务 (Vite Dev Server)
在 `ssh-client` 目录执行：
```bash
cd D:\PythonProject\ssh-client
npm install
npm run dev
```
启动后在浏览器中打开：**`http://localhost:5173`**

### 2.3 构建生产分发产物
```bash
npm run build
```
产物将输出至 `dist/` 目录。

---

## 3. 目录组织

```text
ssh-client/
├── src/
│   ├── components/
│   │   ├── agent/           # SRE 排障对话组件与审批卡片
│   │   │   ├── ApprovalCard.tsx       # 高危命令风险研判与审批决断卡片
│   │   │   ├── ChatInput.tsx          # 智能输入框与快捷提示词
│   │   │   ├── ChatMessageItem.tsx    # 思考流、动作、回显与报告渲染
│   │   │   └── ReActStepCard.tsx      # ReAct 单步折叠卡片组件
│   │   ├── common/          # 通用导航与基础组件
│   │   ├── host/            # 主机资产卡片与登记模态框
│   │   └── terminal/        # XTerm 终端组件与头部工具栏
│   │       ├── TerminalHeader.tsx
│   │       └── XTerminal.tsx
│   ├── services/            # 后端通信服务
│   │   ├── api.ts           # Axios 实例与 WebSocket URL 构造器
│   │   ├── hostService.ts   # 主机资产 CRUD 与网络连通性检测
│   │   ├── sessionService.ts# 会话生命周期与里程碑
│   │   └── agentService.ts  # SSE 流式推演与审批接口
│   ├── types/               # 严格 TypeScript 类型定义
│   ├── views/               # 主视图
│   │   ├── DashboardView.tsx     # 主机资产大盘
│   │   └── TerminalWorkspace.tsx # 终端与 Agent 双屏工作区
│   ├── App.tsx
│   ├── index.css
│   └── main.tsx
├── package.json
├── tsconfig.json
└── vite.config.ts
```
