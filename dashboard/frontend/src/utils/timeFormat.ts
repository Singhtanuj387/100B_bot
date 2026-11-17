/**
 * Time formatting utilities enforcing America/New_York (ET) channel time.
 * All user-facing times across the dashboard are formatted in America/New_York
 * and explicitly labeled with "ET".
 */

export const CHANNEL_TIMEZONE = 'America/New_York';
export const TZ_LABEL = 'ET';

export interface FormatTimeOptions {
  omitZeroMinutes?: boolean; // If true, 12:00 -> "12 PM ET", 18:30 -> "6:30 PM ET"
  includeLabel?: boolean;     // If true, appends "ET"
}

/**
 * Format any Date, ISO string, "HH:MM", or minutes-from-midnight into ET formatted time.
 * Examples:
 *   formatTimeET("2026-11-17T12:00:00-05:00") -> "12 PM ET"
 *   formatTimeET("2026-11-17T14:00:00-05:00") -> "2 PM ET"
 *   formatTimeET("2026-11-17T17:00:00-05:00") -> "5 PM ET"
 *   formatTimeET("2026-11-16T18:00:00-05:00") -> "6 PM ET"
 *   formatTimeET("2026-11-16T18:02:00-05:00") -> "6:02 PM ET"
 *   formatTimeET("18:30") -> "6:30 PM ET"
 *   formatTimeET(720) -> "12 PM ET"
 */
export function formatTimeET(
  input: string | Date | number | null | undefined,
  options: FormatTimeOptions = {}
): string {
  const { omitZeroMinutes = true, includeLabel = true } = options;

  if (input === null || input === undefined || input === '') {
    return `—${includeLabel ? ` ${TZ_LABEL}` : ''}`;
  }

  // 1. If input is minutes from midnight (number)
  if (typeof input === 'number') {
    const clamped = Math.min(1439, Math.max(0, input));
    const h24 = Math.floor(clamped / 60);
    const m = clamped % 60;
    const period = h24 >= 12 ? 'PM' : 'AM';
    let h12 = h24 % 12;
    if (h12 === 0) h12 = 12;

    const mStr = (omitZeroMinutes && m === 0) ? '' : `:${m.toString().padStart(2, '0')}`;
    const labelStr = includeLabel ? ` ${TZ_LABEL}` : '';
    return `${h12}${mStr} ${period}${labelStr}`;
  }

  // 2. If input is a simple "HH:MM" string (e.g. "12:00", "18:30")
  if (typeof input === 'string') {
    const trimmed = input.trim();
    const hhmmMatch = trimmed.match(/^(\d{1,2}):(\d{2})$/);
    if (hhmmMatch) {
      const h = parseInt(hhmmMatch[1], 10);
      const m = parseInt(hhmmMatch[2], 10);
      return formatTimeET(h * 60 + m, options);
    }
  }

  // 3. Otherwise parse as Date / ISO timestamp
  let dt: Date;
  if (typeof input === 'string') {
    const parsed = parseDateSafe(input);
    if (!parsed) {
      return `${input}${includeLabel ? ` ${TZ_LABEL}` : ''}`;
    }
    dt = parsed;
  } else {
    dt = input;
  }

  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: CHANNEL_TIMEZONE,
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    }).formatToParts(dt);

    let hour = '12';
    let minute = '00';
    let dayPeriod = 'PM';

    for (const part of parts) {
      if (part.type === 'hour') hour = part.value;
      if (part.type === 'minute') minute = part.value;
      if (part.type === 'dayPeriod') dayPeriod = part.value;
    }

    const mStr = (omitZeroMinutes && minute === '00') ? '' : `:${minute}`;
    const labelStr = includeLabel ? ` ${TZ_LABEL}` : '';
    return `${hour}${mStr} ${dayPeriod}${labelStr}`;
  } catch {
    return `${input}${includeLabel ? ` ${TZ_LABEL}` : ''}`;
  }
}

/**
 * Parse any date input safely into a Date object.
 * Critical: date-only strings like "YYYY-MM-DD" are parsed by ECMAScript new Date("YYYY-MM-DD")
 * as UTC midnight (00:00:00Z). In timezones west of UTC such as America/New_York (UTC-5/UTC-4),
 * UTC midnight shifts the date backwards to the previous evening (e.g. 2026-11-17 becomes Nov 16, 7:00 PM EST).
 * Setting the time to midday UTC ("YYYY-MM-DDT12:00:00Z") guarantees that formatting in America/New_York
 * remains strictly on the exact calendar date (7:00 AM EST or 8:00 AM EDT).
 */
export function parseDateSafe(input: string | Date | null | undefined): Date | null {
  if (!input) return null;
  if (input instanceof Date) return isNaN(input.getTime()) ? null : input;
  if (typeof input !== 'string') return null;

  const trimmed = input.trim();
  if (!trimmed) return null;

  const dateOnlyMatch = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateOnlyMatch) {
    const dt = new Date(`${trimmed}T12:00:00Z`);
    return isNaN(dt.getTime()) ? null : dt;
  }

  const dt = new Date(trimmed);
  return isNaN(dt.getTime()) ? null : dt;
}

/**
 * Format weekday in ET. E.g. "Sun", "Mon", "Tue"
 */
export function formatWeekdayET(input: string | Date | null | undefined): string {
  if (!input) return '';
  try {
    const dt = parseDateSafe(input);
    if (!dt) return '';
    return new Intl.DateTimeFormat('en-US', {
      timeZone: CHANNEL_TIMEZONE,
      weekday: 'short',
    }).format(dt);
  } catch {
    return '';
  }
}

/**
 * Format full date in ET. E.g. "Sunday, November 15" or "Tue, Nov 17"
 */
export function formatDateET(
  input: string | Date | null | undefined,
  options: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric' }
): string {
  if (!input) return '';
  try {
    const dt = parseDateSafe(input);
    if (!dt) return String(input);

    // If only weekday and day are requested (e.g. { weekday: 'short', day: 'numeric' }),
    // standard en-US formatToParts returns "16 Mon". In dashboard UI ("Mon 16", "Sun 8"),
    // English convention requires weekday before day ("Mon 16").
    if (options.weekday && options.day && !options.month && !options.year) {
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: CHANNEL_TIMEZONE,
        ...options,
      }).formatToParts(dt);
      let w = '';
      let d = '';
      for (const p of parts) {
        if (p.type === 'weekday') w = p.value;
        if (p.type === 'day') d = p.value;
      }
      if (w && d) return `${w} ${d}`;
    }

    return new Intl.DateTimeFormat('en-US', {
      timeZone: CHANNEL_TIMEZONE,
      ...options,
    }).format(dt);
  } catch {
    return String(input);
  }
}
