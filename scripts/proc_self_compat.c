/* Path-discovery compatibility for runtimes exposing /proc/self/exe but not
 * /proc/<own-pid>/exe. No proof-checking or other Lean operations are changed.
 * Only the current process's own executable link is redirected to its alias. */
#define _GNU_SOURCE
#include <unistd.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

ssize_t readlink(const char *path, char *buffer, size_t size) {
    static ssize_t (*original)(const char *, char *, size_t) = NULL;
    if (!original) original = dlsym(RTLD_NEXT, "readlink");
    char own_executable[80];
    snprintf(own_executable, sizeof(own_executable), "/proc/%d/exe", getpid());
    return original(strcmp(path, own_executable) == 0 ? "/proc/self/exe" : path,
                    buffer, size);
}
