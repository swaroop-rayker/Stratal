// Every screen and every selected object has a URL (SCREENS.md §1, deep links).
import { createBrowserRouter, Navigate } from "react-router";

import { Placeholder } from "./screens/Placeholder";
import { AppShell } from "./shell/AppShell";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/datasets" replace /> },
      { path: "datasets", element: <Placeholder screen="SCR-02 Datasets and versions" crumb="Datasets" /> },
      { path: "datasets/:slug", element: <Placeholder screen="SCR-02 Datasets and versions" crumb="Datasets" /> },
      { path: "jobs", element: <Placeholder screen="SCR-07 Jobs list" crumb="Jobs" /> },
      { path: "jobs/:jobId", element: <Placeholder screen="SCR-04 Live job view" crumb="Jobs" /> },
      { path: "*", element: <Placeholder screen="Not found" crumb="Not found" /> },
    ],
  },
]);
