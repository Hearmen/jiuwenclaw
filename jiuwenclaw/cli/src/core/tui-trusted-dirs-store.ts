import { existsSync } from "node:fs";
import { resolve } from "node:path";

/**
 * In-memory trusted directories storage (session-based, no persistence).
 * Managed at CLI startup and via /workspace commands.
 */
let _trustedDirs: string[] = [];

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
  return [..._trustedDirs];
}

/**
 * Add a trusted directory.
 * @param path - Directory path to add
 * @returns "added" if added, "exists" if already trusted, "not_found" if path doesn't exist, "invalid" if invalid path
 */
export function addTrustedDir(path: string): "added" | "exists" | "not_found" | "invalid" {
  const normalized = normalizePath(path);
  if (!normalized) {
    return "invalid";
  }
  if (!existsSync(normalized)) {
    return "not_found";
  }
  if (_trustedDirs.includes(normalized)) {
    return "exists";
  }
  _trustedDirs.push(normalized);
  return "added";
}

/**
 * Reset trusted dirs and set a single path.
 * @param path - Directory path to set as the only trusted dir
 * @returns "set" if set successfully, "not_found" if path doesn't exist, "invalid" if invalid path
 */
export function setTrustedDir(path: string): "set" | "not_found" | "invalid" {
  const normalized = normalizePath(path);
  if (!normalized) {
    return "invalid";
  }
  if (!existsSync(normalized)) {
    return "not_found";
  }
  _trustedDirs = [normalized];
  return "set";
}

/**
 * Remove a trusted directory.
 * @param path - Directory path to remove
 * @returns true if removed, false if not found
 */
export function removeTrustedDir(path: string): boolean {
  const normalized = normalizePath(path);
  if (!normalized) {
    return false;
  }
  const index = _trustedDirs.indexOf(normalized);
  if (index === -1) {
    return false;
  }
  _trustedDirs.splice(index, 1);
  return true;
}

/**
 * Clear all trusted directories (will use default workspace only).
 */
export function clearTrustedDirs(): void {
  _trustedDirs = [];
}

/**
 * Check if a path is a trusted directory.
 * @param path - Directory path to check
 * @returns true if trusted
 */
export function isTrustedDir(path: string): boolean {
  const normalized = normalizePath(path);
  if (!normalized) {
    return false;
  }
  return _trustedDirs.includes(normalized);
}

/**
 * Get the default workspace path.
 */
export function getDefaultWorkspacePath(): string {
  return resolve("~/.jiuwenclaw/agent/jiuwenclaw_workspace");
}