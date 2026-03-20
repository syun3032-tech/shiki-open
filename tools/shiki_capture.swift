// shiki_capture.swift — 識ちゃん専用スクリーンキャプチャ
// ScreenCaptureKit使用（macOS 15+対応）
// 独立バイナリとしてScreen Recording権限を取得可能
// Usage: shiki_capture <output_path.png>
//   or:  shiki_capture --request <request_file>
//        request_file contains the output path (1 line)
//        result is written to <request_file>.done

import Cocoa
import ScreenCaptureKit

// --- 引数解析 ---
let args = CommandLine.arguments

var outputPath: String
var requestFile: String? = nil

if args.count >= 3 && args[1] == "--request" {
    // リクエストファイルモード（open経由で起動時に使用）
    requestFile = args[2]
    guard let content = try? String(contentsOfFile: requestFile!, encoding: .utf8) else {
        fputs("ERROR: Cannot read request file\n", stderr)
        exit(1)
    }
    outputPath = content.trimmingCharacters(in: .whitespacesAndNewlines)
} else if args.count >= 2 {
    outputPath = args[1]
} else {
    fputs("Usage: shiki_capture <output_path>\n", stderr)
    fputs("   or: shiki_capture --request <request_file>\n", stderr)
    exit(1)
}

// --- キャプチャ実行 ---
let semaphore = DispatchSemaphore(value: 0)
var exitCode: Int32 = 0

Task {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: true
        )

        guard let display = content.displays.first else {
            fputs("ERROR: No display found\n", stderr)
            exitCode = 2
            semaphore.signal()
            return
        }

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.width = display.width * 2   // Retina
        config.height = display.height * 2
        config.showsCursor = true
        config.captureResolution = .best

        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: config
        )

        let rep = NSBitmapImageRep(cgImage: image)
        guard let pngData = rep.representation(using: .png, properties: [:]) else {
            fputs("ERROR: PNG encoding failed\n", stderr)
            exitCode = 3
            semaphore.signal()
            return
        }

        try pngData.write(to: URL(fileURLWithPath: outputPath))

        // リクエストファイルモードなら完了マーカーを書く
        if let rf = requestFile {
            try "OK".write(toFile: rf + ".done", atomically: true, encoding: .utf8)
        }

        print("OK")
        exitCode = 0
    } catch {
        let errMsg = "ERROR: \(error.localizedDescription)"
        fputs(errMsg + "\n", stderr)

        // リクエストファイルモードならエラーも書く
        if let rf = requestFile {
            try? errMsg.write(toFile: rf + ".done", atomically: true, encoding: .utf8)
        }

        exitCode = 4
    }
    semaphore.signal()
}

let result = semaphore.wait(timeout: .now() + 10)
if result == .timedOut {
    fputs("ERROR: Capture timed out\n", stderr)
    if let rf = requestFile {
        try? "ERROR: timeout".write(toFile: rf + ".done", atomically: true, encoding: .utf8)
    }
    exit(5)
}
exit(exitCode)
