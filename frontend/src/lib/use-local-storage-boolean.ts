import { useCallback, useSyncExternalStore } from "react";

/**
 * Bind a boolean flag to a localStorage key with cross-instance and
 * cross-tab sync. SSR-safe (returns the supplied default on the server).
 *
 * Use for collapse/expand toggles, "do not show again" flags, and
 * other UI preferences that should survive navigation and refresh.
 */
const subscribe = (callback: () => void): (() => void) => {
  if (typeof window === "undefined") return () => {};
  // The custom event keeps multiple hook instances in the same tab in
  // sync; the storage event handles cross-tab updates.
  const handler = () => callback();
  window.addEventListener("mc:local-storage-boolean-change", handler);
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener("mc:local-storage-boolean-change", handler);
    window.removeEventListener("storage", handler);
  };
};

const buildSnapshot = (key: string, defaultValue: boolean) => (): boolean => {
  if (typeof window === "undefined") return defaultValue;
  const raw = window.localStorage.getItem(key);
  if (raw === "true") return true;
  if (raw === "false") return false;
  return defaultValue;
};

const buildServerSnapshot = (defaultValue: boolean) => (): boolean =>
  defaultValue;

export function useLocalStorageBoolean(
  key: string,
  defaultValue: boolean = false,
): [boolean, (next: boolean) => void] {
  const value = useSyncExternalStore(
    subscribe,
    buildSnapshot(key, defaultValue),
    buildServerSnapshot(defaultValue),
  );

  const setValue = useCallback(
    (next: boolean) => {
      if (typeof window === "undefined") return;
      window.localStorage.setItem(key, next ? "true" : "false");
      window.dispatchEvent(
        new CustomEvent("mc:local-storage-boolean-change", { detail: key }),
      );
    },
    [key],
  );

  return [value, setValue];
}
