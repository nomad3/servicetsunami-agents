// Apple Vision hand-pose landmarker exposed as a C-callable symbol so the
// Rust gesture engine can call it across FFI without swift-bridge.
//
// Symbol: `luna_extract_landmarks(rgb_bytes, width, height, out_buf,
//   out_confidence, out_handedness_left)`. Returns the number of hands
// written into the output buffers (0..=2).
//
// Output buffer layout:
//   - out_buf:           2 hands × 21 landmarks × 3 floats = 126 floats (x, y, z; z=0)
//   - out_confidence:    2 floats — observation confidence per hand
//   - out_handedness_left: 2 bytes — 1 if left hand, 0 if right
//
// Coordinates are normalized: x and y in [0, 1] image space (origin top-left
// after y-flip), z always 0.0 (Vision doesn't return depth).

import Foundation
import Vision
import CoreImage
import CoreGraphics

@_cdecl("luna_extract_landmarks")
public func luna_extract_landmarks(
    _ rgbBytes: UnsafePointer<UInt8>,
    _ width: Int32,
    _ height: Int32,
    _ outBuf: UnsafeMutablePointer<Float>,
    _ outConfidence: UnsafeMutablePointer<Float>,
    _ outHandednessLeft: UnsafeMutablePointer<UInt8>
) -> Int32 {
    let w = Int(width)
    let h = Int(height)
    if w <= 0 || h <= 0 { return 0 }

    let bufferSize = w * h * 3
    let data = Data(bytes: rgbBytes, count: bufferSize)

    guard let provider = CGDataProvider(data: data as CFData) else { return 0 }
    let colorSpace = CGColorSpaceCreateDeviceRGB()
    let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue)
    guard let cgImage = CGImage(
        width: w,
        height: h,
        bitsPerComponent: 8,
        bitsPerPixel: 24,
        bytesPerRow: w * 3,
        space: colorSpace,
        bitmapInfo: bitmapInfo,
        provider: provider,
        decode: nil,
        shouldInterpolate: false,
        intent: .defaultIntent
    ) else { return 0 }

    let request = VNDetectHumanHandPoseRequest()
    request.maximumHandCount = 2

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return 0
    }

    guard let observations = request.results else { return 0 }
    let order: [VNHumanHandPoseObservation.JointName] = [
        .wrist,
        .thumbCMC, .thumbMP, .thumbIP, .thumbTip,
        .indexMCP, .indexPIP, .indexDIP, .indexTip,
        .middleMCP, .middlePIP, .middleDIP, .middleTip,
        .ringMCP, .ringPIP, .ringDIP, .ringTip,
        .littleMCP, .littlePIP, .littleDIP, .littleTip
    ]

    var handsWritten: Int32 = 0
    for (idx, obs) in observations.prefix(2).enumerated() {
        guard let allPoints = try? obs.recognizedPoints(.all) else { continue }
        for (i, joint) in order.enumerated() {
            let base = (idx * 21 + i) * 3
            if let p = allPoints[joint], p.confidence > 0.3 {
                outBuf[base + 0] = Float(p.location.x)
                outBuf[base + 1] = Float(1.0 - p.location.y)  // flip y to image space
                outBuf[base + 2] = 0.0
            } else {
                outBuf[base + 0] = 0
                outBuf[base + 1] = 0
                outBuf[base + 2] = 0
            }
        }
        outConfidence[idx] = obs.confidence
        if #available(macOS 12.0, *) {
            outHandednessLeft[idx] = (obs.chirality == .left) ? 1 : 0
        } else {
            outHandednessLeft[idx] = 0  // chirality unavailable on older macOS; default right
        }
        handsWritten += 1
    }
    return handsWritten
}
