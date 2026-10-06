// A minimal deterministic zip writer: local headers, central directory,
// CRC32, deflate (node:zlib) — no extra fields, no data descriptors, no
// directory entries, every entry a regular file with mode 0644 dated
// 1980-01-01 00:00. Same input (sorted names + bytes) → same bytes.
import { Buffer } from "node:buffer";
import { deflateRawSync } from "node:zlib";

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

export function crc32(bytes) {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

const DOS_TIME = 0; // 00:00:00
const DOS_DATE = (0 << 9) | (1 << 5) | 1; // 1980-01-01
const UTF8_FLAG = 0x0800;
// "Version made by": Unix (3) / zip 2.0, so the external attributes carry a
// Unix mode; regular file 0644 (S_IFREG | 0644), as Go's SetMode(0o644).
const MADE_BY_UNIX = (3 << 8) | 20;
const EXTERNAL_ATTR_0644 = (0o100644 << 16) >>> 0;
const LIMIT = 0xffffffff;

/**
 * Zip ``entries`` (``[{ name, data }]``, ``name`` a relative slash path,
 * ``data`` a Buffer / Uint8Array) in the order given. Callers sort names for
 * determinism. Returns a Buffer.
 */
export function createZip(entries) {
  const chunks = [];
  const central = [];
  let offset = 0;

  for (const { name, data } of entries) {
    if (!name || name.startsWith("/") || name.split("/").includes("..")) {
      throw new Error(`zip: invalid entry name "${name}"`);
    }
    const raw = Buffer.from(data);
    const nameBytes = Buffer.from(name, "utf8");
    const flags = /^[\x20-\x7e]*$/.test(name) ? 0 : UTF8_FLAG;
    const deflated = raw.length ? deflateRawSync(raw, { level: 9 }) : Buffer.alloc(0);
    const stored = deflated.length >= raw.length;
    const method = stored ? 0 : 8;
    const body = stored ? raw : deflated;
    const crc = crc32(raw);
    if (raw.length >= LIMIT || body.length >= LIMIT || offset >= LIMIT) {
      throw new Error("zip: the package is too large (zip64 is not supported)");
    }

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4); // version needed
    local.writeUInt16LE(flags, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt16LE(DOS_TIME, 10);
    local.writeUInt16LE(DOS_DATE, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(raw.length, 22);
    local.writeUInt16LE(nameBytes.length, 26);
    local.writeUInt16LE(0, 28); // extra length
    chunks.push(local, nameBytes, body);

    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(MADE_BY_UNIX, 4); // version made by
    header.writeUInt16LE(20, 6); // version needed
    header.writeUInt16LE(flags, 8);
    header.writeUInt16LE(method, 10);
    header.writeUInt16LE(DOS_TIME, 12);
    header.writeUInt16LE(DOS_DATE, 14);
    header.writeUInt32LE(crc, 16);
    header.writeUInt32LE(body.length, 20);
    header.writeUInt32LE(raw.length, 24);
    header.writeUInt16LE(nameBytes.length, 28);
    header.writeUInt16LE(0, 30); // extra length
    header.writeUInt16LE(0, 32); // comment length
    header.writeUInt16LE(0, 34); // disk number start
    header.writeUInt16LE(0, 36); // internal attributes
    header.writeUInt32LE(EXTERNAL_ATTR_0644, 38); // external attributes
    header.writeUInt32LE(offset, 42);
    central.push(header, nameBytes);

    offset += local.length + nameBytes.length + body.length;
  }

  const centralSize = central.reduce((sum, b) => sum + b.length, 0);
  if (entries.length > 0xffff) throw new Error("zip: too many entries");
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...chunks, ...central, end]);
}
