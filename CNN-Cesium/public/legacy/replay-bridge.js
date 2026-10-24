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

    // Defensive: make sure the default camera controls are never left disabled,
    // otherwise the 3D globe cannot be dragged or zoomed.
    const ssc = viewer.scene && viewer.scene.screenSpaceCameraController
    if (ssc) {
      ssc.enableInputs = true
      ssc.enableRotate = true
      ssc.enableTranslate = true
      ssc.enableZoom = true
      ssc.enableTilt = true
      ssc.enableLook = true
    }

    viewer.__mainCameraPatched = true
  }

  // The legacy "Position" component mirrors the Cesium camera onto the small
  // OpenLayers overview map by calling window.map.setView() on every single
  // Cesium frame. That overwrites whatever the user drags, so the overview map
  // feels frozen. Pause the mirroring while the user handles the overview map,
  // and only resume once the 3D globe camera actually moves again.
  function patchOverviewMap() {
    const map = window.map
    const viewer = window.viewer
    if (!map || !viewer || typeof map.setView !== 'function' || map.__mainDragPatched) return
    const target = typeof map.getTargetElement === 'function' ? map.getTargetElement() : null
    if (!target) return

    const nativeSetView = map.setView.bind(map)
    let pointerActive = false
    let suppressed = false
    let startCenter = null
    let startZoom = null

    // Altitude table mirrored from the legacy bundle's altitude->zoom helper.
    // Indices are OpenLayers zoom levels; values are the matching Cesium camera
    // height in metres. Used to drive the 3D camera from the overview map.
    const ALTITUDE_BY_ZOOM = [
      3280274116421673e-8, 239673905342611e-7, 12109133282591399e-9,
      7544339942150029e-9, 4013980746760576e-9, 215309845872719e-8,
      1031519751307045e-9, 594367.296565025, 342677.25083245104,
      164632.97755610794, 76977.64690613124, 36875.262696890095,
      17669.269867698094, 10253.27377477659, 4913.00444094621,
      3400.8921034522014, 1958.669367726067, 758.7914964838986,
      525.1914307070838, 363.3647775209164
    ]
    function zoomToHeight(zoom) {
      const index = Math.min(Math.max(Math.round(zoom), 0), ALTITUDE_BY_ZOOM.length - 1)
      return ALTITUDE_BY_ZOOM[index]
    }

    function cameraKey() {
      const c = viewer.camera && viewer.camera.positionCartographic
      if (!c) return ''
      return `${c.longitude.toFixed(6)},${c.latitude.toFixed(6)},${Math.round(c.height)}`
    }
    let lastKey = cameraKey()

    // Reverse of the legacy camera->map mirror: place the 3D camera straight
    // down over the overview map's current centre at the matching altitude.
    const cesium = window.Cesium
    function syncGlobeToMap() {
      if (!cesium || !cesium.Cartesian3 || !cesium.Math) return
      const view = map.getView()
      const center = view.getCenter()
      if (!center) return
      viewer.camera.setView({
        destination: cesium.Cartesian3.fromDegrees(center[0], center[1], zoomToHeight(view.getZoom())),
        orientation: {
          heading: 0,
          pitch: cesium.Math.toRadians(-90),
          roll: 0
        }
      })
    }

    map.setView = function (...args) {
      if (pointerActive || suppressed) return
      return nativeSetView(...args)
    }

    target.addEventListener('pointerdown', () => {
      const view = map.getView()
      pointerActive = true
      startCenter = view.getCenter()
      startZoom = view.getZoom()
    }, true)

    // Live feedback while the user drags the overview map.
    map.on('pointerdrag', () => {
      if (!pointerActive) return
      syncGlobeToMap()
    })

    window.addEventListener('pointerup', () => {
      if (!pointerActive) return
      pointerActive = false
      const view = map.getView()
      const c = view.getCenter()
      const moved = view.getZoom() !== startZoom
        || !startCenter || c[0] !== startCenter[0] || c[1] !== startCenter[1]
      if (moved) {
        syncGlobeToMap()
        suppressed = true
        lastKey = cameraKey()
      }
    }, true)

    viewer.scene.preRender.addEventListener(() => {
      if (!suppressed) return
      const key = cameraKey()
      if (key !== lastKey) {
        suppressed = false
        lastKey = key
      }
    })

    map.__mainDragPatched = true
  }

  // The bundled track uses 5px points and 2-3px lines, which is hard to read on
  // large screens. Enlarge the track entities after the bundle creates them.
  function patchTrackStyle() {
    const viewer = window.viewer
    const cesium = window.Cesium
    if (!viewer || !cesium) return

    function readProperty(property) {
      if (property && typeof property.getValue === 'function') return property.getValue()
      return property
    }

    function resizeEntity(entity, isDataSource) {
      if (!isDataSource) {
        // Only the bundle's own history line; the parent page sizes its overlays itself.
        const name = typeof entity.name === 'string' ? entity.name : ''
        const id = typeof entity.id === 'string' ? entity.id : ''
        if (entity.polyline && (name.startsWith('TyphoonLine-') || id.startsWith('TyphoonLine-'))) {
          const target = 6
          if (readProperty(entity.polyline.width) !== target) entity.polyline.width = target
        }
        return
      }
      if (entity.point) {
        const target = 12
        if (readProperty(entity.point.pixelSize) !== target) entity.point.pixelSize = target
      }
      if (entity.polyline && entity.polyline.material && typeof entity.polyline.material.getValue === 'function') {
        const isForecastLine = entity.polyline.material.getValue() instanceof cesium.PolylineDashMaterialProperty
        const target = isForecastLine ? 4 : 0
        if (target && readProperty(entity.polyline.width) !== target) entity.polyline.width = target
      }
    }

    if (viewer.entities) viewer.entities.values.forEach((entity) => resizeEntity(entity, false))
    const sources = viewer.dataSources
    if (sources) {
      for (let i = 0; i < sources.length; i += 1) {
        sources.get(i).entities.values.forEach((entity) => resizeEntity(entity, true))
      }
    }
  }

  nativeSetInterval(patchViewerCamera, 50)
  nativeSetInterval(patchOverviewMap, 100)
  nativeSetInterval(patchTrackStyle, 200)
})()
