import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";

import { App } from "./App";
import { ProductDashboard } from "./pages/ProductDashboard";
import "./styles.css";

createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ProductDashboard />} />
        <Route path="/dev" element={<App />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
