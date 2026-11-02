// swift-tools-version:6.2
import PackageDescription
let package = Package(
    name: "HerdrDesktop",
    platforms: [.macOS(.v13)],
    dependencies: [
        .package(url: "https://github.com/migueldeicaza/SwiftTerm",
                 revision: "a183c15b52858530a17de57dcc72b93eeebc9857")
    ],
    targets: [.executableTarget(name: "HerdrDesktop", dependencies: ["SwiftTerm"])]
)
