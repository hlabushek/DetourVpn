allprojects { repositories { google(); mavenCentral() } }
rootProject.layout.buildDirectory.set(file("../build"))
subprojects { layout.buildDirectory.set(rootProject.layout.buildDirectory.dir(name)) }
// The pinned Flutter integration_test plugin requests runner/rules 1.2+ dynamically.
// Reuse the app's locked versions for this test-only project, including offline builds.
subprojects {
    if (name == "integration_test") {
        configurations.configureEach {
            resolutionStrategy.eachDependency {
                if (requested.group == "androidx.test") {
                    when (requested.name) {
                        "runner" -> useVersion("1.3.0")
                        "rules" -> useVersion("1.2.0")
                    }
                }
            }
        }
    }
}
// Flutter applies Kotlin to Android subprojects; keep its graph separate from
// the standalone I-011 harness, which shares only native sources/binaries.
project(":tunnel") {
    dependencyLocking {
        lockFile.set(rootProject.layout.projectDirectory.file("tunnel-gradle.lockfile"))
    }
}
tasks.register<Delete>("clean") { delete(rootProject.layout.buildDirectory) }
