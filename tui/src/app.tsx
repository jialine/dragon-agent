/**
 * Panda TUI — Main Application
 *
 * Ink React TUI frontend for Panda Agent.
 * Vertical split layout: sidebar + main chat area.
 * Keyboard shortcuts for session management.
 */

import React, { useState, useEffect, useCallback, useRef } from "react";
import { Box, Text, render, useInput, useApp } from "ink";
import Gradient from "ink-gradient";
import { Chat, type ChatMessage, createUserMessage, createAssistantPlaceholder, backendMessageToChat } from "./components/Chat.js";
import { Sidebar } from "./components/Sidebar.js";
import { PandaBackend, getBackend } from "./backend.js";
import type { SessionInfo, SkillInfo, ToolInfo, StreamChunk } from "./backend.js";

// ── Main App Component ──────────────────────────────────────────────────

const App: React.FC = () => {
  const { exit } = useApp();
  const backendRef = useRef<PandaBackend>(getBackend());

  // ── State ──────────────────────────────────────────────────────────

  const [connectionState, setConnectionState] = useState<string>("disconnected");
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeSessionTitle, setActiveSessionTitle] = useState<string>("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showWelcome, setShowWelcome] = useState(true);

  // ── Initialize Backend ─────────────────────────────────────────────

  useEffect(() => {
    const backend = backendRef.current;

    const onStateChange = (state: string) => {
      setConnectionState(state);
    };

    const onError = (err: Error) => {
      setError(`Backend error: ${err.message}`);
    };

    const onDisconnected = (code: number | null) => {
      setConnectionState("disconnected");
      if (code !== 0) {
        setError(`Backend exited with code ${code}. Reconnecting...`);
      }
    };

    const onReconnecting = () => {
      setError("Connection lost. Reconnecting...");
    };

    backend.on("stateChange", onStateChange);
    backend.on("error", onError);
    backend.on("disconnected", onDisconnected);
    backend.on("reconnecting", onReconnecting);

    // Connect
    backend
      .connect()
      .then(() => {
        setError(null);
        return refreshData();
      })
      .catch((err: Error) => {
        setError(`Failed to connect: ${err.message}`);
      });

    return () => {
      backend.off("stateChange", onStateChange);
      backend.off("error", onError);
      backend.off("disconnected", onDisconnected);
      backend.off("reconnecting", onReconnecting);
      backend.disconnect().catch(() => {});
    };
  }, []);

  // ── Data Refresh ───────────────────────────────────────────────────

  const refreshData = useCallback(async () => {
    const backend = backendRef.current;
    if (backend.connectionState !== "connected") return;

    try {
      const [sessList, skillList, toolList] = await Promise.all([
        backend.listSessions(30),
        backend.listSkills(),
        backend.listTools(),
      ]);
      setSessions(sessList);
      setSkills(skillList);
      setTools(toolList);

      // Auto-select first session if none active
      if (!activeSessionId && sessList.length > 0) {
        const first = sessList[0];
        setActiveSessionId(first.id);
        setActiveSessionTitle(first.title);
        await loadSessionMessages(first.id);
      }
    } catch (err) {
      setError(`Failed to refresh: ${(err as Error).message}`);
    }
  }, [activeSessionId]);

  const loadSessionMessages = useCallback(async (sessionId: string) => {
    const backend = backendRef.current;
    if (backend.connectionState !== "connected") return;

    try {
      const msgs = await backend.getMessages(sessionId, 100);
      setMessages(msgs.map(backendMessageToChat));
      setShowWelcome(false);
    } catch (err) {
      setError(`Failed to load messages: ${(err as Error).message}`);
    }
  }, []);

  // ── Actions ────────────────────────────────────────────────────────

  const handleSend = useCallback(
    async (content: string) => {
      const backend = backendRef.current;
      if (backend.connectionState !== "connected") {
        setError("Not connected to backend");
        return;
      }

      setError(null);
      setIsLoading(true);
      setShowWelcome(false);

      // Add user message
      const userMsg = createUserMessage(content);
      setMessages((prev) => [...prev, userMsg]);

      // Add assistant placeholder
      const assistantMsg = createAssistantPlaceholder();
      setMessages((prev) => [...prev, assistantMsg]);

      try {
        // Try streaming first
        const streamChunks: StreamChunk[] = [];
        let streamError = false;

        try {
          for await (const chunk of backend.streamMessage(
            activeSessionId ?? "default",
            content,
          )) {
            streamChunks.push(chunk);
          }
        } catch {
          // Streaming not supported, fall back to non-streaming
          streamError = true;
        }

        if (streamError) {
          // Fall back to non-streaming
          const result = await backend.sendMessage(
            activeSessionId ?? "default",
            content,
          );

          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id
                ? {
                    ...m,
                    content: result.content,
                    isStreaming: false,
                    toolCalls: result.tool_calls ?? undefined,
                    timestamp: new Date().toISOString(),
                  }
                : m,
            ),
          );
        } else {
          // Build content from stream chunks
          let accumulatedContent = "";
          const toolCalls: StreamChunk[] = [];

          for (const chunk of streamChunks) {
            if (chunk.type === "content" && chunk.content) {
              accumulatedContent += chunk.content;
            } else if (chunk.type === "tool_call" || chunk.type === "tool_result") {
              toolCalls.push(chunk);
            }
          }

          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id
                ? {
                    ...m,
                    content: accumulatedContent,
                    isStreaming: false,
                    toolCalls: toolCalls.map((tc) => ({
                      name: tc.tool_call?.name ?? tc.tool_result?.name ?? "unknown",
                      args: tc.tool_call?.args ?? {},
                      status: tc.type === "tool_result" ? "done" as const : "running" as const,
                      result: tc.tool_result?.result ?? null,
                      error: tc.tool_result?.error ?? null,
                    })),
                    timestamp: new Date().toISOString(),
                  }
                : m,
            ),
          );
        }

        // Refresh sessions to update message counts
        refreshData();
      } catch (err) {
        setError(`Send failed: ${(err as Error).message}`);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: `Error: ${(err as Error).message}`, isStreaming: false }
              : m,
          ),
        );
      } finally {
        setIsLoading(false);
      }
    },
    [activeSessionId, refreshData],
  );

  const handleNewSession = useCallback(async () => {
    const backend = backendRef.current;
    if (backend.connectionState !== "connected") return;

    try {
      const sess = await backend.createSession("New Session");
      setSessions((prev) => [sess, ...prev]);
      setActiveSessionId(sess.id);
      setActiveSessionTitle(sess.title);
      setMessages([]);
      setShowWelcome(true);
      setError(null);
    } catch (err) {
      setError(`Failed to create session: ${(err as Error).message}`);
    }
  }, []);

  const handleDeleteSession = useCallback(async () => {
    const backend = backendRef.current;
    if (!activeSessionId || backend.connectionState !== "connected") return;

    try {
      await backend.deleteSession(activeSessionId);
      setSessions((prev) => prev.filter((s) => s.id !== activeSessionId));

      // Select next session or clear
      const remaining = sessions.filter((s) => s.id !== activeSessionId);
      if (remaining.length > 0) {
        const next = remaining[0];
        setActiveSessionId(next.id);
        setActiveSessionTitle(next.title);
        loadSessionMessages(next.id);
      } else {
        setActiveSessionId(null);
        setActiveSessionTitle("");
        setMessages([]);
      }
      setError(null);
    } catch (err) {
      setError(`Failed to delete session: ${(err as Error).message}`);
    }
  }, [activeSessionId, sessions, loadSessionMessages]);

  const handleSelectSession = useCallback(
    (sessionId: string) => {
      const sess = sessions.find((s) => s.id === sessionId);
      if (sess) {
        setActiveSessionId(sessionId);
        setActiveSessionTitle(sess.title);
        loadSessionMessages(sessionId);
        setShowWelcome(false);
      }
    },
    [sessions, loadSessionMessages],
  );

  // ── Keyboard Input ──────────────────────────────────────────────────

  useInput((input, key) => {
    // Ctrl+N: New session
    if (key.ctrl && input === "n") {
      handleNewSession();
      return;
    }

    // Ctrl+D: Delete active session
    if (key.ctrl && input === "d") {
      handleDeleteSession();
      return;
    }

    // Ctrl+R: Refresh data
    if (key.ctrl && input === "r") {
      refreshData();
      return;
    }

    // Tab/Shift+Tab: Cycle through sessions
    if (key.tab) {
      const currentIdx = sessions.findIndex((s) => s.id === activeSessionId);
      if (sessions.length === 0) return;

      const nextIdx = key.shift
        ? (currentIdx - 1 + sessions.length) % sessions.length
        : (currentIdx + 1) % sessions.length;

      const nextSession = sessions[nextIdx];
      handleSelectSession(nextSession.id);
      return;
    }
  });

  // ── Render ──────────────────────────────────────────────────────────

  return (
    <Box flexDirection="column" width="100%" height="100%">
      {/* Top gradient banner */}
      <Box marginBottom={1}>
        <Gradient name="summer">
          <Text bold>🐼 Panda Agent TUI — Self-Evolving AI Agent Framework</Text>
        </Gradient>
      </Box>

      {/* Error banner */}
      {error && (
        <Box marginBottom={1} paddingX={1}>
          <Text backgroundColor="red" color="white" bold>
            {" ⚠ "}{error}
          </Text>
        </Box>
      )}

      {/* Main layout: sidebar + chat */}
      <Box flexDirection="row" flexGrow={1}>
        {/* Sidebar */}
        <Sidebar
          sessions={sessions}
          activeSessionId={activeSessionId}
          skills={skills}
          tools={tools}
          connectionState={connectionState}
          onSelectSession={handleSelectSession}
        />

        {/* Divider */}
        <Box flexShrink={0}>
          <Text color="gray">│</Text>
        </Box>

        {/* Chat area */}
        <Chat
          messages={messages}
          onSend={handleSend}
          isLoading={isLoading}
          sessionTitle={activeSessionTitle || undefined}
        />
      </Box>

      {/* Footer */}
      <Box marginTop={1} paddingX={1}>
        <Text dimColor>
          🐼 Panda Agent · {connectionState} · Sessions: {sessions.length} · Skills: {skills.length} · Tools: {tools.length}
        </Text>
      </Box>
    </Box>
  );
};

// ── Entry Point ────────────────────────────────────────────────────────

// Handle Ctrl+C gracefully
process.on("SIGINT", () => {
  const backend = getBackend();
  backend.disconnect().finally(() => {
    process.exit(0);
  });
});

process.on("SIGTERM", () => {
  const backend = getBackend();
  backend.disconnect().finally(() => {
    process.exit(0);
  });
});

// Render the app
const { waitUntilExit } = render(<App />);

// Export for potential testing
export default App;
