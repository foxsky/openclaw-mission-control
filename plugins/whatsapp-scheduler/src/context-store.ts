import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type { SchedulerContext } from "./handler.js";

interface StoredContext extends SchedulerContext {
  updatedAt: string;
}

interface ContextStore {
  [sender: string]: StoredContext;
}

let storePath = process.env.CONTEXT_STORE_PATH;
let memoryCache: ContextStore = {};
let initialized = false;

export function initStore(path?: string): void {
  if (path) {
    storePath = path;
  }
  if (!storePath) {
    throw new Error("CONTEXT_STORE_PATH must be set");
  }

  const dir = dirname(storePath);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }

  if (existsSync(storePath)) {
    try {
      const data = readFileSync(storePath, "utf-8");
      memoryCache = JSON.parse(data);

      for (const sender in memoryCache) {
        const ctx = memoryCache[sender];
        if (ctx.start && typeof ctx.start === "string") {
          ctx.start = new Date(ctx.start);
        }
        if (ctx.result?.datetime?.date && typeof ctx.result.datetime.date === "string") {
          ctx.result.datetime.date = new Date(ctx.result.datetime.date);
        }
        if (ctx.result?.datetime?.time && typeof ctx.result.datetime.time === "string") {
          ctx.result.datetime.time = new Date(ctx.result.datetime.time);
        }
      }
    } catch (err) {
      console.error("[context-store] Failed to load store:", err);
      memoryCache = {};
    }
  }

  initialized = true;
}

function persist(): void {
  if (!storePath) {
    throw new Error("CONTEXT_STORE_PATH must be set");
  }
  const absPath = resolve(storePath);
  const tmpPath = `${absPath}.tmp`;
  try {
    writeFileSync(tmpPath, JSON.stringify(memoryCache, null, 2));
    renameSync(tmpPath, absPath);
  } catch (err) {
    console.error("[context-store] Failed to persist store:", err);
  }
}

export function getContext(sender: string): SchedulerContext | undefined {
  if (!initialized) initStore();
  const stored = memoryCache[sender];
  if (!stored) return undefined;

  const updatedAt = new Date(stored.updatedAt);
  const ageMs = Date.now() - updatedAt.getTime();
  if (ageMs > 60 * 60 * 1000) {
    delete memoryCache[sender];
    persist();
    return undefined;
  }

  return stored;
}

export function setContext(sender: string, context: SchedulerContext): void {
  if (!initialized) initStore();
  memoryCache[sender] = { ...context, updatedAt: new Date().toISOString() };
  persist();
}

export function deleteContext(sender: string): void {
  if (!initialized) initStore();
  delete memoryCache[sender];
  persist();
}

export function getContextCount(): number {
  if (!initialized) initStore();
  return Object.keys(memoryCache).length;
}

export function clearAll(): void {
  memoryCache = {};
  persist();
}
