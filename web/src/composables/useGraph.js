import { ref, reactive, nextTick } from 'vue'

export function useGraph(graphRef) {
  const fetching = ref(false)
  const showDetailDrawer = ref(false)
  const selectedItem = ref(null)
  const selectedItemType = ref(null)

  const graphData = reactive({
    nodes: [],
    edges: []
  })

  const handleNodeClick = (nodeData) => {
    selectedItem.value = nodeData
    selectedItemType.value = 'node'
    showDetailDrawer.value = true
    console.log('Node clicked:', nodeData)
  }

  const handleEdgeClick = (edgeData) => {
    selectedItem.value = edgeData
    selectedItemType.value = 'edge'
    showDetailDrawer.value = true
    console.log('Edge clicked:', edgeData)
  }

  const handleCanvasClick = () => {
    showDetailDrawer.value = false
    selectedItem.value = null
    selectedItemType.value = null
    // Clear focus state on the graph component if available
    if (graphRef && graphRef.value && graphRef.value.clearFocus) {
      graphRef.value.clearFocus()
    }
  }

  const clearGraph = () => {
    graphData.nodes = []
    graphData.edges = []
    handleCanvasClick()
  }

  const updateGraphData = (nodes, edges) => {
    graphData.nodes = nodes || []
    graphData.edges = edges || []
    // Refresh graph visual after data update
    refreshGraph()
  }

  const refreshGraph = () => {
    nextTick(() => {
      if (graphRef && graphRef.value && graphRef.value.refreshGraph) {
        graphRef.value.refreshGraph()
      }
    })
  }

  return {
    fetching,
    graphData,
    showDetailDrawer,
    selectedItem,
    selectedItemType,
    handleNodeClick,
    handleEdgeClick,
    handleCanvasClick,
    clearGraph,
    updateGraphData,
    refreshGraph
  }
}
