/**
 * TeamArea 组件
 *
 * 显示团队信息，包括团队名称、成员列表和成员状态
 */

import { useTodoStore } from '../stores';

interface TeamMember {
  id: string;
  member_id: string;
  status: string;
  timestamp: number;
  currentTask?: string;
}

interface TeamAreaProps {
  teamName: string;
  members: TeamMember[];
}

export function TeamArea({ teamName, members }: TeamAreaProps) {
  const { todos } = useTodoStore();

  // 获取每个成员正在执行的任务
  const getMemberCurrentTask = (memberId: string) => {
    return todos.find(todo => todo.claimedBy === memberId && todo.status === 'in_progress');
  };

  const getStatusColor = (status: TeamMember['status']) => {
    switch (status) {
      case 'ready':
        return 'bg-ok';
      case 'busy':
        return 'bg-warning';
      case 'completed':
        return 'bg-success';
      case 'idle':
        return 'bg-text-muted';
      case 'completing':
        return 'bg-info';
      default:
        return 'bg-text-muted';
    }
  };

  const formatTime = (timestamp: number) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString();
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 flex-1 overflow-hidden flex flex-col">
        <h3 className="text-[11px] font-medium text-text-muted uppercase tracking-wider mb-4">
          {teamName}
        </h3>
        <div className="flex-1 overflow-y-auto space-y-3">
          {members.map((member) => {
            const currentTask = getMemberCurrentTask(member.member_id);
            return (
              <div key={member.id} className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${getStatusColor(member.status)}`} />
                  {/* <span className="text-sm font-medium">{member.name}</span> */}
                  <span className="text-xs text-text-muted">{member.member_id}</span>
                  <span className="ml-auto text-xs text-text-muted">{formatTime(member.timestamp)}</span>
                </div>
                <div className="text-xs text-text-muted ml-4">
                  状态: {member.status}
                </div>
                {currentTask && (
                  <div className="text-xs text-text-muted ml-4 truncate max-w-[120px]">
                    正在执行: {currentTask.content}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
