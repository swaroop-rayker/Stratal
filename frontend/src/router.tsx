// Every screen and every selected object has a URL (SCREENS.md §1, deep links).
import { createBrowserRouter, Navigate } from "react-router";

import { Crash } from "./screens/Crash";
import { DatasetsScreen } from "./screens/datasets/DatasetsScreen";
import { JobScreen } from "./screens/job/JobScreen";
import { JobsScreen } from "./screens/jobs/JobsScreen";
import { Placeholder } from "./screens/Placeholder";
import { ReportScreen } from "./screens/report/ReportScreen";
import { ReviewScreen } from "./screens/review/ReviewScreen";
import { VersionScreen } from "./screens/version/VersionScreen";
import { AppShell } from "./shell/AppShell";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      {
        // A screen that throws shows an error state inside the shell.
        errorElement: <Crash />,
        children: [
          { index: true, element: <Navigate to="/datasets" replace /> },
          { path: "datasets", element: <DatasetsScreen /> },
          { path: "datasets/:slug", element: <DatasetsScreen /> },
          { path: "versions/:versionId", element: <VersionScreen /> },
          { path: "jobs", element: <JobsScreen /> },
          { path: "jobs/:jobId", element: <JobScreen /> },
          { path: "reviews/:reviewId", element: <ReviewScreen /> },
          { path: "versions/:versionId/report", element: <ReportScreen /> },
          { path: "*", element: <Placeholder screen="Not found" crumb="Not found" /> },
        ],
      },
    ],
  },
]);
