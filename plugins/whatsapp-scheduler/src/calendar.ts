import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export type CalendarConfig = {
  calendarId: string;
  timezone: string;
  gogAccount: string;
  gogKeyringPassword: string;
};

export type CalendarEventResult =
  | { status: "success"; data?: unknown; output?: string }
  | { status: "error"; error: string; code?: number };

export function formatDateTime(dt: Date, timezone: string): string {
  const pad = (value: number) => value.toString().padStart(2, "0");
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(
    dt.getHours(),
  )}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}${timezone}`;
}

export async function createCalendarEvent(
  config: CalendarConfig,
  options: {
    title: string;
    start: Date;
    end?: Date;
    location?: string | null;
    recurrence?: string | null;
  },
  deps: { execFile?: typeof execFileAsync } = {},
): Promise<CalendarEventResult> {
  const end = options.end ?? new Date(options.start.getTime() + 60 * 60 * 1000);
  const startStr = formatDateTime(options.start, config.timezone);
  const endStr = formatDateTime(end, config.timezone);

  const args: string[] = [
    "calendar",
    "create",
    config.calendarId,
    "--summary",
    options.title,
    "--from",
    startStr,
    "--to",
    endStr,
  ];

  if (options.location) {
    args.push("--location", options.location);
  }

  if (options.recurrence) {
    args.push("--rrule", options.recurrence);
  }

  try {
    const execFn = deps.execFile ?? execFileAsync;
    const { stdout } = await execFn("gog", args, {
      env: {
        ...process.env,
        GOG_ACCOUNT: config.gogAccount,
        GOG_KEYRING_PASSWORD: config.gogKeyringPassword,
      },
    });

    try {
      const parsed = JSON.parse(stdout);
      return { status: "success", data: parsed };
    } catch (error) {
      return { status: "success", output: stdout.trim() };
    }
  } catch (error) {
    const err = error as { stderr?: string; message: string; code?: number };
    return {
      status: "error",
      error: err.stderr?.trim() || err.message,
      code: err.code,
    };
  }
}
