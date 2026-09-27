// Snail Mail arm64-v8a port -- Android application module.
//
// Invariants (docs/BUILDING.md, docs/PLATFORM_BOUNDARIES.md):
//  * namespace == original Java package com.sandlotgames.snailmail so every
//    JNI symbol keeps its original name (Java_com_sandlotgames_snailmail_*),
//    and the 26 Java callbacks the native code resolves by name/signature
//    (gJAVAFunction table, v7a:0x8b3f0) keep their names;
//  * applicationId differs from the original so the dev build never replaces
//    an installed original;
//  * arm64-v8a only; no 32-bit ABI, no ARM32 interpreter/translator;
//  * native code linked for 16 KB pages and stored uncompressed + aligned.
plugins {
    id("com.android.application")
}

val repoRoot: File = rootDir.parentFile
val assetsDir: String = providers.gradleProperty("snailmail.assetsDir").orNull
    ?: File(repoRoot, "work/apk_unzip/assets").path

android {
    namespace = "com.sandlotgames.snailmail"
    compileSdk = 35
    // NDK r27c. r27 does not default to 16 KB ELF alignment; the explicit
    // linker flag in src/main/cpp/CMakeLists.txt and the CMake argument
    // below make it so.
    ndkVersion = "27.2.12479018"

    defaultConfig {
        applicationId = "com.sandlotgames.snailmail.port"
        // 23: first level that loads uncompressed native libraries straight
        // from the APK (useLegacyPackaging=false) and rejects text relocations.
        minSdk = 23
        targetSdk = 35
        versionCode = 1
        versionName = "1.00-port-dev"

        ndk {
            abiFilters.clear()
            abiFilters += "arm64-v8a"
        }
        externalNativeBuild {
            cmake {
                arguments += listOf(
                    "-DANDROID_STL=c++_static",
                    "-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON",
                )
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    packaging {
        jniLibs {
            // stored uncompressed and page aligned inside the APK
            useLegacyPackaging = false
        }
    }

    androidResources {
        // SnailMailActivity opens asm.mp3 and ADRenderer opens *.ogg with
        // AssetManager.openFd(), which only works for STORED entries (the
        // original apktool.yml doNotCompress list also has mp3 and ogg).
        noCompress += listOf("mp3", "ogg")
    }

    sourceSets {
        getByName("main") {
            if (File(assetsDir).isDirectory) {
                assets.srcDir(assetsDir)
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildTypes {
        getByName("release") {
            // The native side looks up Java methods by name (GetMethodID);
            // keep rules in proguard-rules.pro protect them if minify is ever enabled.
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
}

// Opt-in post-build check: ./gradlew :app:verifyNativeAlignment
tasks.register<Exec>("verifyNativeAlignment") {
    description = "Checks the debug APK: arm64-v8a only, stored + 16 KB aligned .so, ELF64 PT_LOAD align >= 16 KB"
    dependsOn("assembleDebug")
    val apk = layout.buildDirectory.file("outputs/apk/debug/app-debug.apk")
    commandLine("python3", File(repoRoot, "tools/validation/platform/check_elf_alignment.py").path)
    argumentProviders.add(CommandLineArgumentProvider { listOf(apk.get().asFile.path) })
}
