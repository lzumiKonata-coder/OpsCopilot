import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import { getWebSocketTerminalUrl } from '../../services/api';

export interface XTerminalRef {
  getBuffer: () => string;
  write: (data: string) => void;
  sendInput: (data: string) => void;
  clear: () => void;
  reconnect: () => void;
  focus: () => void;
  fit: () => void;
}

interface XTerminalProps {
  hostId: string;
  onStatusChange: (status: 'connecting' | 'connected' | 'disconnected' | 'error') => void;
  onData?: (data: string) => void;
}

export const XTerminal = forwardRef<XTerminalRef, XTerminalProps>(
  ({ hostId, onStatusChange, onData }, ref) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const terminalRef = useRef<Terminal | null>(null);
    const fitAddonRef = useRef<FitAddon | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const bufferRef = useRef<string>('');

    // 暴露给父组件调用的指令接口
    useImperativeHandle(ref, () => ({
      getBuffer: () => {
        if (!terminalRef.current) return bufferRef.current;
        const term = terminalRef.current;
        const lines: string[] = [];
        const buffer = term.buffer.active;
        const start = Math.max(0, buffer.length - 120);
        for (let i = start; i < buffer.length; i++) {
          const line = buffer.getLine(i);
          if (line) lines.push(line.translateToString(true));
        }
        return lines.join('\n');
      },
      write: (data: string) => {
        terminalRef.current?.write(data);
      },
      sendInput: (data: string) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(
            JSON.stringify({
              type: 'input',
              data: data.endsWith('\n') ? data : `${data}\n`,
            })
          );
        }
      },
      clear: () => {
        terminalRef.current?.clear();
      },
      reconnect: () => {
        connect();
      },
      focus: () => {
        terminalRef.current?.focus();
      },
      fit: () => {
        try {
          fitAddonRef.current?.fit();
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && terminalRef.current) {
            wsRef.current.send(
              JSON.stringify({
                type: 'resize',
                cols: terminalRef.current.cols,
                rows: terminalRef.current.rows,
              })
            );
          }
        } catch {
          // ignore
        }
      },
    }));

    const connect = () => {
      if (!hostId) return;

      // 关闭旧连接
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          // ignore
        }
        wsRef.current = null;
      }

      onStatusChange('connecting');

      const cols = terminalRef.current?.cols || 100;
      const rows = terminalRef.current?.rows || 30;
      const wsUrl = getWebSocketTerminalUrl(hostId, cols, rows);

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        onStatusChange('connected');
        // 同步窗口尺寸
        if (terminalRef.current) {
          ws.send(
            JSON.stringify({
              type: 'resize',
              cols: terminalRef.current.cols,
              rows: terminalRef.current.rows,
            })
          );
        }
      };

      ws.onmessage = (event) => {
        const text = typeof event.data === 'string' ? event.data : '';
        terminalRef.current?.write(text);
        bufferRef.current += text;
        if (bufferRef.current.length > 50000) {
          bufferRef.current = bufferRef.current.slice(-25000);
        }
        if (onData) onData(text);
      };

      ws.onclose = (ev) => {
        onStatusChange('disconnected');
        if (ev.code !== 1000 && ev.code !== 1005) {
          terminalRef.current?.write(
            `\r\n\x1b[33m[OpsCopilot] 终端会话连接已关闭 (code: ${ev.code})。如需重试请点击上方【重连】按钮。\x1b[0m\r\n`
          );
        }
      };

      ws.onerror = () => {
        onStatusChange('error');
      };
    };

    useEffect(() => {
      if (!containerRef.current) return;

      // 1. 初始化 Xterm 终端
      const term = new Terminal({
        cursorBlink: true,
        cursorStyle: 'block',
        fontSize: 13,
        fontFamily: "'JetBrains Mono', Consolas, 'Courier New', monospace",
        // 允许非不透明背景，让终端面板的壁纸背景透出
        allowTransparency: true,
        theme: {
          background: 'rgba(9, 13, 19, 0.25)',
          foreground: '#e6edf3',
          cursor: '#58a6ff',
          selectionBackground: 'rgba(56, 139, 253, 0.4)',
          black: '#0d1117',
          red: '#ff7b72',
          green: '#3fb950',
          yellow: '#d29922',
          blue: '#58a6ff',
          magenta: '#bc8cff',
          cyan: '#39c5cf',
          white: '#b1bac4',
          brightBlack: '#6e7681',
          brightRed: '#ffa198',
          brightGreen: '#56d364',
          brightYellow: '#e3b341',
          brightBlue: '#79c0ff',
          brightMagenta: '#d2a8ff',
          brightCyan: '#56d4dd',
          brightWhite: '#f0f6fc',
        },
        convertEol: true,
        scrollback: 5000,
      });

      const fitAddon = new FitAddon();
      const webLinksAddon = new WebLinksAddon();

      term.loadAddon(fitAddon);
      term.loadAddon(webLinksAddon);

      const copySelection = () => {
        const selection = term.getSelection();
        if (selection) {
          void navigator.clipboard?.writeText(selection);
        }
      };

      // 浏览器/DevTools 独占 Ctrl+Shift+C（无法被页面拦截），改用 Ctrl+X 复制；
      // 粘贴不再自行拦截，交给浏览器原生 paste 事件写入终端 textarea
      term.attachCustomKeyEventHandler((event) => {
        if (event.type !== 'keydown') return true;
        if (event.ctrlKey && !event.shiftKey && !event.altKey && event.key.toLowerCase() === 'x') {
          // 无选中内容时保留终端默认 Ctrl+X 行为（发送 ^X）
          if (!term.getSelection()) return true;
          event.preventDefault();
          copySelection();
          return false;
        }
        return true;
      });

      term.open(containerRef.current);
      fitAddon.fit();

      terminalRef.current = term;
      fitAddonRef.current = fitAddon;

      // 键盘输入监听
      term.onData((data) => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: 'input', data }));
        }
      });

      // 窗口尺寸变更监听
      const handleResize = () => {
        try {
          fitAddon.fit();
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(
              JSON.stringify({
                type: 'resize',
                cols: term.cols,
                rows: term.rows,
              })
            );
          }
        } catch {
          // ignore
        }
      };

      const resizeObserver = new ResizeObserver(() => {
        handleResize();
      });
      resizeObserver.observe(containerRef.current);

      window.addEventListener('resize', handleResize);

      // 启动 WebSocket 连接
      connect();

      return () => {
        window.removeEventListener('resize', handleResize);
        resizeObserver.disconnect();
        if (wsRef.current) {
          wsRef.current.close();
          wsRef.current = null;
        }
        term.dispose();
      };
    }, [hostId]);

    return (
      <div
        className="xterm-wrapper"
        ref={containerRef}
        style={{ width: '100%', height: '100%', position: 'relative' }}
      />
    );
  }
);
