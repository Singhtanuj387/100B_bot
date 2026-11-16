import { describe, it, expect } from 'vitest';
import {
  parseHHMM,
  minutesToHHMM,
  minutesTo12Hour,
  parse12Hour,
  snapTo15Minutes,
  clampSlotsCount,
  sortSlots,
  findNextAvailableSlot,
  generatePreset,
} from '../utils/scheduleMath';

describe('scheduleMath utils', () => {
  it('converts HH:MM to minutes correctly', () => {
    expect(parseHHMM('00:00')).toBe(0);
    expect(parseHHMM('06:15')).toBe(375);
    expect(parseHHMM('12:00')).toBe(720);
    expect(parseHHMM('18:30')).toBe(1110);
    expect(parseHHMM('23:45')).toBe(1425);
  });

  it('converts minutes to HH:MM correctly', () => {
    expect(minutesToHHMM(0)).toBe('00:00');
    expect(minutesToHHMM(375)).toBe('06:15');
    expect(minutesToHHMM(720)).toBe('12:00');
    expect(minutesToHHMM(1110)).toBe('18:30');
    expect(minutesToHHMM(1425)).toBe('23:45');
  });

  it('converts minutes to 12-hour display strings', () => {
    expect(minutesTo12Hour(0)).toBe('12:00 AM');
    expect(minutesTo12Hour(60)).toBe('1:00 AM');
    expect(minutesTo12Hour(720)).toBe('12:00 PM');
    expect(minutesTo12Hour(780)).toBe('1:00 PM');
    expect(minutesTo12Hour(1110)).toBe('6:30 PM');
    expect(minutesTo12Hour(1425)).toBe('11:45 PM');
  });

  it('parses 12-hour strings accurately', () => {
    expect(parse12Hour('12:00 AM')).toBe(0);
    expect(parse12Hour('6:30 PM')).toBe(1110);
    expect(parse12Hour('6:30pm')).toBe(1110);
    expect(parse12Hour('11:45 PM')).toBe(1425);
    expect(parse12Hour('12:00 PM')).toBe(720);
    expect(parse12Hour('invalid')).toBeNull();
  });

  it('snaps accurately to 15-minute intervals and bounds', () => {
    expect(snapTo15Minutes(1110)).toBe(1110); // 18:30 exact
    expect(snapTo15Minutes(1114)).toBe(1110); // rounds down to 18:30
    expect(snapTo15Minutes(1118)).toBe(1125); // rounds up to 18:45
    expect(snapTo15Minutes(-10)).toBe(0);      // lower bound
    expect(snapTo15Minutes(1500)).toBe(1425);  // upper bound (23:45)
  });

  it('clamps slot counts between 1 and 12', () => {
    expect(clampSlotsCount(0)).toBe(1);
    expect(clampSlotsCount(1)).toBe(1);
    expect(clampSlotsCount(6)).toBe(6);
    expect(clampSlotsCount(12)).toBe(12);
    expect(clampSlotsCount(15)).toBe(12);
  });

  it('sorts slots chronologically', () => {
    const raw = ['18:30', '09:00', '21:15', '12:00'];
    expect(sortSlots(raw)).toEqual(['09:00', '12:00', '18:30', '21:15']);
  });

  it('finds next unoccupied 15-minute slot', () => {
    const existing = ['18:00', '19:00'];
    const nextSlot = findNextAvailableSlot(existing);
    expect(existing).not.toContain(nextSlot);
    expect(parseHHMM(nextSlot) % 15).toBe(0);
  });

  it('generates presets with correct count and unique 15-minute values', () => {
    const presets = ['even_spread', 'evening_heavy', 'morning_and_night'] as const;
    for (const p of presets) {
      for (const count of [1, 3, 6, 8, 12]) {
        const slots = generatePreset(p, count);
        expect(slots.length).toBe(count);
        // Ensure strictly ascending and unique
        for (let i = 0; i < slots.length; i++) {
          const mins = parseHHMM(slots[i]);
          expect(mins % 15).toBe(0);
          if (i > 0) {
            expect(parseHHMM(slots[i])).toBeGreaterThan(parseHHMM(slots[i - 1]));
          }
        }
      }
    }
  });
});
