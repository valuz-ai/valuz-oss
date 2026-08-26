import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { DocumentDetailPanel } from "@valuz/ui";
import "./index.css";

const table = (rows: number, tag: string) =>
  "| Date | Open | High | Low | Close | Volume | Adj | Note |\n" +
  "|---|---|---|---|---|---|---|---|\n" +
  Array.from({ length: rows }, (_, i) =>
    `| ${tag}-${i} | ${i}.11 | ${i}.22 | ${i}.33 | ${i}.44 | ${i * 7} | ${i}.55 | row ${i} |`,
  ).join("\n");

const DOCS = [
  { name: "big.xlsx", md: table(5000, "big") },
  { name: "tiny.md", md: "# tiny\n\njust a line" },
  { name: "mid.xlsx", md: table(300, "mid") },
];


function App() {
  const [i, setI] = useState(0);
  const d = DOCS[i];
  return (
    <div className="p-4">
      <div className="flex gap-2 pb-2">
        {DOCS.map((x, k) => (
          <button key={x.name} data-testid={`doc-${k}`} onClick={() => setI(k)}>{x.name}</button>
        ))}
      </div>
      <div data-testid="host" style={{ width: 720 }}>
        <DocumentDetailPanel
          doc={{ name: d.name, format: "XLSX", status: "ready",
                 preview: { markdown: d.md, truncated: false } }}
        />
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
