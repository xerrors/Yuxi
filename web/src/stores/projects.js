import { ref } from 'vue'
import { defineStore } from 'pinia'
import { projectApi } from '@/apis/project_api'

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref([])
  const isLoading = ref(false)
  const error = ref('')
  let requestVersion = 0

  const invalidatePendingLoad = () => {
    requestVersion += 1
    isLoading.value = false
    error.value = ''
  }

  const loadProjects = async () => {
    const currentVersion = ++requestVersion
    isLoading.value = true
    error.value = ''
    try {
      const loadedProjects = (await projectApi.getProjects()) || []
      if (currentVersion === requestVersion) projects.value = loadedProjects
      return projects.value
    } catch (loadError) {
      if (currentVersion === requestVersion) error.value = '项目加载失败'
      throw loadError
    } finally {
      if (currentVersion === requestVersion) isLoading.value = false
    }
  }

  const upsertProject = (project) => {
    if (!project?.id) return
    invalidatePendingLoad()
    projects.value = [project, ...projects.value.filter((item) => item.id !== project.id)]
  }

  const replaceProject = (project) => {
    if (!project?.id) return
    invalidatePendingLoad()
    projects.value = projects.value.map((item) => (item.id === project.id ? project : item))
  }

  const removeProject = (projectId) => {
    if (!projectId) return
    invalidatePendingLoad()
    projects.value = projects.value.filter((project) => project.id !== projectId)
  }

  return {
    projects,
    isLoading,
    error,
    loadProjects,
    upsertProject,
    replaceProject,
    removeProject
  }
})
