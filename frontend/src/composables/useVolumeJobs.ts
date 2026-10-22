import { NovelAPI, type NovelProject } from '@/api/novel'
import { useJob } from './useJob'

export function useVolumeJobs(projectId: () => string | undefined, updated: (project: NovelProject) => void) {
  const jobs = useJob(projectId, {
    kind: 'volume_planning', kinds: ['blueprint_generate', 'volume_outline', 'volumes_outline', 'volume_smart_split'],
    async onTerminal(job, signal) {
      const project = await NovelAPI.getNovel(job.project_id, { signal })
      if (!signal.aborted && projectId() === job.project_id) updated(project)
    }
  })
  const generate = (number: number) => jobs.submit(String(number), (key, signal) => NovelAPI.generateVolumeOutline(projectId()!, number, key, { signal }))
  const generateAll = () => jobs.submit('remaining', (key, signal) => NovelAPI.generateRemainingVolumes(projectId()!, key, { signal }))
  const smartSplit = () => jobs.submit('plan', (key, signal) => NovelAPI.smartSplitVolumes(projectId()!, key, { signal }))
  return { ...jobs, generate, generateAll, smartSplit }
}
