import React from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, Routes, Route } from "react-router-dom";

import { App } from "./App";
import { ProductDashboard } from "./pages/ProductDashboard";
import { Settings } from "./pages/Settings";
import "./styles.css";

import { initApiBase } from "./api/agentApi";

initApiBase().then(() => {
  createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <HashRouter>
        <Routes>
          <Route path="/" element={<ProductDashboard />} />
          <Route path="/dev" element={<App />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </HashRouter>
    </React.StrictMode>,
  );
});
