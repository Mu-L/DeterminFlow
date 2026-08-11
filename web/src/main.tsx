import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ExtensionProvider } from "./extensions/context";
import { initializeTheme } from "./lib/theme";
import { ThemeProvider } from "./theme";
import "./index.css";

initializeTheme();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <ExtensionProvider>
        <App />
      </ExtensionProvider>
    </ThemeProvider>
  </React.StrictMode>
);
