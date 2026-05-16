import { createCalendarEvent } from "../calendar";

describe("calendar", () => {
  test("creates event via gog", async () => {
    const result = await createCalendarEvent(
      {
        calendarId: "calendar-id",
        timezone: "-03:00",
        gogAccount: "acct",
        gogKeyringPassword: "pwd",
      },
      {
        title: "Reunião",
        start: new Date("2026-02-06T10:00:00Z"),
      },
      {
        execFile: (async () => ({ stdout: JSON.stringify({ id: "evt" }), stderr: "" })) as any,
      },
    );

    expect(result.status).toBe("success");
  });
});
