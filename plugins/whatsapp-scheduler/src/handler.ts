import {
  analyzeMessage,
  detectConfirmation,
  extractLocation,
  extractTime,
  generateConfirmationMessage,
  getRecurrenceRule,
} from "./detector";
import { createCalendarEvent, type CalendarConfig, type CalendarEventResult } from "./calendar";

export type SchedulerContext = {
  result?: ReturnType<typeof analyzeMessage>;
  title?: string;
  location?: string | null;
  recurrence?: string | null;
  start?: Date;
  sender?: string;
};

export type HandlerResponse = {
  action:
    | "no_intent"
    | "need_info"
    | "need_location"
    | "confirm"
    | "scheduled"
    | "cancelled"
    | "error";
  message?: string | null;
  context?: SchedulerContext;
  event?: unknown;
};

export type HandlerDeps = {
  createEvent?: (
    config: CalendarConfig,
    options: {
      title: string;
      start: Date;
      end?: Date;
      location?: string | null;
      recurrence?: string | null;
    },
  ) => Promise<CalendarEventResult>;
};

export async function handleMessage(
  text: string,
  sender: string,
  pendingContext: SchedulerContext | null,
  config: CalendarConfig & { defaultLocation: string },
  deps: HandlerDeps = {},
): Promise<HandlerResponse> {
  if (pendingContext) {
    return handleConfirmation(text, sender, pendingContext, config, deps);
  }

  const result = analyzeMessage(text, sender);
  const intent = result.intent;
  const dtInfo = result.datetime;

  if (!intent.hasIntent) {
    return { action: "no_intent", message: null };
  }

  if (!dtInfo.date && !dtInfo.time) {
    return {
      action: "need_info",
      message: "Claro! Para qual dia e horário você prefere a reunião?",
      context: { result },
    };
  }

  if (dtInfo.date && !dtInfo.time) {
    return {
      action: "need_info",
      message: `Ok, ${dtInfo.dateStr}. Qual horário fica bom?`,
      context: { result },
    };
  }

  const location = determineLocation(dtInfo, text, config.defaultLocation);
  const title = `Reunião com ${sender}`;
  const recurrence = intent.hasRecurrence ? getRecurrenceRule(text) : null;

  return {
    action: "confirm",
    message: generateConfirmationMessage(result, sender, config.defaultLocation),
    context: {
      result,
      title,
      location,
      recurrence,
      start: dtInfo.time ?? undefined,
      sender,
    },
  };
}

export function determineLocation(
  _dtInfo: ReturnType<typeof analyzeMessage>["datetime"],
  text: string,
  defaultLocation: string,
): string | null {
  const specified = extractLocation(text);
  if (specified) {
    return specified;
  }

  return defaultLocation;
}

export async function handleConfirmation(
  text: string,
  sender: string,
  context: SchedulerContext,
  config: CalendarConfig & { defaultLocation: string },
  deps: HandlerDeps = {},
): Promise<HandlerResponse> {
  const location = extractLocation(text);
  if (location) {
    context.location = location;
  }

  const timeInfo = extractTime(text);
  if (timeInfo) {
    const result = context.result;
    const dtInfo = result?.datetime;
    if (dtInfo?.date) {
      const updated = new Date(dtInfo.date);
      updated.setHours(timeInfo.hour, timeInfo.minute, 0, 0);
      context.start = updated;
    }
  }

  if (detectConfirmation(text)) {
    return scheduleEvent(context, config, deps);
  }

  const cancelWords = ["não", "nao", "no", "cancelar", "cancel", "desistir"];
  if (cancelWords.some((word) => text.toLowerCase().includes(word))) {
    return { action: "cancelled", message: "❌ Agendamento cancelado." };
  }

  return {
    action: "confirm",
    message:
      "Deseja confirmar o agendamento? Responda 'sim' para confirmar ou 'não' para cancelar.",
    context,
  };
}

export async function scheduleEvent(
  context: SchedulerContext,
  config: CalendarConfig & { defaultLocation: string },
  deps: HandlerDeps = {},
): Promise<HandlerResponse> {
  const title = context.title ?? (context.sender ? `Reunião com ${context.sender}` : "Reunião");
  const start = context.start;
  const location = context.location ?? config.defaultLocation;
  const recurrence = context.recurrence ?? null;

  if (!start) {
    return { action: "error", message: "❌ Erro: Data/hora não definida." };
  }

  const createEvent = deps.createEvent ?? createCalendarEvent;
  const result = await createEvent(config, {
    title,
    start,
    location,
    recurrence,
  });

  if (result.status === "success") {
    const dateStr = start.toLocaleDateString("pt-BR");
    const timeStr = start.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });

    let msg = `✅ Agendamento confirmado!\n\n📅 ${title}\n   • Data: ${dateStr}\n   • Horário: ${timeStr}\n   • Local: ${location}`;
    if (recurrence) {
      msg += "\n   • Recorrência: Sim";
    }

    return { action: "scheduled", message: msg, event: result.data ?? result.output };
  }

  return { action: "error", message: `❌ Erro ao agendar: ${result.error}` };
}
