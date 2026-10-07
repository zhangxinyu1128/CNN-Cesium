(() => {
  const nativeSetInterval = window.setInterval.bind(window)
  const nativeClearInterval = window.clearInterval.bind(window)

  function isLegacyTyphoonAnimation(handler, timeout) {
    if (typeof handler !== 'function' || Number(timeout) !== 200) return false
    const source = Function.prototype.toString.call(handler)
    return source.includes('_currentPointObj') && source.includes('_typhoonData')
  }

  // The main page owns the replay clock. Keep the legacy bundle's polygon timer stopped.
  window.setInterval = function (handler, timeout, ...args) {
    if (isLegacyTyphoonAnimation(handler, timeout)) {
      return { __legacyTyphoonAnimationTimer: true }
    }
    return nativeSetInterval(handler, timeout, ...args)
  }

  window.clearInterval = function (timer) {
    if (timer && typeof timer === 'object' && timer.__legacyTyphoonAnimationTimer) return
    nativeClearInterval(timer)
  }

  function patchViewerCamera() {
    const viewer = window.viewer
    const cesium = window.Cesium
    if (!viewer || typeof viewer.flyTo !== 'function' || !cesium || viewer.__mainCameraPatched) return

    const nativeFlyTo = viewer.flyTo.bind(viewer)
    viewer.flyTo = function (target, options) {
      const nextOptions = { ...(options || {}) }
      if (!nextOptions.offset && cesium.HeadingPitchRange && cesium.Math) {
        nextOptions.offset = new cesium.HeadingPitchRange(
          0,
          cesium.Math.toRadians(-68),
          0
        )
      }
      return nativeFlyTo(target, nextOptions)
    }
    viewer.__mainCameraPatched = true
  }

  nativeSetInterval(patchViewerCamera, 50)
})()
