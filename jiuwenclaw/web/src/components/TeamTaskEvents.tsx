/**
 * TeamTaskEvents 组件
 *
 * 显示团队任务相关的事件日志
 */

interface TeamTaskEvent {
  id: string;
  type: string;
  team_id: string;
  task_id: string;
  status: string;
  timestamp: number;
}

interface TeamTaskEventsProps {
  events: TeamTaskEvent[];
}

export function TeamTaskEvents({ events }: TeamTaskEventsProps) {

  const formatTime = (timestamp: number) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString();
  };

  const getEventName = (type: string) => {
    // 从 type 中提取 task 后的部分作为事件名称
    const match = type.match(/team\.task\.(\w+)/);
    return match ? match[1] : type;
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 flex-1 overflow-hidden flex flex-col">
        <h3 className="text-[11px] font-medium text-text-muted uppercase tracking-wider mb-4">
          任务事件日志
        </h3>
        <div className="flex-1 overflow-y-auto space-y-2">
          {events.map((event) => (
            <div key={event.id} className="bg-secondary/50 rounded-md p-2 space-y-1">
              <div className="flex items-center justify-between text-xs">
                <span className="font-medium text-text">{getEventName(event.type)}</span>
                <span className="text-text-muted">{formatTime(event.timestamp)}</span>
              </div>
              <div className="flex items-center gap-3 text-xs text-text-muted">
                <span>任务: <span className="text-text">{event.task_id}</span></span>
                <span>状态: <span className="text-text">{event.status}</span></span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
