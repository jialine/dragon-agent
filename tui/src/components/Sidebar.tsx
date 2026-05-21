/**
 * Sidebar.tsx — Session list, skill list, tool count, and status.
 *
 * Vertical sidebar with:
 * - Sessions list with active indicator
 * - Skills list
 * - Tool count
 * - Status indicator
 */

import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import type { SessionInfo, SkillInfo, ToolInfo } from "../backend.js";

export interface SidebarProps {
  sessions: SessionInfo[];
  activeSessionId: string | null;
  skills: SkillInfo[];
  tools: ToolInfo[];
  connectionState: string;
  onSelectSession: (sessionId: string) => void;
  width?: number;
}

const SIDEBAR_WIDTH = 28;

export const Sidebar: React.FC<SidebarProps> = ({
  sessions,
  activeSessionId,
  skills,
  tools,
  connectionState,
  onSelectSession,
  width = SIDEBAR_WIDTH,
}) => {
  const statusColor =
    connectionState === "connected"
      ? "green"
      : connectionState === "connecting" || connectionState === "reconnecting"
      ? "yellow"
      : "red";

  const statusIcon =
    connectionState === "connected" ? (
      <Text color="green">●</Text>
    ) : connectionState === "connecting" || connectionState === "reconnecting" ? (
      <Text color="yellow">
        <Spinner type="dots" />
      </Text>
    ) : (
      <Text color="red">○</Text>
    );

  const renderSessions = () => {
    const displaySessions = sessions.slice(0, 15);
    return displaySessions.map((s) => {
      const isActive = s.id === activeSessionId;
      const truncatedTitle =
        s.title.length > 18 ? s.title.slice(0, 17) + "…" : s.title;

      return (
        <Box key={s.id} flexShrink={0}>
          <Text color={isActive ? "cyan" : undefined} bold={isActive}>
            {isActive ? "▶ " : "  "}
            {truncatedTitle}
          </Text>
        </Box>
      );
    });
  };

  const renderSkills = () => {
    const displaySkills = skills.slice(0, 8);
    return displaySkills.map((s) => (
      <Box key={s.name} flexShrink={0}>
        <Text dimColor>
          {"  "}📋 {s.name.length > 20 ? s.name.slice(0, 19) + "…" : s.name}
        </Text>
      </Box>
    ));
  };

  const truncatedSessions = sessions.length > 15;

  return (
    <Box
      width={width}
      flexDirection="column"
      borderStyle="single"
      borderColor="gray"
      paddingX={1}
      flexShrink={0}
    >
      {/* Brand header */}
      <Box marginBottom={1}>
        <Text bold color="cyan">
          🐼 Panda
        </Text>
        <Text dimColor> v1.2</Text>
      </Box>

      {/* Status */}
      <Box marginBottom={1}>
        {statusIcon}
        <Text color={statusColor}> {connectionState}</Text>
      </Box>

      <Box marginBottom={1}>
        <Text dimColor>{"─".repeat(width - 4)}</Text>
      </Box>

      {/* Sessions */}
      <Box marginBottom={1}>
        <Text bold>📁 Sessions</Text>
        <Text dimColor> ({sessions.length})</Text>
      </Box>
      <Box flexDirection="column" marginLeft={0} flexShrink={1}>
        {sessions.length === 0 ? (
          <Text dimColor>  (none)</Text>
        ) : (
          renderSessions()
        )}
        {truncatedSessions && (
          <Text dimColor>  ... and {sessions.length - 15} more</Text>
        )}
      </Box>

      <Box marginY={1}>
        <Text dimColor>{"─".repeat(width - 4)}</Text>
      </Box>

      {/* Skills */}
      <Box marginBottom={1}>
        <Text bold>🧠 Skills</Text>
        <Text dimColor> ({skills.length})</Text>
      </Box>
      <Box flexDirection="column" flexShrink={1}>
        {skills.length === 0 ? (
          <Text dimColor>  (none loaded)</Text>
        ) : (
          renderSkills()
        )}
        {skills.length > 8 && (
          <Text dimColor>  ... and {skills.length - 8} more</Text>
        )}
      </Box>

      <Box marginY={1}>
        <Text dimColor>{"─".repeat(width - 4)}</Text>
      </Box>

      {/* Tools */}
      <Box marginBottom={1}>
        <Text bold>🔧 Tools</Text>
        <Text dimColor> ({tools.length})</Text>
      </Box>
      <Box flexDirection="column">
        {tools.length === 0 ? (
          <Text dimColor>  (none loaded)</Text>
        ) : (
          tools.slice(0, 8).map((t) => (
            <Box key={t.name} flexShrink={0}>
              <Text dimColor>
                {"  "}🔹 {t.name.length > 20 ? t.name.slice(0, 19) + "…" : t.name}
              </Text>
            </Box>
          ))
        )}
        {tools.length > 8 && (
          <Text dimColor>  ... and {tools.length - 8} more</Text>
        )}
      </Box>

      {/* Keyboard shortcuts */}
      <Box marginTop={1}>
        <Text dimColor>{"─".repeat(width - 4)}</Text>
      </Box>
      <Box flexDirection="column" marginTop={1}>
        <Text dimColor>Ctrl+N  New Session</Text>
        <Text dimColor>Ctrl+D  Delete Session</Text>
        <Text dimColor>Ctrl+R  Refresh</Text>
        <Text dimColor>Ctrl+C  Quit</Text>
        <Text dimColor>Tab     Switch Session</Text>
      </Box>
    </Box>
  );
};
