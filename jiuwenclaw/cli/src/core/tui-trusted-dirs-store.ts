import { existsSync, statSync } from "node:fs";
import { resolve } from "node:path";

import { loadTuiConfig, saveTuiConfig } from "./tui-config-store.js";

/**
 * Trusted directories storage with persistence via ~/.jiuwenclaw-tui/config.json.
 * Managed at CLI startup and via /workspace commands.
 */
let _trustedDirs: string[] | null = null;

/**
 * Ensure _trustedDirs is loaded from persisted config.
 */
function ensureLoaded(): void {
  if (_trustedDirs === null) {
    const config = loadTuiConfig();
    _trustedDirs = Array.isArray(config.trustedDirs) ? [...config.trustedDirs!] : [];
  }
}

/**
 * Persist current _trustedDirs to config file.
 */
function persist(): void {
  saveTuiConfig({ trustedDirs: _trustedDirs! });
}

/**
 * Normalize a path for comparison (handle trailing separators, case on Windows)
 */
function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) {
    return "";
  }
  const resolved = resolve(trimmed);
  // On Windows, normalize case
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

/**
 * Get all trusted directories.
 * Returns empty array if no dirs set (will use default workspace).
 * @returns Array of trusted directory paths (normalized)
 */
export function getTrustedDirs(): string[] {
  ensureLoaded();
  return [..._trustedDirs!];
}

/**
 * Add a trusted directory.
 * @param path - Directory path to add (must be a folder, not a file)
 * @returns "added" if added, "exists" if already trusted, "not_found" if path doesn't exist, "invalid" if invalid path or not a directory
 */
export function addTrustedDir(path: string): "added" | "exists" | "not_found" | "invalid" {
  ensureLoaded();
  const normalized = normalizePath(path);
  if (!normalized) {
    return "invalid";
  }
  if (!existsSync(normalized)) {
    return "not_found";
  }
  try {
    const stats = statSync(normalized);
    if (!stats.isDirectory()) {
      return "invalid";
    }
  } catch {
    return "invalid";
  }
  if (_trustedDirs!.includes(normalized)) {
    return "exists";
  }
  _trustedDirs!.push(normalized);
  persist();
  return "added";
}

/**
 * Reset trusted dirs and set a single path.
 * @param path - Directory path to set as the only trusted dir (must be a folder, not a file)
 * @returns "set" if set successfully, "not_found" if path doesn't exist, "invalid" if invalid path or not a directory
 */
export function setTrustedDir(path: string): "set" | "not_found" | "invalid" {
  ensureLoaded();
  const normalized = normalizePath(path);
  if (!normalized) {
    return "invalid";
  }
  if (!existsSync(normalized)) {
    return "not_found";
  }
  try {
    const stats = statSync(normalized);
    if (!stats.isDirectory()) {
      return "invalid";
    }
  } catch {
    return "invalid";
  }
  _trustedDirs = [normalized];
  persist();
  return "set";
}

/**
 * Remove a trusted directory.
 * @param path - Directory path to remove
 * @returns true if removed, false if not found
 */
export function removeTrustedDir(path: string): boolean {
  ensureLoaded();
  const normalized = normalizePath(path);
  if (!normalized) {
    return false;
  }
  const index = _trustedDirs!.indexOf(normalized);
  if (index === -1) {
    return false;
  }
  _trustedDirs!.splice(index, 1);
  persist();
  return true;
}

/**
 * Clear all trusted directories (will use default workspace only).
 */
export function clearTrustedDirs(): void {
  ensureLoaded();
  _trustedDirs = [];
  persist();
}

/**
 * Check if a path is a trusted directory.
 * @param path - Directory path to check
 * @returns true if trusted
 */
export function isTrustedDir(path: string): boolean {
  ensureLoaded();
  const normalized = normalizePath(path);
  if (!normalized) {
    return false;
  }
  return _trustedDirs!.includes(normalized);
}

/**
 * Get the default workspace path.
 */
export function getDefaultWorkspacePath(): string {
  return resolve("~/.jiuwenclaw/agent/jiuwenclaw_workspace");
}
