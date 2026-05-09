import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useLocalStorageBoolean } from "./use-local-storage-boolean";

describe("useLocalStorageBoolean", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("returns the default value when nothing is stored", () => {
    const { result } = renderHook(() => useLocalStorageBoolean("k", false));
    expect(result.current[0]).toBe(false);
  });

  it("respects an explicit default of true", () => {
    const { result } = renderHook(() => useLocalStorageBoolean("k", true));
    expect(result.current[0]).toBe(true);
  });

  it("hydrates from localStorage", () => {
    window.localStorage.setItem("k", "true");
    const { result } = renderHook(() => useLocalStorageBoolean("k", false));
    expect(result.current[0]).toBe(true);
  });

  it("ignores malformed stored value and uses default", () => {
    window.localStorage.setItem("k", "garbage");
    const { result } = renderHook(() => useLocalStorageBoolean("k", true));
    expect(result.current[0]).toBe(true);
  });

  it("setter updates state and writes localStorage", () => {
    const { result } = renderHook(() => useLocalStorageBoolean("k", false));
    act(() => result.current[1](true));
    expect(result.current[0]).toBe(true);
    expect(window.localStorage.getItem("k")).toBe("true");
  });

  it("syncs across hook instances with the same key", () => {
    const a = renderHook(() => useLocalStorageBoolean("shared", false));
    const b = renderHook(() => useLocalStorageBoolean("shared", false));
    act(() => a.result.current[1](true));
    expect(b.result.current[0]).toBe(true);
  });

  it("scopes per-key — different keys do not interfere", () => {
    const a = renderHook(() => useLocalStorageBoolean("alpha", false));
    const b = renderHook(() => useLocalStorageBoolean("beta", false));
    act(() => a.result.current[1](true));
    expect(a.result.current[0]).toBe(true);
    expect(b.result.current[0]).toBe(false);
  });
});
