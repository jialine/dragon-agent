/**
 * Panda TUI Backend — JSON-RPC client over stdin/stdout.
 *
 * Spawns `python -m panda tui` and communicates using newline-delimited
 * JSON-RPC 2.0 messages. Supports request/response, streaming via async
 * generators, and automatic reconnection.
 */

import { spawn, ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import { createInterface } from "node:readline";

// ── Types ──────────────────────────────────────────────────────────────

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: Record<string, unknown>;
}

export interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number | string;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

export interface JsonRpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

export type JsonRpcMessage = JsonRpcRequest | JsonRpcResponse | JsonRpcNotification;

export interface SessionInfo {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  platform: string;
  model: string;
  token_count: number;
  message_count: number;
}

export interface MessageInfo {
  role: string;
  content: string;
  timestamp: string;
  tool_calls?: ToolCallInfo[] | null;
}

export interface ToolCallInfo {
  name: string;
  args: Record<string, unknown>;
  result?: string | null;
  error?: string | null;
  status: "running" | "done" | "error";
}

export interface SkillInfo {
  name: string;
  description: string;
  tags: string[];
  version: number;
}

export interface ToolInfo {
  name: string;
  description: string;
  category: string;
  tags: string[];
}

export interface StreamChunk {
  type: "content" | "tool_call" | "tool_result" | "done" | "error";
  content?: string;
  tool_call?: ToolCallInfo;
  tool_result?: { name: string; result: string; error?: string };
  error?: string;
}

// ── Connection State ───────────────────────────────────────────────────

type ConnectionState = "disconnected" | "connecting" | "connected" | "reconnecting";

// ── Backend Client ─────────────────────────────────────────────────────

export class PandaBackend extends EventEmitter {
  private process: ChildProcess | null = null;
  private state: ConnectionState = "disconnected";
  private requestId = 0;
  private pending = new Map<number | string, {
    resolve: (value: unknown) => void;
    reject: (error: Error) => void;
  }>();
  private lineBuffer = "";
  private pythonPath: string;

  constructor(pythonPath = "python3") {
    super();
    this.pythonPath = pythonPath;
  }

  // ── Connection ─────────────────────────────────────────────────────

  get connectionState(): ConnectionState {
    return this.state;
  }

  async connect(): Promise<void> {
    if (this.state === "connected" || this.state === "connecting") {
      return;
    }

    this.state = "connecting";
    this.emit("stateChange", this.state);

    return new Promise((resolve, reject) => {
      try {
        this.process = spawn(this.pythonPath, ["-m", "panda", "tui"], {
          stdio: ["pipe", "pipe", "pipe"],
          env: { ...process.env, PYTHONUNBUFFERED: "1" },
        });

        const rl = createInterface({ input: this.process.stdout! });

        rl.on("line", (line: string) => {
          this._handleLine(line.trim());
        });

        this.process.stderr?.on("data", (data: Buffer) => {
          // Log stderr for debugging but don't treat as fatal
          const msg = data.toString().trim();
          if (msg) {
            this.emit("stderr", msg);
          }
        });

        this.process.on("error", (err: Error) => {
          this.state = "disconnected";
          this.emit("stateChange", this.state);
          this.emit("error", err);
          reject(err);
        });

        this.process.on("exit", (code: number | null) => {
          this.state = "disconnected";
          this.emit("stateChange", this.state);
          this.emit("disconnected", code);

          // Reject all pending requests
          for (const [, pending] of this.pending) {
            pending.reject(new Error(`Backend exited with code ${code}`));
          }
          this.pending.clear();

          // Auto-reconnect after a delay
          if (code !== 0) {
            setTimeout(() => this._reconnect(), 2000);
            this.emit("reconnecting");
          }
        });

        // Give it a moment to start, then mark connected
        setTimeout(() => {
          this.state = "connected";
          this.emit("stateChange", this.state);
          resolve();
        }, 500);

      } catch (err) {
        this.state = "disconnected";
        this.emit("stateChange", this.state);
        reject(err);
      }
    });
  }

  private async _reconnect(): Promise<void> {
    if (this.state === "connecting") return;
    this.state = "reconnecting";
    this.emit("stateChange", this.state);

    try {
      await this.connect();
    } catch {
      setTimeout(() => this._reconnect(), 5000);
    }
  }

  async disconnect(): Promise<void> {
    if (this.process) {
      // Send shutdown notification
      try {
        this._sendRaw({ jsonrpc: "2.0", method: "shutdown", params: {} });
      } catch { /* ignore */ }

      this.process.kill("SIGTERM");
      this.process = null;
    }
    this.state = "disconnected";
    this.emit("stateChange", this.state);
  }

  // ── Message Handling ────────────────────────────────────────────────

  private _handleLine(line: string): void {
    if (!line) return;

    try {
      const msg: JsonRpcMessage = JSON.parse(line);

      // Handle notifications (no id)
      if ("method" in msg && !("id" in msg)) {
        this.emit("notification", msg as JsonRpcNotification);
        // Also emit specific notification types
        this.emit(`notify:${msg.method}`, msg.params);
        return;
      }

      // Handle responses
      if ("id" in msg && ("result" in msg || "error" in msg)) {
        const resp = msg as JsonRpcResponse;
        const pending = this.pending.get(resp.id);
        if (pending) {
          this.pending.delete(resp.id);
          if (resp.error) {
            pending.reject(new Error(`RPC error ${resp.error.code}: ${resp.error.message}`));
          } else {
            pending.resolve(resp.result);
          }
        }
      }
    } catch {
      // Non-JSON line (e.g., raw stream output)
      this.emit("rawLine", line);
    }
  }

  private _sendRaw(msg: JsonRpcMessage): void {
    if (!this.process?.stdin || !this.process.stdin.writable) {
      throw new Error("Backend not connected");
    }
    this.process.stdin.write(JSON.stringify(msg) + "\n");
  }

  // ── RPC Methods ─────────────────────────────────────────────────────

  async call(method: string, params?: Record<string, unknown>): Promise<unknown> {
    if (this.state !== "connected") {
      throw new Error(`Backend not connected (state: ${this.state})`);
    }

    const id = ++this.requestId;
    const request: JsonRpcRequest = {
      jsonrpc: "2.0",
      id,
      method,
      params,
    };

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`RPC timeout: ${method}`));
      }, 30000);

      this.pending.set(id, {
        resolve: (value: unknown) => {
          clearTimeout(timeout);
          resolve(value);
        },
        reject: (err: Error) => {
          clearTimeout(timeout);
          reject(err);
        },
      });

      try {
        this._sendRaw(request);
      } catch (err) {
        clearTimeout(timeout);
        this.pending.delete(id);
        reject(err);
      }
    });
  }

  // ── Session Methods ─────────────────────────────────────────────────

  async listSessions(limit = 20): Promise<SessionInfo[]> {
    return this.call("sessions.list", { limit }) as Promise<SessionInfo[]>;
  }

  async createSession(title?: string): Promise<SessionInfo> {
    return this.call("session.create", { title }) as Promise<SessionInfo>;
  }

  async deleteSession(sessionId: string): Promise<boolean> {
    return this.call("session.delete", { session_id: sessionId }) as Promise<boolean>;
  }

  async getMessages(sessionId: string, limit = 100): Promise<MessageInfo[]> {
    return this.call("session.get_messages", {
      session_id: sessionId,
      limit,
    }) as Promise<MessageInfo[]>;
  }

  // ── Chat Methods ────────────────────────────────────────────────────

  async sendMessage(
    sessionId: string,
    content: string,
  ): Promise<{ content: string; tool_calls?: ToolCallInfo[] }> {
    return this.call("chat.send", {
      session_id: sessionId,
      content,
    }) as Promise<{ content: string; tool_calls?: ToolCallInfo[] }>;
  }

  /**
   * Stream a chat message, yielding chunks as they arrive.
   * The backend sends streaming responses as notifications with method "chat.stream".
   */
  async *streamMessage(
    sessionId: string,
    content: string,
  ): AsyncGenerator<StreamChunk, void, undefined> {
    // Subscribe to streaming notifications
    const chunks: StreamChunk[] = [];
    let done = false;
    let error: string | null = null;

    const onNotify = (params: unknown) => {
      const p = params as Record<string, unknown>;
      const chunk = p.chunk as StreamChunk;
      if (chunk) {
        chunks.push(chunk);
        if (chunk.type === "done" || chunk.type === "error") {
          if (chunk.type === "error") {
            error = chunk.error ?? "Unknown error";
          }
          done = true;
        }
      }
    };

    this.on("notify:chat.stream", onNotify);

    try {
      // Send the streaming request
      await this.call("chat.send_stream", {
        session_id: sessionId,
        content,
      });

      // Yield chunks as they arrive
      while (!done) {
        while (chunks.length > 0) {
          const chunk = chunks.shift()!;
          yield chunk;
          if (chunk.type === "done" || chunk.type === "error") {
            if (chunk.type === "error") {
              throw new Error(chunk.error ?? "Stream error");
            }
            return;
          }
        }
        // Wait a tick for more chunks
        await new Promise((r) => setTimeout(r, 50));
      }

      // Drain remaining
      while (chunks.length > 0) {
        yield chunks.shift()!;
      }

      if (error) {
        throw new Error(error);
      }
    } finally {
      this.off("notify:chat.stream", onNotify);
    }
  }

  // ── Skill Methods ───────────────────────────────────────────────────

  async listSkills(): Promise<SkillInfo[]> {
    return this.call("skills.list", {}) as Promise<SkillInfo[]>;
  }

  // ── Tool Methods ────────────────────────────────────────────────────

  async listTools(): Promise<ToolInfo[]> {
    return this.call("tools.list", {}) as Promise<ToolInfo[]>;
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<{
    success: boolean;
    output?: string;
    error?: string;
    latency_ms: number;
  }> {
    return this.call("tool.call", { name, args }) as Promise<{
      success: boolean;
      output?: string;
      error?: string;
      latency_ms: number;
    }>;
  }

  // ── Health ──────────────────────────────────────────────────────────

  async health(): Promise<{
    status: string;
    sessions: number;
    tools: number;
    skills: number;
  }> {
    return this.call("health", {}) as Promise<{
      status: string;
      sessions: number;
      tools: number;
      skills: number;
    }>;
  }
}

// ── Singleton ─────────────────────────────────────────────────────────

let _backend: PandaBackend | null = null;

export function getBackend(): PandaBackend {
  if (!_backend) {
    _backend = new PandaBackend();
  }
  return _backend;
}

export function createBackend(pythonPath?: string): PandaBackend {
  _backend = new PandaBackend(pythonPath);
  return _backend;
}
