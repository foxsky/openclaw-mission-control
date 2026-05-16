import os from "node:os";
import path from "node:path";
import { promises as fs } from "node:fs";
import { initStore, setContext, getContext, deleteContext } from "../context-store";

describe("context-store", () => {
  test("persists and clears context", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "whatsapp-scheduler-"));
    const filePath = path.join(dir, "contexts.json");

    process.env.CONTEXT_STORE_PATH = filePath;
    initStore(filePath);

    setContext("key", { sender: "Ana" });
    const stored = getContext("key");
    expect(stored?.sender).toBe("Ana");

    deleteContext("key");
    const cleared = getContext("key");
    expect(cleared).toBeUndefined();
  });
});
