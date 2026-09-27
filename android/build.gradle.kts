// Root build file: plugin versions only.
// AGP 8.13.0 requires Gradle >= 8.13 and JDK >= 17 (wrapper pins Gradle 8.14.3).
// NOT verified against maven.google.com from the analysis sandbox: every
// maven.google.com artifact 301-redirects to dl.google.com, which is blocked
// there (see docs/BUILDING.md, "Recorded build attempt").
plugins {
    id("com.android.application") version "8.13.0" apply false
}
