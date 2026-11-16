/** 创建一个失败后可重试、成功后复用结果的单飞异步操作。 */
export const createSingleFlight = (run) => {
  let promise = null
  let result
  let generation = 0

  const execute = async (...args) => {
    if (result !== undefined) return result
    if (promise) return promise

    const currentGeneration = generation
    promise = Promise.resolve()
      .then(() => run(...args))
      .then((value) => {
        if (currentGeneration === generation) result = value
        return value
      })
      .catch((error) => {
        if (currentGeneration === generation) promise = null
        throw error
      })

    return promise
  }

  execute.reset = () => {
    generation += 1
    promise = null
    result = undefined
  }

  return execute
}
