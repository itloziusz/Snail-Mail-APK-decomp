/* snailmail_host entry point; the driver lives in main.c (sm_host_run). */
int sm_host_run(int argc, char **argv);

int main(int argc, char **argv) { return sm_host_run(argc, argv); }
