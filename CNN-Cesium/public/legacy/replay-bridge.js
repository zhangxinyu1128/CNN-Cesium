(() => {
  const nativeSetInterval = window.setInterval.bind(window)
  const nativeClearInterval = window.clearInterval.bind(window)

  // Cesium clears its WebGL drawing buffer after presenting a frame by default.
  // Keep the buffer so the host page can export the visible map as PNG/video.
  const NativeViewer = window.Cesium && window.Cesium.Viewer
  if (NativeViewer && !NativeViewer.__mainExportPatched) {
    const ExportViewer = function (container, options) {
      const nextOptions = { ...(options || {}) }
      nextOptions.contextOptions = { ...(nextOptions.contextOptions || {}) }
      nextOptions.contextOptions.webgl = {
        ...(nextOptions.contextOptions.webgl || {}),
        preserveDrawingBuffer: true
      }
      return new NativeViewer(container, nextOptions)
    }
    ExportViewer.prototype = NativeViewer.prototype
    ExportViewer.__mainExportPatched = true
    window.Cesium.Viewer = ExportViewer
  }

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
