/* Prints the first lrand48() of a fresh process (no srand48). Compare with
 * rand48_check output: BSD/bionic default state vs glibc zero state. */
#define _DEFAULT_SOURCE
#include <stdio.h>
#include <stdlib.h>
int main(void) { printf("%ld\n", lrand48()); return 0; }
