"use client";

import { useEffect } from "react";

export function TeamsBootstrap() {
  useEffect(() => {
    if (window.self === window.top) {
      return;
    }

    void import("@microsoft/teams-js")
      .then(({ app }) => app.initialize())
      .catch(() => undefined);
  }, []);

  return null;
}