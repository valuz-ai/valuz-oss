import {
  Braces,
  File,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileTerminal,
  FileText,
  Settings,
  type LucideIcon,
} from "lucide-react";

const FILE_ICONS: Record<string, LucideIcon> = {
  // Plain text / docs
  txt: FileText,
  md: FileText,
  markdown: FileText,
  doc: FileText,
  docx: FileText,
  rtf: FileText,
  pdf: FileText,
  // Spreadsheets / data tables
  csv: FileSpreadsheet,
  tsv: FileSpreadsheet,
  xls: FileSpreadsheet,
  xlsx: FileSpreadsheet,
  // Structured data
  json: Braces,
  yml: Braces,
  yaml: Braces,
  toml: Braces,
  xml: Braces,
  // Shell / scripts
  sh: FileTerminal,
  bash: FileTerminal,
  zsh: FileTerminal,
  fish: FileTerminal,
  // Code
  py: FileCode,
  ts: FileCode,
  tsx: FileCode,
  js: FileCode,
  jsx: FileCode,
  rs: FileCode,
  go: FileCode,
  rb: FileCode,
  java: FileCode,
  kt: FileCode,
  swift: FileCode,
  c: FileCode,
  cc: FileCode,
  cpp: FileCode,
  h: FileCode,
  hpp: FileCode,
  cs: FileCode,
  php: FileCode,
  sql: FileCode,
  html: FileCode,
  css: FileCode,
  scss: FileCode,
  // Images
  png: FileImage,
  jpg: FileImage,
  jpeg: FileImage,
  gif: FileImage,
  svg: FileImage,
  webp: FileImage,
  // Config
  ini: Settings,
  conf: Settings,
  env: Settings,
};

export function getFileTypeIcon(name: string): LucideIcon {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return FILE_ICONS[ext] ?? File;
}
