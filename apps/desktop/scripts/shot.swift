// Screenshot the frontmost on-screen window of an app: `swift scripts/shot.swift <owner> <out.png>`.
// <owner> is the process name: "Studi0Trace" for a bundle, "studi0trace-desktop" under `npm run dev`.
import CoreGraphics
import Foundation

let args = CommandLine.arguments
guard args.count == 3 else { FileHandle.standardError.write("usage: shot.swift <owner> <out.png>\n".data(using: .utf8)!); exit(2) }
let info = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
guard let window = info.first(where: { ($0[kCGWindowOwnerName as String] as? String) == args[1] && ($0[kCGWindowLayer as String] as? Int) == 0 }),
      let id = window[kCGWindowNumber as String] as? Int else {
    FileHandle.standardError.write("no window of \(args[1]) on screen\n".data(using: .utf8)!)
    exit(1)
}
let p = Process()
p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
p.arguments = ["-x", "-o", "-l\(id)", args[2]]
try p.run()
p.waitUntilExit()
exit(p.terminationStatus)
