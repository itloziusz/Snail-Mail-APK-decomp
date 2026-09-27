# Native code resolves these by name + signature (JAVA_RegisterFunctions,
# v7a:0x13ca4, table gJAVAFunction v7a:0x8b3f0) and calls the natives by
# their JNI symbol names; nothing in the shell may be renamed or removed.
-keep class com.sandlotgames.snailmail.** {
    native <methods>;
    public int JAVA*(...);
    public void JAVA*(...);
    public static long JTime;
}
-keepnames class com.sandlotgames.snailmail.**
