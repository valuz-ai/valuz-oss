import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { A2UIGallery } from "../src/gallery";
import "./demo.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <A2UIGallery />
  </StrictMode>,
);
