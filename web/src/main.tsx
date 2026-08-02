import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ExtensionProvider } from "./extensions/context";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ExtensionProvider>
      <App />
    </ExtensionProvider>
  </React.StrictMode>
);
