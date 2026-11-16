let catalogPromise

export const USD_TO_CNY_RATE = 7

export const loadModelMetadataCatalog = () => {
  catalogPromise ||= import('virtual:model-display-metadata').then(({ providers }) => ({ providers }))
  return catalogPromise
}

const getModelMetadata = (providers, providerId, modelId) => {
  return providers?.[providerId]?.models?.[modelId] || null
}

export const resolveModelDisplayMetadata = (providers, providerId, model = {}) => {
  const catalogModel = getModelMetadata(providers, providerId, model.id || model.model_id)
  const returnedInputModalities =
    model.input_modalities ||
    model.architecture?.input_modalities ||
    model.raw_metadata?.architecture?.input_modalities ||
    []
  const inputModalities = returnedInputModalities.length
    ? returnedInputModalities
    : catalogModel?.modalities?.input || []
  const context = model.context_length || catalogModel?.limit?.context || null
  const cost = normalizeRemotePrice(model.pricing) || catalogModel?.cost

  return {
    matched: !!catalogModel,
    inputModalities,
    context,
    contextLabel: formatModelTokenCount(context),
    isOneMillionContext: context >= 1_000_000 && context < 1_500_000,
    vision: inputModalities.includes('image'),
    price: cost
  }
}

const formatModelTokenCount = (value) => {
  if (!Number.isFinite(value)) return ''
  if (value >= 1_000_000) return `${trimTrailingZeros((value / 1_000_000).toFixed(1))}M`
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`
  return String(value)
}

const formatModelPrice = (value) => {
  if (!Number.isFinite(value)) return ''
  const precision = value < 0.01 ? 4 : value < 1 ? 3 : 2
  return trimTrailingZeros(value.toFixed(precision))
}

export const formatModelPriceDisplay = (price, currency = 'USD') => {
  if (!Number.isFinite(price?.input) || !Number.isFinite(price?.output)) return ''
  const rate = currency === 'CNY' ? USD_TO_CNY_RATE : 1
  const symbol = currency === 'CNY' ? '¥' : '$'
  return `${symbol}${formatModelPrice(price.input * rate)} / ${symbol}${formatModelPrice(price.output * rate)}`
}

const normalizeRemotePrice = (pricing) => {
  if (!pricing) return null
  const input = Number.parseFloat(pricing.prompt ?? pricing.prompt_price ?? pricing.input)
  const output = Number.parseFloat(pricing.completion ?? pricing.completion_price ?? pricing.output)
  if (!Number.isFinite(input) || !Number.isFinite(output) || input < 0 || output < 0) {
    return null
  }
  return { input: input * 1_000_000, output: output * 1_000_000 }
}

const trimTrailingZeros = (value) => String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, '$1')
