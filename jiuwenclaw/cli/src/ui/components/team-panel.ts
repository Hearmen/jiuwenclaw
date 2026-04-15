import type { TeamMemberEvent, TeamMessageEvent, TeamTaskEvent } from "../../core/types.js";
import { padToWidth } from "../rendering/text.js";
import { palette } from "../theme.js";
import {
  isLeaderMember,
  isWorkingStatusLabel,
  latestMemberSummaries,
  messageEventLabel,
  messagePreviewLabel,
  memberStatusPhrase,
  memberStatusTone,
  taskEventLabel,
  truncate,
  type TeamMemberSummary,
} from "./team-shared.js";

function colorMemberLine(line: string, statusLabel: string): string {
  switch (memberStatusTone(statusLabel)) {
    case "active":
      return palette.text.tool(line);
    case "warning":
      return palette.status.warning(line);
    case "error":
      return palette.status.error(line);
    case "subtle":
      return palette.text.subtle(line);
    case "idle":
    default:
      return palette.text.dim(line);
  }
}

function renderMemberTree(
  members: TeamMemberSummary[],
  taskEvents: TeamTaskEvent[],
  messageEvents: TeamMessageEvent[],
  width: number,
  selectedMemberId: string | null,
  maxVisibleTeammates = 6,
): string[] {
  const lines: string[] = [];
  const leader = members.find((member) => isLeaderMember(member.memberId));
  const teammates = members.filter((member) => !isLeaderMember(member.memberId));
  const latestTask = taskEvents.at(-1);
  const latestBroadcast = [...messageEvents].reverse().find((event) => !event.toMember);
  const selectedTeammateIndex = selectedMemberId
    ? teammates.findIndex((member) => member.memberId === selectedMemberId)
    : -1;
  const teammateOffset =
    teammates.length <= maxVisibleTeammates || selectedTeammateIndex < 0
      ? 0
      : Math.max(
          0,
          Math.min(
            selectedTeammateIndex - Math.floor(maxVisibleTeammates / 2),
            teammates.length - maxVisibleTeammates,
          ),
        );
  const visibleTeammates =
    teammates.length <= maxVisibleTeammates
      ? teammates
      : teammates.slice(teammateOffset, teammateOffset + maxVisibleTeammates);

  if (leader) {
    const leadPrimary = `team-lead · ${memberStatusPhrase(leader)}`;
    lines.push(
      padToWidth(
        leader.memberId === selectedMemberId
          ? palette.text.assistant(truncate(`› ${leadPrimary}`, width - 1))
          : colorMemberLine(truncate(leadPrimary, width - 1), leader.statusLabel),
        width,
      ),
    );
    if (leader.preview) {
      lines.push(
        padToWidth(
          palette.text.subtle(
            `⎿ ${truncate(messagePreviewLabel(leader.preview.replace(/\s+/g, " "), leader.previewKind), Math.max(12, width - 4))}`,
          ),
          width,
        ),
      );
    }
    if (latestTask) {
      lines.push(
        padToWidth(
          palette.text.subtle(`⎿ ${truncate(taskEventLabel(latestTask), Math.max(12, width - 4))}`),
          width,
        ),
      );
    } else if (latestBroadcast && latestBroadcast.content.trim()) {
      lines.push(
        padToWidth(
          palette.text.subtle(
            `⎿ ${truncate(latestBroadcast.content.replace(/\s+/g, " ").trim(), Math.max(12, width - 4))}`,
          ),
          width,
        ),
      );
    }
    if (teammates.length > 0) {
      lines.push(" ".repeat(width));
    }
  }

  if (teammateOffset > 0) {
    lines.push(padToWidth(palette.text.subtle(`… ${teammateOffset} earlier teammates`), width));
  }

  for (const [index, member] of visibleTeammates.entries()) {
    const isLast = teammateOffset + index === teammates.length - 1;
    const branch = isLast ? "└─" : "├─";
    const primary = `${branch} @${member.memberId} · ${memberStatusPhrase(member)}`;
    lines.push(
      padToWidth(
        member.memberId === selectedMemberId
          ? palette.text.assistant(truncate(`› ${primary}`, width - 1))
          : colorMemberLine(truncate(primary, width - 1), member.statusLabel),
        width,
      ),
    );
    if (member.preview) {
      const childPrefix = isLast ? "   " : "│  ";
      lines.push(
        padToWidth(
          palette.text.subtle(
            `${childPrefix}⎿ ${truncate(messagePreviewLabel(member.preview.replace(/\s+/g, " "), member.previewKind), Math.max(12, width - 6))}`,
          ),
          width,
        ),
      );
    }
  }

  const remainingTeammates = teammates.length - teammateOffset - visibleTeammates.length;
  if (remainingTeammates > 0) {
    lines.push(padToWidth(palette.text.subtle(`… ${remainingTeammates} more teammates`), width));
  }
  return lines;
}

export function renderTeamPanel(
  memberEvents: TeamMemberEvent[],
  taskEvents: TeamTaskEvent[],
  messageEvents: TeamMessageEvent[],
  width: number,
  selectedMemberId: string | null,
): string[] {
  const members = latestMemberSummaries(memberEvents, messageEvents);
  const lines: string[] = [];
  const leader = members.find((member) => isLeaderMember(member.memberId));
  const teammates = members.filter((member) => !isLeaderMember(member.memberId));
  const workingCount = teammates.filter((member) =>
    isWorkingStatusLabel(member.statusLabel),
  ).length;
  const latestMessage = messageEvents.at(-1);

  lines.push(
    padToWidth(
      palette.text.secondary(
        `Team · ${teammates.length} teammate${teammates.length === 1 ? "" : "s"}${workingCount > 0 ? ` · ${workingCount} working` : leader ? ` · ${memberStatusPhrase(leader)}` : ""}`,
      ),
      width,
    ),
  );

  if (members.length > 0) {
    lines.push(...renderMemberTree(members, taskEvents, messageEvents, width, selectedMemberId));
  } else if (latestMessage) {
    lines.push(
      padToWidth(
        palette.text.dim(
          `⎿ ${truncate(messageEventLabel(latestMessage), Math.max(12, width - 2))}`,
        ),
        width,
      ),
    );
  }

  lines.push(padToWidth(palette.text.subtle("↑/↓ navigate"), width));
  lines.push(" ".repeat(width));
  return lines;
}

export function renderMiniTeamTree(
  memberEvents: TeamMemberEvent[],
  taskEvents: TeamTaskEvent[],
  messageEvents: TeamMessageEvent[],
  width: number,
): string[] {
  const members = latestMemberSummaries(memberEvents, messageEvents);
  if (members.length === 0) {
    return [];
  }
  return renderMemberTree(
    members,
    taskEvents,
    messageEvents,
    width,
    null,
    2,
  ).slice(0, 4);
}
