/**
 * Splitting a parsed document into units small enough to virtualize.
 *
 * Block-level splitting alone does not help the case this exists for. The
 * document that stalls the panel is a spreadsheet flattened to GFM, and a
 * table — however many rows — is a *single* block. Measured: rendering cost
 * tracks table cells, not bytes. At equal size, a table costs 3.9x a
 * paragraph at 2,000 cells and 12.6x at 16,000, while prose stays linear. So
 * a table has to be cut along its rows or nothing is gained.
 *
 * Each unit is valid markdown on its own — a table chunk carries the header
 * and delimiter rows — because the renderer is handed one unit at a time and
 * has no memory of the last.
 */

/** Rows per table chunk. */
export const TABLE_ROWS_PER_UNIT = 40;

const isTableDelimiter = (line: string): boolean =>
  /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(line) &&
  line.includes("-");

const isTableRow = (line: string): boolean => line.trimStart().startsWith("|");

/**
 * Whether a fence opens or closes here. Tracked so a table drawn inside a code
 * block is left alone: it is text to display, not a table to cut up.
 */
const isFence = (line: string): boolean => /^\s*(```|~~~)/.test(line);

export function splitIntoUnits(
  markdown: string,
  rowsPerUnit: number = TABLE_ROWS_PER_UNIT,
): string[] {
  if (!markdown) return [];
  const lines = markdown.split("\n");
  const units: string[] = [];
  let buffer: string[] = [];
  let inFence = false;

  const flush = () => {
    if (buffer.length) {
      units.push(buffer.join("\n"));
      buffer = [];
    }
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    if (isFence(line)) {
      inFence = !inFence;
      buffer.push(line);
      continue;
    }
    if (inFence) {
      buffer.push(line);
      continue;
    }

    // A table starts where a row is followed by a delimiter. Both lines are
    // required: a lone leading pipe is ordinary text far more often than it is
    // a table, and treating it as one would split a paragraph mid-sentence.
    const startsTable =
      isTableRow(line) &&
      i + 1 < lines.length &&
      isTableDelimiter(lines[i + 1]);

    if (!startsTable) {
      buffer.push(line);
      // Blank line ends a block. Cheap, and it keeps ordinary prose in units
      // small enough that measuring them is not itself the cost.
      if (line.trim() === "") flush();
      continue;
    }

    flush();
    const header = line;
    const delimiter = lines[i + 1];
    i += 1;

    const body: string[] = [];
    while (i + 1 < lines.length && isTableRow(lines[i + 1])) {
      i += 1;
      body.push(lines[i]);
    }

    if (body.length <= rowsPerUnit) {
      units.push([header, delimiter, ...body].join("\n"));
      continue;
    }
    for (let start = 0; start < body.length; start += rowsPerUnit) {
      units.push(
        [header, delimiter, ...body.slice(start, start + rowsPerUnit)].join(
          "\n",
        ),
      );
    }
  }

  flush();
  return units.filter((unit) => unit.trim() !== "");
}

/**
 * How many table rows the document contains, counted the same way
 * ``splitIntoUnits`` finds them — a row line following a delimiter, outside
 * any code fence. Used to decide whether windowing is worth its cost, since
 * cost tracks table cells and nothing else in a document produces them in
 * bulk.
 */
export function countTableRows(markdown: string): number {
  if (!markdown) return 0;
  const lines = markdown.split("\n");
  let rows = 0;
  let inFence = false;
  let inTable = false;

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (isFence(line)) {
      inFence = !inFence;
      inTable = false;
      continue;
    }
    if (inFence) continue;

    if (!isTableRow(line)) {
      inTable = false;
      continue;
    }
    if (inTable) {
      rows += 1;
      continue;
    }
    // A lone leading pipe is prose far more often than it is a table; the
    // delimiter on the next line is what makes it one.
    if (i + 1 < lines.length && isTableDelimiter(lines[i + 1])) {
      inTable = true;
      i += 1;
    }
  }
  return rows;
}
