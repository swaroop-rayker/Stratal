// Every screen and every selected object has a URL (SCREENS.md §1, deep links).
import { createBrowserRouter, Navigate } from "react-router";

import { DatasetsScreen } from "./screens/datasets/DatasetsScreen";
import { Placeholder } from "./screens/Placeholder";
import { VersionScreen } from "./screens/version/VersionScreen";
import { AppShell } from "./shell/AppShell";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/datasets" replace /> },
      { path: "datasets", element: <DatasetsScreen /> },
      { path: "datasets/:slug", element: <DatasetsScreen /> },
      { path: "versions/:versionId", element: <VersionScreen /> },
      { path: "jobs", element: <Placeholder screen="SCR-07 Jobs list" crumb="Jobs" /> },
      { path: "jobs/:jobId", element: <Placeholder screen="SCR-04 Live job view" crumb="Jobs" /> },
      { path: "*", element: <Placeholder screen="Not found" crumb="Not found" /> },
    ],
  },
]);
