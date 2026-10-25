import { ProjectWorkspaceView } from "@/components/proofos/workspace";

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProjectWorkspaceView projectId={id} />;
}
