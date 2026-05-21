/**
 * Chat.tsx — Chat panel component.
 *
 * Scrollable message list with user input at bottom.
 * Supports streaming message display and tool call visualization.
 */

import React, { useState, useRef, useEffect, useCallback } from "react";
import { Box, Text } from "ink";
import TextInput from "ink-text-input";
import Spinner from "ink-spinner";
import { ToolCall } from "./ToolCall.js";
import type { MessageInfo, ToolCallInfo, StreamChunk } from "../backend.js";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: string;
  toolCalls?: ToolCallInfo[];
  isStreaming?: boolean;
}

export interface ChatProps {
  messages: ChatMessage[];
  onSend: (content: string) => void;
  isLoading: boolean;
  sessionTitle?: string;
}

// Simple ID generator
let _msgId = 0;
function nextId(): string {
  return `msg_${++_msgId}_${Date.now()}`;
}

export const Chat: React.FC<ChatProps> = ({
  messages,
  onSend,
  isLoading,
  sessionTitle,
}) => {
  const [input, setInput] = useState("");
  const [visibleStart, setVisibleStart] = useState(0);
  const MAX_VISIBLE = 20; // Max messages to show at once

  // Auto-scroll to bottom when new messages arrive
  const prevLength = useRef(messages.length);
  useEffect(() => {
    if (messages.length > prevLength.current && messages.length > MAX_VISIBLE) {
      setVisibleStart(messages.length - MAX_VISIBLE);
    }
    prevLength.current = messages.length;
  }, [messages.length]);

  const handleSubmit = useCallback(
    (value: string) => {
      const trimmed = value.trim();
      if (trimmed && !isLoading) {
        onSend(trimmed);
        setInput("");
      }
    },
    [onSend, isLoading],
  );

  const visibleMessages = messages.slice(
    Math.max(0, messages.length - MAX_VISIBLE),
  );

  const formatTime = (ts: string): string => {
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  };

  return (
    <Box flexDirection="column" flexGrow={1} paddingX={1}>
      {/* Header */}
      <Box borderStyle="single" borderColor="gray" paddingX={1} marginBottom={1}>
        <Text bold color="cyan">
          🐼 {sessionTitle ?? "Chat"}
        </Text>
        {messages.length > MAX_VISIBLE && (
          <Text dimColor>
            {" "}
            ({messages.length} messages — showing last {MAX_VISIBLE})
          </Text>
        )}
      </Box>

      {/* Messages */}
      <Box flexDirection="column" flexGrow={1}>
        {messages.length === 0 && !isLoading && (
          <Box paddingY={1}>
            <Text dimColor>
              No messages yet. Type a message to start chatting with 🐼 Panda.
            </Text>
          </Box>
        )}

        {visibleMessages.map((msg) => (
          <Box key={msg.id} flexDirection="column" marginBottom={1}>
            {/* Role + timestamp */}
            <Box>
              <Text bold color={msg.role === "user" ? "green" : msg.role === "assistant" ? "cyan" : "yellow"}>
                {msg.role === "user" ? "▶ You" : msg.role === "assistant" ? "🐼 Panda" : msg.role}
              </Text>
              {msg.timestamp && (
                <Text dimColor> · {formatTime(msg.timestamp)}</Text>
              )}
              {msg.isStreaming && (
                <Text color="yellow">
                  {" "}
                  <Spinner type="dots" />
                </Text>
              )}
            </Box>

            {/* Content */}
            {msg.content ? (
              <Box marginLeft={2} flexDirection="column">
                <Text>{msg.content}</Text>
              </Box>
            ) : null}

            {/* Tool calls */}
            {msg.toolCalls?.map((tc, i) => (
              <Box key={`${msg.id}_tool_${i}`} marginLeft={2}>
                <ToolCall
                  name={tc.name}
                  args={tc.args}
                  status={tc.status}
                  result={tc.result}
                  error={tc.error ?? undefined}
                />
              </Box>
            ))}
          </Box>
        ))}

        {/* Loading indicator */}
        {isLoading && messages.length > 0 && messages[messages.length - 1]?.isStreaming && (
          <Box>
            <Text color="yellow">
              <Spinner type="dots" /> Panda is thinking...
            </Text>
          </Box>
        )}
      </Box>

      {/* Input bar */}
      <Box borderStyle="single" borderColor="blue" paddingX={1} paddingY={1}>
        <Text bold color="green">
          ▶{" "}
        </Text>
        {isLoading ? (
          <Text dimColor>Waiting for response...</Text>
        ) : (
          <TextInput
            value={input}
            onChange={setInput}
            onSubmit={handleSubmit}
            placeholder="Type a message... (Ctrl+D to send)"
          />
        )}
      </Box>
    </Box>
  );
};

// ── Helper to convert backend messages ───────────────────────────────

export function backendMessageToChat(msg: MessageInfo): ChatMessage {
  return {
    id: `bmsg_${msg.timestamp}_${Math.random().toString(36).slice(2, 8)}`,
    role: msg.role as ChatMessage["role"],
    content: msg.content,
    timestamp: msg.timestamp,
    toolCalls: (msg.tool_calls ?? undefined) as ToolCallInfo[] | undefined,
    isStreaming: false,
  };
}

export function createUserMessage(content: string): ChatMessage {
  return {
    id: nextId(),
    role: "user",
    content,
    timestamp: new Date().toISOString(),
    isStreaming: false,
  };
}

export function createAssistantPlaceholder(): ChatMessage {
  return {
    id: nextId(),
    role: "assistant",
    content: "",
    timestamp: new Date().toISOString(),
    isStreaming: true,
  };
}
