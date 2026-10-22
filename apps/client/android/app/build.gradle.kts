plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("dev.flutter.flutter-gradle-plugin")
}
android {
    namespace = "org.example.vpnclient"
    compileSdk = 36
    buildToolsVersion = "36.0.0"
    defaultConfig {
        applicationId = "org.example.vpnclient"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "0.1-i013"
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    // Compile the reviewed adapter and parser directly, without the I-011 UI.
    sourceSets["main"].java.apply {
        srcDir("../../../android/app/src/main/java")
        exclude("**/MainActivity.java", "**/PocApplication.java")
    }
    sourceSets["test"].java.srcDir("../../../android/app/src/test/java")
    packaging { jniLibs.useLegacyPackaging = false }
    buildTypes { release { isMinifyEnabled = false; isShrinkResources = false } }
}
flutter { source = "../.." }
kotlin { compilerOptions { jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17 } }
dependencies {
    implementation(project(":tunnel"))
    implementation("org.jetbrains.kotlin:kotlin-stdlib:2.2.20")
    testImplementation("junit:junit:4.13.2")
}
configurations.configureEach { resolutionStrategy.activateDependencyLocking() }
dependencyLocking {
    // Flutter selects engine ABI artifacts per device. Their exact versions are
    // pinned by the SDK engine revision; locking their presence breaks device builds.
    ignoredDependencies.add("io.flutter:*")
}
