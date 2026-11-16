import { describe, it, expect } from 'vitest';
import { stripHashtags } from '../utils/textFormat';

describe('stripHashtags', () => {
  it('strips #shorts and #funny from title ends', () => {
    expect(stripHashtags('He Had No Idea It Was A Paper Wasp Nest #shorts #funny')).toBe(
      'He Had No Idea It Was A Paper Wasp Nest'
    );
  });

  it('strips embedded and multiple hashtags cleanly', () => {
    expect(stripHashtags('#viral Cat jumps over the moon #shorts #funny #catlover')).toBe(
      'Cat jumps over the moon'
    );
  });

  it('handles titles with no hashtags', () => {
    expect(stripHashtags('Normal title without tags')).toBe('Normal title without tags');
  });

  it('handles empty or null titles', () => {
    expect(stripHashtags('')).toBe('');
    expect(stripHashtags(null)).toBe('');
    expect(stripHashtags(undefined)).toBe('');
  });
});
