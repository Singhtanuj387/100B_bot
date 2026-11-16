/**
 * Utility functions for text formatting in the dashboard UI.
 */

/**
 * Strips hashtags such as #shorts, #funny, #viral from titles for clean UI display.
 * Example: "He Had No Idea It Was A Paper Wasp Nest #shorts #funny"
 *       -> "He Had No Idea It Was A Paper Wasp Nest"
 */
export function stripHashtags(title?: string | null): string {
  if (!title) return '';
  return title
    .replace(/#[\w\d_-]+/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}
