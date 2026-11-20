import chromeIcon from '@/assets/icons/devices/chrome.svg'
import macosIcon from '@/assets/icons/devices/macos.svg'
import windowsIcon from '@/assets/icons/devices/windows.svg'
import linuxIcon from '@/assets/icons/devices/linux.svg'
import androidIcon from '@/assets/icons/devices/android.svg'

/** 浏览器接入为 Chrome 扩展，电脑图标只依据远端平台，不猜测名称。 */
export function deviceAppearance(device) {
  if (device.kind === 'browser') return { icon: chromeIcon, platformLabel: 'Chrome · 浏览器' }
  const platform = device.platform?.trim() || ''
  if (/^(macos|mac os(?: x)?|darwin)(\b|$)/i.test(platform))
    return { icon: macosIcon, platformLabel: 'macOS · 电脑' }
  if (/^(windows|win32|win64)(\b|$)/i.test(platform))
    return { icon: windowsIcon, platformLabel: 'Windows · 电脑' }
  if (/^linux(\b|$)/i.test(platform))
    return { icon: linuxIcon, platformLabel: 'Linux · 电脑' }
  if (/^android(\b|$)/i.test(platform))
    return { icon: androidIcon, platformLabel: 'Android · 设备' }
  return { icon: null, platformLabel: platform ? `${platform} · 电脑` : '电脑' }
}
