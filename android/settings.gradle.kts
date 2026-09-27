// Snail Mail arm64-v8a port: Android shell build.
// Versions are pinned here and in gradle/wrapper/gradle-wrapper.properties;
// see docs/BUILDING.md for the rationale and what could not be verified in
// the analysis environment (Google Maven / dl.google.com is blocked there).
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "SnailMailPort"
include(":app")
