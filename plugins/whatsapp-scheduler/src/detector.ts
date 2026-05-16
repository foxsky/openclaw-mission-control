export type IntentResult = {
  hasIntent: boolean;
  keywords: string[];
  location: string | null;
  hasRecurrence: boolean;
  confidence: number;
};

export type DateInfo = {
  date: Date;
  dateStr: string;
};

export type TimeInfo = {
  hour: number;
  minute: number;
  timeStr: string;
};

export type DateTimeInfo = {
  date: Date | null;
  dateStr: string | null;
  time: Date | null;
  timeStr: string | null;
  hour: number | null;
  minute: number | null;
};

export type AnalyzeResult = {
  text: string;
  sender: string;
  intent: IntentResult;
  datetime: DateTimeInfo;
  confidence: number;
};

const TRIGGER_KEYWORDS = new Set([
  "meet",
  "meeting",
  "schedule",
  "scheduled",
  "appointment",
  "call",
  "video call",
  "videochat",
  "later this week",
  "tomorrow",
  "next week",
  "this week",
  "reunião",
  "reuniões",
  "agendar",
  "agendado",
  "marcar",
  "marcado",
  "ligar",
  "ligação",
  "contato",
  "videochamada",
  "video chamada",
  "esta semana",
  "amanhã",
  "amanha",
  "semana que vem",
  "proxima semana",
]);

const WEEKDAYS: Record<string, number> = {
  domingo: 6,
  sunday: 6,
  segunda: 0,
  "segunda-feira": 0,
  monday: 0,
  terca: 1,
  "terça": 1,
  "terça-feira": 1,
  tuesday: 1,
  quarta: 2,
  "quarta-feira": 2,
  wednesday: 2,
  quinta: 3,
  "quinta-feira": 3,
  thursday: 3,
  sexta: 4,
  "sexta-feira": 4,
  friday: 4,
  sabado: 5,
  "sábado": 5,
  saturday: 5,
};

const LOCATION_KEYWORDS: Record<string, string> = {
  online: "Online",
  presencial: "Presencial",
  remoto: "Online",
  "escritório": "Escritório",
  escritorio: "Escritório",
  office: "Escritório",
  prodater: "Prodater",
};

const CONFIRMATION_KEYWORDS = [
  "sim",
  "yes",
  "ok",
  "confirmo",
  "confirmado",
  "claro",
  "certo",
  "valeu",
  "brigado",
  "thanks",
  "obrigado",
  "obrigada",
  "yeah",
  "yep",
  "yup",
  "pode ser",
  "fechado",
  "combinado",
];

const RECURRENCE_KEYWORDS = [
  "recurring",
  "repetir",
  "semanal",
  "semanalmente",
  "mensal",
  "mensalmente",
  "todo dia",
  "diário",
  "diario",
  "toda segunda",
  "toda terça",
  "toda quarta",
  "toda quinta",
  "toda sexta",
  "todo domingo",
  "todo sabado",
];

export function detectIntent(text: string): IntentResult {
  const textLower = text.toLowerCase();
  const detectedKeywords = Array.from(TRIGGER_KEYWORDS).filter((kw) =>
    textLower.includes(kw),
  );
  const hasRecurrence = RECURRENCE_KEYWORDS.some((kw) =>
    textLower.includes(kw),
  );

  let locationFound: string | null = null;
  for (const [kw, loc] of Object.entries(LOCATION_KEYWORDS)) {
    if (textLower.includes(kw)) {
      locationFound = loc;
      break;
    }
  }

  return {
    hasIntent: detectedKeywords.length > 0,
    keywords: detectedKeywords,
    location: locationFound,
    hasRecurrence,
    confidence: Math.min(detectedKeywords.length, 3),
  };
}

export function extractTime(text: string): TimeInfo | null {
  const textLower = text.toLowerCase();

  let match = textLower.match(/à?s?\s*(\d{1,2})(?:h(\d{2})|:(\d{2}))?/);
  if (match) {
    const hour = Number(match[1]);
    const minute = match[2] ? Number(match[2]) : match[3] ? Number(match[3]) : 0;
    return { hour, minute, timeStr: `${hour.toString().padStart(2, "0")}:${minute
      .toString()
      .padStart(2, "0")}` };
  }

  match = textLower.match(/(\d{1,2})h(\d{2})?/);
  if (match) {
    const hour = Number(match[1]);
    const minute = match[2] ? Number(match[2]) : 0;
    return { hour, minute, timeStr: `${hour.toString().padStart(2, "0")}:${minute
      .toString()
      .padStart(2, "0")}` };
  }

  match = textLower.match(/(\d{1,2})(?::(\d{2}))?\s*(pm|am)/i);
  if (match) {
    let hour = Number(match[1]);
    const minute = match[2] ? Number(match[2]) : 0;
    const meridiem = match[3].toLowerCase();
    if (meridiem === "pm" && hour !== 12) {
      hour += 12;
    } else if (meridiem === "am" && hour === 12) {
      hour = 0;
    }
    return { hour, minute, timeStr: `${hour.toString().padStart(2, "0")}:${minute
      .toString()
      .padStart(2, "0")}` };
  }

  match = textLower.match(/(\d{1,2}):(\d{2})/);
  if (match) {
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    return { hour, minute, timeStr: `${hour.toString().padStart(2, "0")}:${minute
      .toString()
      .padStart(2, "0")}` };
  }

  match = textLower.match(/at\s+(\d{1,2})/);
  if (match) {
    let hour = Number(match[1]);
    if (hour < 9) {
      hour += 12;
    }
    return { hour, minute: 0, timeStr: `${hour.toString().padStart(2, "0")}:00` };
  }

  return null;
}

export function extractDate(text: string, now: Date = new Date()): DateInfo | null {
  const textLower = text.toLowerCase();
  const today = new Date(now);
  const weekday = (today.getDay() + 6) % 7;

  if (textLower.includes("tomorrow") || textLower.includes("amanhã") || textLower.includes("amanha")) {
    const target = new Date(today);
    target.setDate(target.getDate() + 1);
    return { date: target, dateStr: "amanhã" };
  }

  if (textLower.includes("today") || textLower.includes("hoje")) {
    return { date: today, dateStr: "hoje" };
  }

  if (
    textLower.includes("next week") ||
    textLower.includes("semana que vem") ||
    textLower.includes("proxima semana") ||
    textLower.includes("próxima semana")
  ) {
    const target = new Date(today);
    target.setDate(target.getDate() + 7);
    return { date: target, dateStr: "semana que vem" };
  }

  if (textLower.includes("this week") || textLower.includes("esta semana")) {
    const daysUntilFriday = (4 - weekday + 7) % 7 || 1;
    const target = new Date(today);
    target.setDate(target.getDate() + daysUntilFriday);
    return { date: target, dateStr: "esta semana" };
  }

  for (const [dayName, dayNum] of Object.entries(WEEKDAYS)) {
    if (textLower.includes(dayName)) {
      const daysAhead = (dayNum - weekday + 7) % 7 || 7;
      const target = new Date(today);
      target.setDate(target.getDate() + daysAhead);
      return { date: target, dateStr: dayName };
    }
  }

  return null;
}

export function extractDateTime(text: string, now: Date = new Date()): DateTimeInfo {
  const dateInfo = extractDate(text, now);
  const timeInfo = extractTime(text);

  const result: DateTimeInfo = {
    date: dateInfo?.date ?? null,
    dateStr: dateInfo?.dateStr ?? null,
    time: null,
    timeStr: timeInfo?.timeStr ?? null,
    hour: timeInfo?.hour ?? null,
    minute: timeInfo?.minute ?? null,
  };

  if (dateInfo && timeInfo) {
    const time = new Date(dateInfo.date);
    time.setHours(timeInfo.hour, timeInfo.minute, 0, 0);
    result.time = time;
  } else if (timeInfo) {
    const today = new Date(now);
    result.date = today;
    result.dateStr = "hoje";
    const time = new Date(today);
    time.setHours(timeInfo.hour, timeInfo.minute, 0, 0);
    result.time = time;
  }

  return result;
}

export function isBefore2pm(dtInfo: DateTimeInfo): boolean {
  if (dtInfo.hour !== null) {
    return dtInfo.hour < 14;
  }
  return true;
}

export function detectConfirmation(text: string): boolean {
  const textLower = text.toLowerCase().trim();
  if (CONFIRMATION_KEYWORDS.some((kw) => textLower.includes(kw))) {
    return true;
  }

  if (extractTime(text)) {
    return true;
  }

  for (const kw of Object.keys(LOCATION_KEYWORDS)) {
    if (textLower.includes(kw)) {
      return true;
    }
  }

  return false;
}

export function extractLocation(text: string): string | null {
  const textLower = text.toLowerCase();
  for (const [kw, loc] of Object.entries(LOCATION_KEYWORDS)) {
    if (textLower.includes(kw)) {
      return loc;
    }
  }
  return null;
}

export function extractContactName(text: string, sender: string): string {
  if (sender && sender !== "Você") {
    return sender;
  }
  const match = text.match(/com\s+([A-Z][a-zà-ú]+)/);
  if (match) {
    return match[1];
  }
  return "Contato";
}

export function analyzeMessage(text: string, sender: string, now: Date = new Date()): AnalyzeResult {
  const intent = detectIntent(text);
  const dtInfo = extractDateTime(text, now);

  let confidence = intent.confidence;
  if (dtInfo.date) {
    confidence = Math.min(confidence + 1, 3);
  }
  if (dtInfo.time) {
    confidence = Math.min(confidence + 1, 3);
  }

  return {
    text,
    sender,
    intent,
    datetime: dtInfo,
    confidence,
  };
}

export function shouldAutoConfirm(result: AnalyzeResult): boolean {
  return result.datetime.date !== null && result.datetime.time !== null;
}

export function generateConfirmationMessage(
  result: AnalyzeResult,
  sender: string,
  defaultLocation: string,
): string {
  const dt = result.datetime;
  const intent = result.intent;
  const title = `Reunião com ${sender}`;
  const messages: string[] = ["📅 Vou agendar:", `   • Título: ${title}`];

  if (dt.dateStr) {
    messages.push(`   • Data: ${dt.dateStr.charAt(0).toUpperCase()}${dt.dateStr.slice(1)}`);
  } else {
    messages.push("   • Data: A definir");
  }

  if (dt.timeStr) {
    messages.push(`   • Horário: ${dt.timeStr}`);
  } else {
    messages.push("   • Horário: A definir");
  }

  const location = intent.location ?? defaultLocation ?? "A confirmar";
  messages.push(`   • Local: ${location}`);

  if (intent.hasRecurrence) {
    messages.push("   • Recorrência: Sim");
  }

  messages.push("", "✅ Confirma o agendamento?");

  return messages.join("\n");
}

export function getRecurrenceRule(text: string): string | null {
  const textLower = text.toLowerCase();

  if (textLower.includes("toda segunda") || textLower.includes("every monday")) {
    return "RRULE:FREQ=WEEKLY;BYDAY=MO";
  }
  if (textLower.includes("toda terça") || textLower.includes("every tuesday")) {
    return "RRULE:FREQ=WEEKLY;BYDAY=TU";
  }
  if (textLower.includes("toda quarta") || textLower.includes("every wednesday")) {
    return "RRULE:FREQ=WEEKLY;BYDAY=WE";
  }
  if (textLower.includes("toda quinta") || textLower.includes("every thursday")) {
    return "RRULE:FREQ=WEEKLY;BYDAY=TH";
  }
  if (textLower.includes("toda sexta") || textLower.includes("every friday")) {
    return "RRULE:FREQ=WEEKLY;BYDAY=FR";
  }
  if (textLower.includes("semanal") || textLower.includes("weekly")) {
    return "RRULE:FREQ=WEEKLY";
  }
  if (textLower.includes("mensal") || textLower.includes("monthly")) {
    return "RRULE:FREQ=MONTHLY";
  }
  if (textLower.includes("diário") || textLower.includes("diario") || textLower.includes("daily")) {
    return "RRULE:FREQ=DAILY";
  }

  return null;
}
