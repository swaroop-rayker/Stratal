// Every screen and every selected object has a URL (SCREENS.md §1, deep links).
import { createBrowserRouter, Navigate } from "react-router";

import { BuilderScreen } from "./screens/build/BuilderScreen";
import { RunScreen } from "./screens/build/RunScreen";
import { Crash } from "./screens/Crash";
import { DatasetsScreen } from "./screens/datasets/DatasetsScreen";
import { JobScreen } from "./screens/job/JobScreen";
import { JobsScreen } from "./screens/jobs/JobsScreen";
import { LineageScreen } from "./screens/models/LineageScreen";
import { ModelsScreen } from "./screens/models/ModelsScreen";
import { Placeholder } from "./screens/Placeholder";
import { ReportScreen } from "./screens/report/ReportScreen";
import { ReviewScreen } from "./screens/review/ReviewScreen";
import { SettingsScreen } from "./screens/settings/SettingsScreen";
import { StudyScreen } from "./screens/studies/StudyScreen";
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
          { path: "models", element: <ModelsScreen /> },
          { path: "models/build", element: <BuilderScreen /> },
          { path: "models/:lineageId", element: <LineageScreen /> },
          { path: "runs/new", element: <RunScreen /> },
          { path: "studies/:studyId", element: <StudyScreen /> },
          { path: "settings", element: <SettingsScreen /> },
          { path: "*", element: <Placeholder screen="Not found" crumb="Not found" /> },
        ],
      },
    ],
  },
]);
