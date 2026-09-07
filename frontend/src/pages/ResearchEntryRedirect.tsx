import { Navigate, useParams } from 'react-router-dom'

/** Legacy workspace entry now supplies seeds to the shared research workflow. */
export default function ResearchEntryRedirect() {
  const { id } = useParams()
  return <Navigate to={`/explore?workspace_id=${encodeURIComponent(id ?? '')}`} replace />
}
