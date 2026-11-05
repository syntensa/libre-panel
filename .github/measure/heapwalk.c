/* MEASUREMENT (feature branch only): one heap walked in one call, its lock held only here.
 *
 * Walked from Python step by step (ctypes), the heap's lock was held while ctypes gave the GIL
 * up between the steps: another thread that held the GIL then waited for the lock in malloc,
 * and the walk for the GIL: the app stood still. Here nothing needs the GIL: the threads that
 * allocate meanwhile wait for the lock, and go on once the walk is done.
 *
 * gcc -shared -O2 -o heapwalk.dll heapwalk.c */
#include <string.h>
#include <windows.h>

typedef struct {
    unsigned long long start, end;
} Range;

typedef struct {
    unsigned long long busy, free, committed, uncommitted, blocks, regions, large;
} Totals;

/* The regions of `heap` (their address ranges, up to `room`), its blocks of 256 KB and more
 * (up to `room_large`), and what is busy and free in it. 0 where it could not be locked. */
__declspec(dllexport) int walk_heap(HANDLE heap, Range *regions, int room, Range *large,
                                    int room_large, Totals *totals) {
    PROCESS_HEAP_ENTRY entry;
    memset(&entry, 0, sizeof entry);
    memset(totals, 0, sizeof *totals);
    if (!HeapLock(heap))
        return 0;
    while (HeapWalk(heap, &entry)) {
        if (entry.wFlags & PROCESS_HEAP_REGION) {
            if (totals->regions < (unsigned long long)room) {
                regions[totals->regions].start = (ULONG_PTR)entry.lpData;
                regions[totals->regions].end = (ULONG_PTR)entry.lpData + entry.cbData;
            }
            totals->regions++;
            totals->committed += entry.Region.dwCommittedSize;
            totals->uncommitted += entry.Region.dwUnCommittedSize;
        } else if (entry.wFlags & PROCESS_HEAP_UNCOMMITTED_RANGE) {
            continue;
        } else if (entry.wFlags & PROCESS_HEAP_ENTRY_BUSY) {
            totals->busy += entry.cbData + entry.cbOverhead;
            totals->blocks++;
            if (entry.cbData >= 256 * 1024) {
                if (totals->large < (unsigned long long)room_large) {
                    large[totals->large].start = (ULONG_PTR)entry.lpData;
                    large[totals->large].end = (ULONG_PTR)entry.lpData + entry.cbData;
                }
                totals->large++;
            }
        } else {
            totals->free += entry.cbData;
        }
    }
    HeapUnlock(heap);
    return 1;
}
