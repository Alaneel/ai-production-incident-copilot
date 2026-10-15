import type { Metadata } from "next";
import { IncidentConsole } from "./IncidentConsole";

export const metadata: Metadata = {
  title: "Incident INC-2847",
  description:
    "Evidence-first production incident diagnosis with guarded remediation.",
};

export default function Home() {
  return <IncidentConsole />;
}
