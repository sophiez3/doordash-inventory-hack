import { useEffect, useRef, useState } from 'react'
import jsQR from 'jsqr'

// Opens the camera and calls onScan(text) whenever it sees a QR code.
// The camera starts when this component appears and stops when it disappears.
export default function QrScanner({ onScan }) {
  const videoRef = useRef(null)
  const onScanRef = useRef(onScan)
  const [cameraError, setCameraError] = useState('')

  // Always call the newest onScan without restarting the camera.
  useEffect(() => {
    onScanRef.current = onScan
  })

  useEffect(() => {
    let stream = null
    let frame = 0
    let cancelled = false
    let lastCheck = 0
    const video = videoRef.current
    const canvas = document.createElement('canvas') // off-screen scratch pad

    const tick = (time) => {
      if (cancelled) return
      // Check about 6 times per second; that's plenty and saves battery.
      if (time - lastCheck > 160 && video.readyState === video.HAVE_ENOUGH_DATA) {
        lastCheck = time
        canvas.width = video.videoWidth
        canvas.height = video.videoHeight
        const ctx = canvas.getContext('2d', { willReadFrequently: true })
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height)
        const code = jsQR(pixels.data, pixels.width, pixels.height)
        if (code && code.data) onScanRef.current(code.data)
      }
      frame = requestAnimationFrame(tick)
    }

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        video.srcObject = stream
        await video.play()
        frame = requestAnimationFrame(tick)
      } catch {
        if (!cancelled) {
          setCameraError('Camera unavailable. Allow camera access, or enter the code manually below.')
        }
      }
    }
    start()

    // Cleanup: stop the camera light and the scanning loop.
    return () => {
      cancelled = true
      cancelAnimationFrame(frame)
      if (stream) stream.getTracks().forEach((t) => t.stop())
    }
  }, [])

  return (
    <div className="scanner">
      <video ref={videoRef} playsInline muted />
      <div className="scan-frame" />
      {cameraError && <div className="banner error small">{cameraError}</div>}
    </div>
  )
}
