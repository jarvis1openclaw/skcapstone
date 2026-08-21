import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { foundationLabel } from "./foundation";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("SKLegal root element is missing");
}

createRoot(root).render(
  <StrictMode>
    <main>
      <h1>{foundationLabel("foundation")}</h1>
      <p>The legal workbench is not implemented in the foundation card.</p>
    </main>
  </StrictMode>,
);
