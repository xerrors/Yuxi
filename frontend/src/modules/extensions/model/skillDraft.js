export function collectSkillChanges(saved, draft, nodes) {
  return [
    ...nodes,
    ...Object.entries(draft)
      .filter(([path, content]) => saved[path] !== content)
      .map(([path, content]) => ({
        action: path in saved || nodes.some((node) => node.path === path) ? 'write' : 'create',
        path,
        content
      }))
  ]
}
