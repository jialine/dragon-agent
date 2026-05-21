/**
 * ToolCall.tsx — Tool call visualization card.
 *
 * Shows: tool name, args summary, spinner while running,
 * result with expand/collapse toggle.
 */

import React, { useState } from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";

export interface ToolCallProps {
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done" | "error";
  result?: string | null;
  error?: string | null;
  latencyMs?: number;
}

export const ToolCall: React.FC<ToolCallProps> = ({
  name,
  args,
  status,
  result,
  error,
  latencyMs,
}) => {
  const [expanded, setExpanded] = useState(false);
  const toggleExpanded = () => setExpanded((prev) => !prev);

  const argsSummary = Object.entries(args)
    .slice(0, 3)
    .map(([k, v]) => {
      const sv = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}=${sv.length > 40 ? sv.slice(0, 40) + "..." : sv}`;
    })
    .join(", ");

  const statusIcon =
    status === "running" ? (
      <Text color="yellow">
        <Spinner type="dots" />{" "}
      </Text>
    ) : status === "error" ? (
      <Text color="red">✗ </Text>
    ) : (
      <Text color="green">✓ </Text>
    );

  const statusColor = status === "running" ? "yellow" : status === "error" ? "red" : "green";

  return (
    <Box flexDirection="column" marginY={1}>
      {/* Header row */}
      <Box>
        {statusIcon}
        <Text bold color="cyan">
          🔧 {name}
        </Text>
        {argsSummary ? (
          <Text color="gray" dimColor>
            {" "}
            ({argsSummary})
          </Text>
        ) : null}
        {latencyMs !== undefined && status !== "running" ? (
          <Text color="gray" dimColor>
            {" "}
            [{latencyMs}ms]
          </Text>
        ) : null}
      </Box>

      {/* Status line */}
      <Box marginLeft={2}>
        <Text color={statusColor}>
          {status === "running"
            ? "Running..."
            : status === "error"
            ? `Failed: ${error ?? "Unknown error"}`
            : "Complete"}
        </Text>
        {(result || error) && (
          <Text color="gray">
            {" "}
            —{" "}
            <Text color="blue" dimColor={!expanded}>
              [{expanded ? "collapse" : "expand"}]
            </Text>
          </Text>
        )}
      </Box>

      {/* Expanded result/error */}
      {expanded && result && (
        <Box
          marginLeft={2}
          marginTop={1}
          borderStyle="round"
          borderColor="gray"
          paddingX={1}
          flexDirection="column"
        >
          <Text dimColor>{result.slice(0, 500)}</Text>
          {result.length > 500 && <Text dimColor>... (truncated)</Text>}
        </Box>
      )}
      {expanded && error && (
        <Box
          marginLeft={2}
          marginTop={1}
          borderStyle="round"
          borderColor="red"
          paddingX={1}
          flexDirection="column"
        >
          <Text color="red">{error}</Text>
        </Box>
      )}
    </Box>
  );
};
