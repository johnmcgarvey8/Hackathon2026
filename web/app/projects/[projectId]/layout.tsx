import { ProjectShell } from "@/components/project-shell";

export default async function ScopedProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <ProjectShell key={projectId}>{children}</ProjectShell>;
}
