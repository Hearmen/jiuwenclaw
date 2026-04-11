import { Chalk } from "chalk";
import type { EditorTheme, MarkdownTheme, SelectListTheme } from "@mariozechner/pi-tui";

export const chalk = new Chalk({ level: 3 });

export type ThemeName = "system" | "dark" | "light";
export type AccentColorName = "default" | "blue" | "green" | "pink" | "purple" | "red" | "yellow";

const THEME_OPTIONS: readonly ThemeName[] = ["system", "dark", "light"] as const;
const ACCENT_OPTIONS: readonly AccentColorName[] = [
  "default",
  "blue",
  "green",
  "pink",
  "purple",
  "red",
  "yellow",
] as const;

type ThemeDefinition = {
  textPrimary: string;
  textSecondary: string;
  textDim: string;
  textAccent: string;
  statusSuccess: string;
  statusError: string;
  statusWarning: string;
  statusInfo: string;
  borderPanel: string;
  borderQuestion: string;
  surfaceUserBg: string;
  surfaceUserFg: string;
  markdownHeading: string;
  markdownCode: string;
  markdownCodeBlock: string;
  diffAddBg: string;
  diffAddFg: string;
  diffRemoveBg: string;
  diffRemoveFg: string;
  diffContextBg: string;
  diffContextFg: string;
};

const THEME_DEFINITIONS: Record<"light" | "dark", ThemeDefinition> = {
  light: {
    textPrimary: "#1f2937",
    textSecondary: "#0f766e",
    textDim: "#6b7280",
    textAccent: "#2563eb",
    statusSuccess: "#15803d",
    statusError: "#dc2626",
    statusWarning: "#ca8a04",
    statusInfo: "#0891b2",
    borderPanel: "#94a3b8",
    borderQuestion: "#d97706",
    surfaceUserBg: "#e8e8e8",
    surfaceUserFg: "#111827",
    markdownHeading: "#0f766e",
    markdownCode: "#a16207",
    markdownCodeBlock: "#111827",
    diffAddBg: "#dcfce7",
    diffAddFg: "#14532d",
    diffRemoveBg: "#fee2e2",
    diffRemoveFg: "#7f1d1d",
    diffContextBg: "#f3f4f6",
    diffContextFg: "#6b7280",
  },
  dark: {
    textPrimary: "#e5e7eb",
    textSecondary: "#67e8f9",
    textDim: "#94a3b8",
    textAccent: "#60a5fa",
    statusSuccess: "#4ade80",
    statusError: "#f87171",
    statusWarning: "#fbbf24",
    statusInfo: "#22d3ee",
    borderPanel: "#475569",
    borderQuestion: "#f59e0b",
    surfaceUserBg: "#1f2937",
    surfaceUserFg: "#f9fafb",
    markdownHeading: "#67e8f9",
    markdownCode: "#fbbf24",
    markdownCodeBlock: "#f3f4f6",
    diffAddBg: "#14532d",
    diffAddFg: "#dcfce7",
    diffRemoveBg: "#7f1d1d",
    diffRemoveFg: "#fee2e2",
    diffContextBg: "#1f2937",
    diffContextFg: "#94a3b8",
  },
};

const ACCENT_COLORS: Record<Exclude<AccentColorName, "default">, string> = {
  blue: "#2563eb",
  green: "#16a34a",
  pink: "#db2777",
  purple: "#7c3aed",
  red: "#dc2626",
  yellow: "#ca8a04",
};

let currentThemeName: ThemeName = "light";
let currentAccentColor: AccentColorName = "default";

// TODO: Persist theme/accent across CLI restarts after the broader config/settings story is finalized.

function detectSystemTheme(): "light" | "dark" {
  const colorfgbg = process.env.COLORFGBG;
  if (colorfgbg) {
    const parts = colorfgbg.split(";");
    const bg = Number.parseInt(parts[parts.length - 1] ?? "", 10);
    if (Number.isFinite(bg)) {
      return bg >= 0 && bg <= 6 ? "dark" : "light";
    }
  }
  return process.env.TERM_PROGRAM === "Apple_Terminal" ? "light" : "dark";
}

function getResolvedThemeName(): "light" | "dark" {
  return currentThemeName === "system" ? detectSystemTheme() : currentThemeName;
}

function getThemeDefinition(): ThemeDefinition {
  return THEME_DEFINITIONS[getResolvedThemeName()];
}

function getAccentHex(): string {
  if (currentAccentColor === "default") {
    return getThemeDefinition().textAccent;
  }
  return ACCENT_COLORS[currentAccentColor];
}

export function getThemeOptions(): readonly ThemeName[] {
  return THEME_OPTIONS;
}

export function getAccentColorOptions(): readonly AccentColorName[] {
  return ACCENT_OPTIONS;
}

export function getCurrentThemeName(): ThemeName {
  return currentThemeName;
}

export function getCurrentAccentColor(): AccentColorName {
  return currentAccentColor;
}

export function setCurrentThemeName(theme: ThemeName): void {
  currentThemeName = theme;
}

export function setCurrentAccentColor(color: AccentColorName): void {
  currentAccentColor = color;
}

export const palette = {
  text: {
    primary: (value: string) => chalk.hex(getThemeDefinition().textPrimary)(value),
    secondary: (value: string) => chalk.hex(getThemeDefinition().textSecondary)(value),
    accent: (value: string) => chalk.hex(getAccentHex())(value),
    dim: (value: string) => chalk.hex(getThemeDefinition().textDim)(value),
  },
  surface: {
    user: (value: string) =>
      chalk.bgHex(getThemeDefinition().surfaceUserBg).hex(getThemeDefinition().surfaceUserFg)(
        value,
      ),
  },
  status: {
    success: (value: string) => chalk.hex(getThemeDefinition().statusSuccess)(value),
    error: (value: string) => chalk.hex(getThemeDefinition().statusError)(value),
    warning: (value: string) => chalk.hex(getThemeDefinition().statusWarning)(value),
    info: (value: string) => chalk.hex(getThemeDefinition().statusInfo)(value),
  },
  diff: {
    add: (value: string) =>
      chalk.bgHex(getThemeDefinition().diffAddBg).hex(getThemeDefinition().diffAddFg)(value),
    remove: (value: string) =>
      chalk.bgHex(getThemeDefinition().diffRemoveBg).hex(getThemeDefinition().diffRemoveFg)(value),
    context: (value: string) =>
      chalk.bgHex(getThemeDefinition().diffContextBg).hex(getThemeDefinition().diffContextFg)(
        value,
      ),
  },
  border: {
    panel: (value: string) => chalk.hex(getThemeDefinition().borderPanel)(value),
    active: (value: string) => chalk.hex(getAccentHex())(value),
    question: (value: string) => chalk.hex(getThemeDefinition().borderQuestion)(value),
  },
};

export const selectListTheme: SelectListTheme = {
  selectedPrefix: (value: string) => chalk.hex(getAccentHex())(value),
  selectedText: (value: string) => chalk.bold.hex(getThemeDefinition().textPrimary)(value),
  description: (value: string) => chalk.hex(getThemeDefinition().textDim)(value),
  scrollInfo: (value: string) => chalk.hex(getThemeDefinition().textDim)(value),
  noMatch: (value: string) => chalk.hex(getThemeDefinition().textDim)(value),
};

export const editorTheme: EditorTheme = {
  borderColor: (value: string) => chalk.hex(getThemeDefinition().borderPanel)(value),
  selectList: selectListTheme,
};

export const markdownTheme: MarkdownTheme = {
  heading: (value: string) => chalk.bold.hex(getThemeDefinition().markdownHeading)(value),
  link: (value: string) => chalk.hex(getAccentHex())(value),
  linkUrl: (value: string) => chalk.hex(getThemeDefinition().textDim)(value),
  code: (value: string) => chalk.hex(getThemeDefinition().markdownCode)(value),
  codeBlock: (value: string) => chalk.hex(getThemeDefinition().markdownCodeBlock)(value),
  codeBlockBorder: (value: string) => chalk.hex(getThemeDefinition().borderPanel)(value),
  quote: (value: string) => chalk.italic.hex(getThemeDefinition().textDim)(value),
  quoteBorder: (value: string) => chalk.hex(getThemeDefinition().borderPanel)(value),
  hr: (value: string) => chalk.hex(getThemeDefinition().borderPanel)(value),
  listBullet: (value: string) => chalk.hex(getAccentHex())(value),
  bold: (value: string) => chalk.bold(value),
  italic: (value: string) => chalk.italic(value),
  strikethrough: (value: string) => chalk.strikethrough(value),
  underline: (value: string) => chalk.underline(value),
};
