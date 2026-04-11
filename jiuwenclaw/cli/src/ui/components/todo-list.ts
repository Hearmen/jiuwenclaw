import { SelectList } from "@mariozechner/pi-tui";
import type { TodoItem } from "../../core/types.js";
import { padToWidth } from "../rendering/text.js";
import { palette, selectListTheme } from "../theme.js";

function todoLabel(todo: TodoItem): string {
  const prefix = todo.status === "in_progress" ? "●" : todo.status === "completed" ? "✓" : "○";
  return `${prefix} ${todo.activeForm || todo.content}`;
}

function todoDescription(todo: TodoItem): string {
  if (todo.status === "in_progress") {
    return "in progress";
  }
  if (todo.status === "completed") {
    return "completed";
  }
  return "pending";
}

export function renderTodoList(todos: TodoItem[], width: number): string[] {
  if (todos.length === 0) {
    return [];
  }

  const ordered = [
    ...todos.filter((todo) => todo.status === "in_progress"),
    ...todos.filter((todo) => todo.status === "pending"),
    ...todos.filter((todo) => todo.status === "completed"),
  ];

  const list = new SelectList(
    ordered.map((todo) => ({
      value: todo.id,
      label: todoLabel(todo),
      description: todoDescription(todo),
    })),
    Math.min(Math.max(ordered.length, 1), 8),
    selectListTheme,
  );

  return [
    padToWidth(palette.text.secondary("Todo"), width),
    ...list.render(width),
    " ".repeat(width),
  ];
}
