/*
 *#==============================================================#
 *# File       :   PB X Delete (Turbo)
 *# Ctime      :   2025-10-16
 *# Mtime      :   2026-10-18
 *# Version    :   V107 (empty-run exit 3, abort dir cleanup, DIR_NOT_EMPTY retry)
 *# Author     :   www.linkleading.com
 *# Copyright (C) 2025-2026
 *#==============================================================#
 *
 * Change Log:
 * V107:
 * - 空跑/--list 打不开/零有效目标 → EXIT_CODE:3（不再假成功 0）
 * - 用户中止后仍尽力按 depth 清理空目录（best-effort）
 * - TryRemoveDir：DIR_NOT_EMPTY 短退避重试（POSIX 关句柄窗口）
 * V106:
 * - 平滑停止：CreateEventW(Local\\PB_XDelete_StopEvent)；不 ResetEvent（由 GUI 在 Popen 前复位）
 * V105:
 * - LoadPathW：路径不存在 WARN；ACCESS_DENIED 等计入失败并影响 EXIT_CODE:3
 * V104:
 * - 退出码：0=全成功；1=参数错误；2=用户中止；3=部分失败(Files_Fail/Dirs_Fail>0)
 * - 汇总末输出 EXIT_CODE:N，便于 GUI/脚本解析（协议头仍为 PB_XDELETE_PROTOCOL:1）
 * V103:
 * - 扫描：无界 LIFO ScanStack（DFS）消除 Scanner 满队列自锁；pushBatch 一次加锁
 * - FixPath：GetFullPathNameW 展开相对路径/正斜杠后再加 \\?\
 * - FileItem{dirIdx,name,size}：队列不再存完整路径+vol；Worker 按目录缓存复用父路径
 * - --list 大量单文件攒 BATCH 再入队；HDD 探测支持首目标为文件；去掉 DirNode::path_len
 * - TryRemoveDir：FDIX 优先（与 TryDeleteFile 一致）
 * V102:
 * - TryDelete/TryRemoveDir：SHARING_VIOLATION/NOT_FOUND 快失败，仅 ACCESS_DENIED 走 TakeOwnership/ACL
 * - GetVolumeRootKey：\\?\X:\ / UNC 字符串快路径，失败才 GetVolumePathNameW
 * - 目录阶段：ReconstructPathTo 无锁；O(N) 按 depth 分桶（去掉全量 sort）
 * - Small/Large Worker：按批 fetch_add(relaxed)；Dashboard join + g_dashboardDone；单文件路径入删除队列
 * V101:
 * - 每目录一次 GetVolumeRootKey，文件随 PathSize.vol 下传；FDIX 线程本地命中同卷则跳过 map 锁
 * - 枚举：FindFirstFileExW(FindExInfoBasic | FIND_FIRST_EX_LARGE_FETCH)
 * - 子目录：本目录内收集后 AddChildDirsBatch 一次加锁批量入树，再入 scanQueue
 * V100:
 * - Scanner：每目录批量 fetch_add(g_totalFilesFound)，降低海量小文件下原子竞争（对齐 Czkawka increase_items 批加）
 * - 线程：--threads N / 环境变量 PB_XDELETE_THREADS；SSD 上 Scanner 提高到最多 4（HDD 仍强制 1）
 * - 扫描：FindFirstFileW 失败输出 WARN（上限 50），便于权限/路径排查
 * V99:
 * - FDIX 路径：CreateFileW 成功后 SetFileInformationByHandle(FileIoPriorityHintInformation, IoPriorityHigh)，与线程级 I/O 优先级互补（对齐 pb_xflow VFS_Local_Open）
 * V40.15:
 * - 首个目标卷 IOCTL_STORAGE_QUERY_PROPERTY(SeekPenalty)：机械盘降低 Scanner/Small/Large/DirPool 并发，减轻寻道抖动
 * - Scanner/Small/Large/DirWorker 线程 SetThreadInformation(ThreadIoPriority) 提高 I/O 优先级（内核可用时）
 * V40.14:
 * - Dashboard: newline-terminated [File]/[Dirs] line when stdout is not a console (pipe/redirect);
 *   avoids merged lines like "[File]...PROGRESS:SCANNING:..." in GUI logs / readline-style readers
 * V40.13:
 * - argc<2: wcerr usage; stdout: PB_XDELETE_PROTOCOL:1 (GUI/脚本可校验)
 * V40.12:
 * - g_allDirs: parent-index + final segment (DirNode) replaces full-path vector; scan queue carries node index
 * - ReconstructPathTo for enumeration/delete; depth/path_len stored at insert for sort (no full string compare)
 * V40.11:
 * - FDIX: per-volume cache (GetVolumePathNameW key) instead of global g_useFileDispositionEx (FAT U盘不再拖垮 NTFS)
 * - TryRemoveDir: FileDispositionInfoEx on empty dirs (same flags as files)
 * - Dir phase: condition_variable wait replaces sleep(1ms) spin on pending
 * V40.10:
 * - g_dirCount = g_allDirs.size() under g_dirListMtx (no drift); dir cleanup: atomic index dispatch (no per-layer queue)
 * - fullPath/searchPath reserve(32768); depth sort keeps length tie-break
 * V40.9:
 * - Cached Administrators SID (TakeOwnership/GrantDacl); DeleteFileEx saves LastError before CloseHandle
 * - atomic g_dirCount + queue approximate sizes; Dashboard uses lock for dir total / thread pool for dir phase
 * V40.8:
 * - g_scanQueue 500000 / QUEUE_LIMIT_LARGE 100000 (fixes scanner deadlock when ScanQ stuck at limit)
 * V40.7:
 * - QUEUE_LIMIT_LARGE = 20000 (prevents head-of-line blocking)
 * V40.6:
 * - EnablePrivilege: use L"..." literals (avoids UNICODE macro trap)
 * - Progress: include dirs in total_items (fixes 100% stall during DirCleaner)
 * V40.5:
 * - LoadPath: lock g_dirListMtx when pushing to g_allDirs (fixes heap corruption race)
 * - EnablePrivilege(SE_TAKE_OWNERSHIP_NAME|SE_RESTORE_NAME) at startup
 * V40.4:
 * - HIGH_PRIORITY_CLASS (was REALTIME, prevents system freeze)
 * - g_totalBytesFreed + Bytes_Freed in SUMMARY
 * V40.3:
 * - TryRemoveDir: skip TakeOwnership/GrantDacl when ERROR_DIR_NOT_EMPTY (avoids ACL overhead)
 * V40.2:
 * - ScopedActiveGuard: RAII decrement on all exit paths (fixes counter leak when FindFirstFileW fails)
 * V40.1:
 * - GrantDaclFullControl: native SetEntriesInAclW (no cmd.exe process bomb)
 * - g_scanComplete + PROGRESS:SCANNING during scan (fixes yo-yo progress bar)
 * V40.0:
 * - Atomic active count: onPopIncrement inside BlockingQueue::pop (closes pre-emption gap)
 * V39.9:
 * - Reparse point guard: do not traverse junctions/symlinks (prevents OOB delete + infinite loop)
 * - Icacls fallback: after TakeOwnership, grant Administrators:F if delete still fails
 * - Dynamic large workers: 2 threads when cores > 6
 * V39.8: Manifest mode (--list)
 * V39.7: Scan race fix, progress, zombie fix
 * V39.6: Deadlock fix, dir depth-order, TakeOwnership, FAILED_PATHS
 * V39.5: Dynamic threading, Dirs_Fail, TakeOwnership
 * V39.4: FileDispositionInfoEx, unique_ptr, PROGRESS, graceful stop
 */

#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <iostream>
#include <vector>
#include <string>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <queue>
#include <atomic>
#include <chrono>
#include <algorithm>
#include <memory>
#include <fstream>
#include <filesystem>
#include <functional>
#include <unordered_map>
#include <limits>
#include <numeric>
#include <cwchar>
#include <aclapi.h>
#include <sddl.h>
#include <deque>
#ifdef _MSC_VER
#pragma comment(lib, "advapi32")
#endif
#include <winioctl.h>

/* MinGW 旧头文件可能缺 SeekPenalty 描述符（与 MSDN 一致） */
#ifndef STORAGE_DEVICE_SEEK_PENALTY_DESCRIPTOR
typedef struct _STORAGE_DEVICE_SEEK_PENALTY_DESCRIPTOR {
    ULONG Version;
    ULONG Size;
    BOOLEAN IncursSeekPenalty;
    BOOLEAN Reserved[3];
} STORAGE_DEVICE_SEEK_PENALTY_DESCRIPTOR, *PSTORAGE_DEVICE_SEEK_PENALTY_DESCRIPTOR;
#endif

/*
 * Fast Delete V39.5 - "Micro-Surgical" (Turbo Parallel Scan)
 * * IOPS MAXIMIZATION + ROBUSTNESS *
 *
 * V39.5:
 * - Parallel Work-Stealing Scanner: Multi-threaded directory traversal.
 * - Memory Optimized Directory List: Stable sort (Depth + Length).
 * - TakeOwnership Fallback: Attempt ACL bypass via SetNamedSecurityInfoW.
 * - Dynamic Threading: Scales based on hardware_concurrency.
 */

using namespace std::chrono;

// Graceful stop: GUI sets this event to request engine stop (Local\ = same session, no SeCreateGlobalPrivilege)
#define STOP_EVENT_NAME L"Local\\PB_XDelete_StopEvent"
#ifndef ERROR_DIR_NOT_EMPTY
#define ERROR_DIR_NOT_EMPTY 145
#endif

// FileDispositionInfoEx (Win10+): for better concurrency, POSIX semantics
// Value 21 = FileDispositionInfoEx in FILE_INFO_BY_HANDLE_CLASS (minwinbase.h)
#define FDIX_CLASS 21
#ifndef FILE_DISPOSITION_DELETE
#define FILE_DISPOSITION_DELETE 0x00000001
#endif
#ifndef FILE_DISPOSITION_POSIX_SEMANTICS
#define FILE_DISPOSITION_POSIX_SEMANTICS 0x00000002
#endif
#ifndef FILE_DISPOSITION_IGNORE_READONLY_ATTRIBUTE
#define FILE_DISPOSITION_IGNORE_READONLY_ATTRIBUTE 0x00000010
#endif
typedef struct _MY_FILE_DISPOSITION_INFO_EX {
    DWORD Flags;
} MY_FILE_DISPOSITION_INFO_EX;

// ==========================================
// Global Configuration & Stats
// ==========================================
std::atomic<long long> g_filesDeleted(0);
std::atomic<long long> g_filesFailed(0);
std::atomic<long long> g_dirsDeleted(0);
std::atomic<long long> g_dirsFailed(0);
std::atomic<long long> g_totalFilesFound(0);
std::atomic<long long> g_totalBytesFreed(0);
std::atomic<bool> g_isProcessingDirs(false);
std::atomic<bool> g_scanComplete(false);
std::atomic<bool> g_stopRequested(false);
std::atomic<bool> g_dashboardDone(false);
std::mutex g_dashMtx;
std::condition_variable g_dashCv;
std::atomic<int> g_activeScanners(0);
/* FDIX 按卷缓存：false=该卷不再尝试 SetFileInformationByHandle(FDIX)，避免 FAT 等卷一次失败拖垮全局 */
std::mutex g_fdxVolumeMtx;
std::unordered_map<std::wstring, bool> g_fdxVolumeOk;
std::mutex g_failedPaths_mtx;
std::vector<std::wstring> g_failedPaths;
const size_t MAX_FAILED_PATHS = 500;
std::atomic<int> g_scanWarnCount(0);
const int MAX_SCAN_WARNS = 50;

std::mutex g_dirListMtx;
/* 根节点 parent==DIR_PARENT_NONE 且 name 为完整路径；子节点仅末段名，删除/枚举时沿 parent 链还原 */
constexpr size_t DIR_PARENT_NONE = (std::numeric_limits<size_t>::max)();
struct DirNode {
    size_t parent;
    std::wstring name;
    size_t depth; /* '\\' 个数 */
};
std::deque<DirNode> g_allDirNodes;
std::atomic<size_t> g_dirCount(0); /* store(g_allDirNodes.size()) 与 push 同锁 */
std::mutex g_consoleMtx;

static size_t AddRootDir(const std::wstring& fullPath) {
    std::lock_guard<std::mutex> lock(g_dirListMtx);
    DirNode n;
    n.parent = DIR_PARENT_NONE;
    n.name = fullPath;
    n.depth = std::count(fullPath.begin(), fullPath.end(), L'\\');
    g_allDirNodes.push_back(std::move(n));
    g_dirCount.store(g_allDirNodes.size(), std::memory_order_relaxed);
    return g_allDirNodes.size() - 1;
}

/** 同一父目录下批量插入；一次持锁。outIdx 与 segs 等长 */
static void AddChildDirsBatch(size_t parentIdx,
                              const std::vector<std::wstring>& segs,
                              std::vector<size_t>& outIdx) {
    outIdx.clear();
    if (segs.empty()) return;
    outIdx.reserve(segs.size());
    std::lock_guard<std::mutex> lock(g_dirListMtx);
    const size_t p_depth = g_allDirNodes[parentIdx].depth;
    for (size_t i = 0; i < segs.size(); ++i) {
        DirNode n;
        n.parent = parentIdx;
        n.name = segs[i];
        n.depth = p_depth + 1;
        g_allDirNodes.push_back(std::move(n));
        outIdx.push_back(g_allDirNodes.size() - 1);
    }
    g_dirCount.store(g_allDirNodes.size(), std::memory_order_relaxed);
}

static thread_local std::vector<const std::wstring*> tls_dirChain;

/** 自叶子向根取链，再拼接为完整 \\?\ 路径。
 *  needLock=true：扫描期与写树并发；扫描结束后目录树只读可传 false。 */
static void ReconstructPathTo(size_t idx, std::wstring& out, bool needLock = true) {
    tls_dirChain.clear();
    tls_dirChain.reserve(64);
    auto walk = [&]() {
        for (size_t i = idx;;) {
            const DirNode& n = g_allDirNodes[i];
            tls_dirChain.push_back(&n.name);
            if (n.parent == DIR_PARENT_NONE) break;
            i = n.parent;
        }
    };
    if (needLock) {
        std::lock_guard<std::mutex> lk(g_dirListMtx);
        walk();
    } else {
        walk();
    }
    if (tls_dirChain.empty()) {
        out.clear();
        return;
    }
    out.assign(*tls_dirChain.back());
    for (int k = (int)tls_dirChain.size() - 2; k >= 0; --k) {
        out.push_back(L'\\');
        out.append(*tls_dirChain[k]);
    }
}

static PSID g_AdministratorsSid = NULL;
static bool EnsureAdministratorsSid(void) {
  if (g_AdministratorsSid)
    return true;
  SID_IDENTIFIER_AUTHORITY NtAuthority = SECURITY_NT_AUTHORITY;
  return AllocateAndInitializeSid(&NtAuthority, 2, SECURITY_BUILTIN_DOMAIN_RID,
          DOMAIN_ALIAS_RID_ADMINS, 0, 0, 0, 0, 0, 0, &g_AdministratorsSid) != FALSE;
}
/* 独立 .exe 退出时 OS 回收进程；若将来作 DLL 热加载，需显式 FreeSid/或模块级 Cleanup */

bool EnablePrivilege(LPCWSTR lpszPrivilege) {
    HANDLE hToken;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &hToken))
        return false;
    LUID luid;
    if (!LookupPrivilegeValueW(NULL, lpszPrivilege, &luid)) {
        CloseHandle(hToken);
        return false;
    }
    TOKEN_PRIVILEGES tp = { 0 };
    tp.PrivilegeCount = 1;
    tp.Privileges[0].Luid = luid;
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    BOOL res = AdjustTokenPrivileges(hToken, FALSE, &tp, sizeof(tp), NULL, NULL);
    CloseHandle(hToken);
    return res != FALSE;
}
HANDLE g_stopEvent = NULL;

const long long LARGE_FILE_THRESHOLD = 20 * 1024 * 1024; // 20MB
const size_t BATCH_SIZE_SMALL = 128;
const size_t QUEUE_LIMIT_SMALL = 512;
const size_t QUEUE_LIMIT_LARGE = 100000; // [核心修复] 放大以防大文件队列阻塞导致扫描器停工

// ==========================================
// Advanced Task Queues
// ==========================================
template <typename T>
class BlockingQueue {
private:
    std::queue<T> queue;
    std::mutex mtx;
    std::condition_variable cv_prod;
    std::condition_variable cv_cons;
    bool done;
    size_t limit;
    std::atomic<int>* onPopIncrement = nullptr;
    std::atomic<size_t> len_approx{0};

public:
    BlockingQueue(size_t l) : done(false), limit(l) {}
    void setOnPopIncrement(std::atomic<int>* p) { onPopIncrement = p; }

    void push(T item) {
        std::unique_lock<std::mutex> lock(mtx);
        cv_prod.wait(lock, [this] { return queue.size() < limit || done; });
        if (done) return;
        queue.push(std::move(item));
        len_approx.store(queue.size(), std::memory_order_relaxed);
        cv_cons.notify_one();
    }

    bool pop(T& item) {
        std::unique_lock<std::mutex> lock(mtx);
        cv_cons.wait(lock, [this] { return !queue.empty() || done; });
        if (queue.empty() && done) return false;
        item = std::move(queue.front());
        queue.pop();
        len_approx.store(queue.size(), std::memory_order_relaxed);
        if (onPopIncrement) (*onPopIncrement)++;
        cv_prod.notify_one();
        return true;
    }

    void setDone() {
        {
            std::lock_guard<std::mutex> lock(mtx);
            done = true;
        }
        cv_cons.notify_all();
        cv_prod.notify_all();
    }

    size_t size() {
        std::lock_guard<std::mutex> lock(mtx);
        return queue.size();
    }
    size_t size_approx() const { return len_approx.load(std::memory_order_relaxed); }
};

/* 无界 LIFO 扫描栈：消除 Scanner 自产自销满队列死锁；DFS 降低峰值并利好 NTFS 缓存 */
class ScanStack {
private:
    std::vector<size_t> stack;
    std::mutex mtx;
    std::condition_variable cv;
    bool done = false;
    std::atomic<int>* onPopIncrement = nullptr;
    std::atomic<size_t> len_approx{0};

public:
    ScanStack() { stack.reserve(4096); }
    void setOnPopIncrement(std::atomic<int>* p) { onPopIncrement = p; }

    void push(size_t item) {
        {
            std::lock_guard<std::mutex> lock(mtx);
            if (done) return;
            stack.push_back(item);
            len_approx.store(stack.size(), std::memory_order_relaxed);
        }
        cv.notify_one();
    }

    void pushBatch(const std::vector<size_t>& items, const std::vector<char>& mask) {
        size_t added = 0;
        {
            std::lock_guard<std::mutex> lock(mtx);
            if (done) return;
            for (size_t i = items.size(); i > 0; --i) {
                if (mask[i - 1]) {
                    stack.push_back(items[i - 1]);
                    added++;
                }
            }
            if (added > 0)
                len_approx.store(stack.size(), std::memory_order_relaxed);
        }
        if (added == 1) cv.notify_one();
        else if (added > 1) cv.notify_all();
    }

    bool pop(size_t& item) {
        std::unique_lock<std::mutex> lock(mtx);
        cv.wait(lock, [this] { return !stack.empty() || done; });
        if (stack.empty() && done) return false;
        item = stack.back();
        stack.pop_back();
        len_approx.store(stack.size(), std::memory_order_relaxed);
        if (onPopIncrement) (*onPopIncrement)++;
        return true;
    }

    void setDone() {
        {
            std::lock_guard<std::mutex> lock(mtx);
            done = true;
        }
        cv.notify_all();
    }

    size_t size() {
        std::lock_guard<std::mutex> lock(mtx);
        return stack.size();
    }
    size_t size_approx() const { return len_approx.load(std::memory_order_relaxed); }
};

struct FileItem {
    size_t dirIdx = DIR_PARENT_NONE; /* DIR_PARENT_NONE：name 为完整路径（单文件） */
    std::wstring name;               /* 目录内仅文件名，利于 SSO */
    long long size = 0;
};
using BatchPtr = std::unique_ptr<std::vector<FileItem>>;
BlockingQueue<BatchPtr> g_smallQueue(QUEUE_LIMIT_SMALL);
BlockingQueue<FileItem> g_largeQueue(QUEUE_LIMIT_LARGE);
ScanStack g_scanQueue;

#ifndef FIND_FIRST_EX_LARGE_FETCH
#define FIND_FIRST_EX_LARGE_FETCH 0x00000002
#endif

// ==========================================
// Security & IO Helpers
// ==========================================
inline std::wstring FixPath(const std::wstring& path) {
    if (path.empty()) return L"";
    if (path.rfind(L"\\\\?\\", 0) == 0) return path;

    std::wstring absPath = path;
    DWORD needed = GetFullPathNameW(path.c_str(), 0, NULL, NULL);
    if (needed > 0) {
        std::wstring buf(needed, L'\0');
        DWORD written = GetFullPathNameW(path.c_str(), needed, &buf[0], NULL);
        if (written > 0 && written < needed) {
            buf.resize(written);
            absPath = std::move(buf);
        }
    }
    while (absPath.size() > 3 && (absPath.back() == L'\\' || absPath.back() == L'/')) {
        if (absPath.size() == 3 && absPath[1] == L':') break;
        absPath.pop_back();
    }

    if (absPath.rfind(L"\\\\?\\", 0) == 0) return absPath;
    if (absPath.size() >= 2 && absPath[0] == L'\\' && absPath[1] == L'\\')
        return L"\\\\?\\UNC\\" + absPath.substr(2);
    return L"\\\\?\\" + absPath;
}

/** 删除类 Worker：提高 I/O 优先级（Win8+）；失败则忽略 */
static void ApplyThreadIoPriorityHigh() {
    typedef BOOL(WINAPI * PFN_SetThreadInformation)(HANDLE, int, PVOID, DWORD);
    static PFN_SetThreadInformation fn = (PFN_SetThreadInformation)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "SetThreadInformation");
    if (!fn) return;
    enum { kThreadIoPriority = 4 }; /* THREAD_INFORMATION_CLASS */
    enum IoPriorityHint_ { IoPriorityHigh_ = 3 };
#pragma pack(push, 1)
    struct ThreadIoPriority_ { ULONG IoPriority; };
#pragma pack(pop)
    ThreadIoPriority_ tip = {};
    tip.IoPriority = IoPriorityHigh_;
    (void)fn(GetCurrentThread(), kThreadIoPriority, &tip, sizeof(tip));
}

/* Vista+ 句柄级 I/O 优先级（winnt 类 50）；MinGW 旧头可能无宏 */
#ifndef FileIoPriorityHintInformation
#define FileIoPriorityHintInformation ((FILE_INFO_BY_HANDLE_CLASS)50)
#endif

/** FDIX 等已打开句柄：提高存储栈调度优先级（失败则忽略） */
static void ApplyFileHandleIoPriorityHigh(HANDLE h) {
    if (!h || h == INVALID_HANDLE_VALUE) return;
    /* IO_PRIORITY_HINT IoPriorityHigh = 3 */
    typedef struct {
        ULONG PriorityHint;
    } FILE_IO_PRIORITY_HINT_INFO_LOCAL;
    FILE_IO_PRIORITY_HINT_INFO_LOCAL hint = { 3 };
    (void)SetFileInformationByHandle(h, FileIoPriorityHintInformation, &hint, sizeof(hint));
}

/**
 * 若卷报告 IncursSeekPenalty（典型机械盘），返回 true；UNC/失败则 false（保持高并发策略）。
 * PropertyId 7 = StorageDeviceSeekPenaltyProperty (Win8+)。
 */
static bool VolumeHasSeekPenalty(const std::wstring& path) {
    if (path.rfind(L"\\\\?\\UNC\\", 0) == 0) return false;
    wchar_t volPath[MAX_PATH * 4];
    if (!GetVolumePathNameW(path.c_str(), volPath, (DWORD)(sizeof(volPath) / sizeof(volPath[0]))))
        return false;
    std::wstring vol = volPath;
    while (!vol.empty() && (vol.back() == L'\\' || vol.back() == L'/'))
        vol.pop_back();
    if (vol.empty()) return false;
    HANDLE h = CreateFileW(vol.c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
    if (h == INVALID_HANDLE_VALUE) return false;

    STORAGE_PROPERTY_QUERY query = {};
    query.PropertyId = (STORAGE_PROPERTY_ID)7;
    query.QueryType = PropertyStandardQuery;

    STORAGE_DEVICE_SEEK_PENALTY_DESCRIPTOR seek = {};
    DWORD br = 0;
    BOOL ok = DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, &query, sizeof(query),
                              &seek, sizeof(seek), &br, NULL);
    CloseHandle(h);
    if (!ok || br < sizeof(STORAGE_DEVICE_SEEK_PENALTY_DESCRIPTOR)) return false;
    return seek.IncursSeekPenalty != FALSE;
}

/** 解析正整数线程覆盖：CLI --threads 优先于环境变量 PB_XDELETE_THREADS；0=自动 */
static int ParsePositiveIntW(const wchar_t* s) {
    if (!s || !*s) return 0;
    wchar_t* end = nullptr;
    unsigned long v = wcstoul(s, &end, 10);
    if (end == s || v == 0 || v > 256) return 0;
    return (int)v;
}

static int ReadThreadsFromEnv() {
    wchar_t buf[32];
    DWORD n = GetEnvironmentVariableW(L"PB_XDELETE_THREADS", buf, (DWORD)(sizeof(buf) / sizeof(buf[0])));
    if (n == 0 || n >= (DWORD)(sizeof(buf) / sizeof(buf[0]))) return 0;
    return ParsePositiveIntW(buf);
}

struct CliOptions {
    int threadOverride = 0; /* 0=auto */
    bool listMode = false;
    std::wstring listFile;
    std::vector<std::wstring> roots;
    bool ok = true;
    const wchar_t* err = nullptr;
};

static CliOptions ParseCli(int argc, wchar_t* argv[]) {
    CliOptions o;
    o.threadOverride = ReadThreadsFromEnv();
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        if (a == L"--threads") {
            if (i + 1 >= argc) {
                o.ok = false;
                o.err = L"--threads requires a positive integer";
                return o;
            }
            int n = ParsePositiveIntW(argv[++i]);
            if (n <= 0) {
                o.ok = false;
                o.err = L"--threads value must be 1..256";
                return o;
            }
            o.threadOverride = n;
        } else if (a == L"--list") {
            if (i + 1 >= argc) {
                o.ok = false;
                o.err = L"--list requires a file path";
                return o;
            }
            o.listMode = true;
            o.listFile = argv[++i];
        } else if (!a.empty() && a[0] == L'-') {
            o.ok = false;
            o.err = L"unknown option (supported: --threads N, --list <file>)";
            return o;
        } else {
            o.roots.push_back(std::move(a));
        }
    }
    if (o.listMode && !o.roots.empty()) {
        o.ok = false;
        o.err = L"do not mix --list with positional paths";
        return o;
    }
    if (!o.listMode && o.roots.empty()) {
        o.ok = false;
        o.err = L"no target paths";
        return o;
    }
    return o;
}

/** 在创建线程前探测介质：取清单/参数中第一条有效路径 */
static std::wstring PeekFirstRoot(const CliOptions& opt) {
    if (opt.listMode) {
        std::ifstream f{std::filesystem::path(opt.listFile)};
        if (!f.is_open()) return L"";
        std::string line;
        while (std::getline(f, line)) {
            size_t s = line.find_first_not_of(" \t\r\n");
            if (s == std::string::npos) continue;
            size_t e = line.find_last_not_of(" \t\r\n");
            line = line.substr(s, e - s + 1);
            if (line.empty()) continue;
            if (line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB && (unsigned char)line[2] == 0xBF)
                line = line.substr(3);
            int len = MultiByteToWideChar(CP_UTF8, 0, line.c_str(), -1, NULL, 0);
            if (len > 0) {
                std::vector<wchar_t> wbuf((size_t)len);
                MultiByteToWideChar(CP_UTF8, 0, line.c_str(), -1, &wbuf[0], len);
                return std::wstring(&wbuf[0]);
            }
        }
        return L"";
    }
    return opt.roots.empty() ? L"" : opt.roots[0];
}

static void EmitScanWarn(const std::wstring& dirPath, DWORD err) {
    int n = g_scanWarnCount.fetch_add(1, std::memory_order_relaxed);
    if (n >= MAX_SCAN_WARNS) return;
    int len = WideCharToMultiByte(CP_UTF8, 0, dirPath.c_str(), -1, NULL, 0, NULL, NULL);
    std::lock_guard<std::mutex> lock(g_consoleMtx);
    if (len > 0) {
        std::vector<char> buf((size_t)len);
        WideCharToMultiByte(CP_UTF8, 0, dirPath.c_str(), -1, &buf[0], len, NULL, NULL);
        std::cout << "WARN:SCAN_OPEN_FAIL err=" << err << " path=" << &buf[0] << std::endl;
    } else {
        std::cout << "WARN:SCAN_OPEN_FAIL err=" << err << std::endl;
    }
    if (n + 1 == MAX_SCAN_WARNS)
        std::cout << "WARN:SCAN_OPEN_FAIL further warnings suppressed" << std::endl;
}

bool TakeOwnership(const std::wstring& path) {
    if (!EnsureAdministratorsSid())
        return false;
    DWORD res = SetNamedSecurityInfoW((LPWSTR)path.c_str(), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                      g_AdministratorsSid, NULL, NULL, NULL);
    return res == ERROR_SUCCESS;
}

bool GrantDaclFullControl(const std::wstring& path) {
    if (!EnsureAdministratorsSid())
        return false;
    EXPLICIT_ACCESS_W ea = { 0 };
    ea.grfAccessPermissions = GENERIC_ALL;
    ea.grfAccessMode = GRANT_ACCESS;
    ea.grfInheritance = NO_INHERITANCE;
    ea.Trustee.TrusteeForm = TRUSTEE_IS_SID;
    ea.Trustee.TrusteeType = TRUSTEE_IS_WELL_KNOWN_GROUP;
    ea.Trustee.ptstrName = (LPWSTR)g_AdministratorsSid;
    PACL pNewDacl = NULL;
    DWORD err = SetEntriesInAclW(1, &ea, NULL, &pNewDacl);
    if (err != ERROR_SUCCESS || !pNewDacl) return false;
    err = SetNamedSecurityInfoW((LPWSTR)path.c_str(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, NULL, NULL, pNewDacl, NULL);
    LocalFree(pNewDacl);
    return err == ERROR_SUCCESS;
}

/** 用于 FDIX 缓存键：优先字符串截取 \\?\X:\ / UNC，避免每路径 GetVolumePathNameW */
static std::wstring GetVolumeRootKey(const std::wstring& path) {
    if (path.empty()) return L"";
    /* \\?\UNC\server\share\... */
    if (path.size() >= 8 && path.compare(0, 8, L"\\\\?\\UNC\\") == 0) {
        size_t a = path.find(L'\\', 8);
        if (a == std::wstring::npos) return path;
        size_t b = path.find(L'\\', a + 1);
        if (b == std::wstring::npos) return path;
        return path.substr(0, b + 1);
    }
    /* \\?\C:\... */
    if (path.size() >= 6 && path.compare(0, 4, L"\\\\?\\") == 0 && path[5] == L':') {
        wchar_t d = path[4];
        if ((d >= L'A' && d <= L'Z') || (d >= L'a' && d <= L'z')) {
            if (path.size() >= 7 && path[6] == L'\\')
                return path.substr(0, 7);
            return path.substr(0, 6) + L"\\";
        }
    }
    /* C:\...（未加 \\?\ 前缀时） */
    if (path.size() >= 3 && path[1] == L':' && (path[2] == L'\\' || path[2] == L'/')) {
        wchar_t d = path[0];
        if ((d >= L'A' && d <= L'Z') || (d >= L'a' && d <= L'z')) {
            std::wstring out = L"\\\\?\\";
            out.push_back(d);
            out.append(L":\\");
            return out;
        }
    }
    /* 挂载点等特殊路径：回退 API */
    DWORD cap = MAX_PATH * 4;
    std::wstring buf(cap, L'\0');
    if (GetVolumePathNameW(path.c_str(), &buf[0], cap)) {
        buf.resize(wcslen(buf.c_str()));
        return buf;
    }
    return path;
}

static inline bool IsBusyOrGone(DWORD err) {
    return err == ERROR_SHARING_VIOLATION || err == ERROR_LOCK_VIOLATION
        || err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND;
}
static inline bool IsGone(DWORD err) {
    return err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND;
}

/** 文件或空目录：Win10+ FDIX，按卷记录是否支持，避免单卷失败全局降级。
 *  volKeyOpt 非空时跳过卷解析；同线程连续同卷命中 tls 则跳过 map 锁。
 *  outErr：失败时写入 CreateFile/SetFileInformation 的 LastError。 */
inline bool SetDispositionDeleteEx(const std::wstring& path, DWORD baseFlags,
                                   const std::wstring* volKeyOpt = nullptr, DWORD* outErr = nullptr) {
    std::wstring volLocal;
    const std::wstring& vol = (volKeyOpt && !volKeyOpt->empty())
        ? *volKeyOpt
        : (volLocal = GetVolumeRootKey(path), volLocal);

    struct TlsFdx {
        std::wstring key;
        int state = -1; /* -1 unknown, 0 unsupported, 1 ok */
    };
    static thread_local TlsFdx tls;

    if (tls.state == 0 && tls.key == vol) {
        if (outErr) *outErr = ERROR_NOT_SUPPORTED;
        return false;
    }

    bool skipMapLookup = (tls.state == 1 && tls.key == vol);
    if (!skipMapLookup) {
        std::lock_guard<std::mutex> lk(g_fdxVolumeMtx);
        auto it = g_fdxVolumeOk.find(vol);
        if (it != g_fdxVolumeOk.end()) {
            tls.key = vol;
            tls.state = it->second ? 1 : 0;
            if (!it->second) {
                if (outErr) *outErr = ERROR_NOT_SUPPORTED;
                return false;
            }
            skipMapLookup = true;
        }
    }

    HANDLE h = CreateFileW(path.c_str(), DELETE, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, baseFlags, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        if (outErr) *outErr = GetLastError();
        return false;
    }
    ApplyFileHandleIoPriorityHigh(h);
    MY_FILE_DISPOSITION_INFO_EX info = {0};
    info.Flags = FILE_DISPOSITION_DELETE | FILE_DISPOSITION_POSIX_SEMANTICS | FILE_DISPOSITION_IGNORE_READONLY_ATTRIBUTE;
    BOOL ok = SetFileInformationByHandle(h, (FILE_INFO_BY_HANDLE_CLASS)FDIX_CLASS, &info, sizeof(info));
    DWORD err = ok ? ERROR_SUCCESS : GetLastError();
    CloseHandle(h);
    if (outErr) *outErr = err;
    if (!ok && (err == 87 || err == 50)) {
        std::lock_guard<std::mutex> lk(g_fdxVolumeMtx);
        g_fdxVolumeOk[vol] = false;
        tls.key = vol;
        tls.state = 0;
    } else if (ok) {
        if (!skipMapLookup || tls.state != 1 || tls.key != vol) {
            std::lock_guard<std::mutex> lk(g_fdxVolumeMtx);
            g_fdxVolumeOk[vol] = true;
        }
        tls.key = vol;
        tls.state = 1;
    }
    return ok != FALSE;
}

inline bool TryDeleteFile(const std::wstring& path, const std::wstring* volKeyOpt = nullptr) {
    const DWORD flags = FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT;
    DWORD err = 0;
    if (SetDispositionDeleteEx(path, flags, volKeyOpt, &err)) return true;
    if (IsGone(err)) return true;
    if (IsBusyOrGone(err)) return false;

    /* Legacy：清只读后再 DeleteFile；占用则绝不走 ACL */
    if (SetFileAttributesW(path.c_str(), FILE_ATTRIBUTE_NORMAL) || GetLastError() == ERROR_FILE_NOT_FOUND) {
        if (DeleteFileW(path.c_str())) return true;
        err = GetLastError();
        if (IsGone(err)) return true;
        if (err == ERROR_SHARING_VIOLATION || err == ERROR_LOCK_VIOLATION) return false;
    } else {
        err = GetLastError();
        if (IsGone(err)) return true;
        if (err == ERROR_SHARING_VIOLATION || err == ERROR_LOCK_VIOLATION) return false;
    }

    /* 绝对禁止对符号链接/交接点提权 */
    DWORD attrs = GetFileAttributesW(path.c_str());
    if (attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_REPARSE_POINT))
        return false;

    /* 仅访问拒绝才 TakeOwnership / DACL */
    if (err != ERROR_ACCESS_DENIED)
        return false;

    if (TakeOwnership(path)) {
        SetFileAttributesW(path.c_str(), FILE_ATTRIBUTE_NORMAL);
        if (DeleteFileW(path.c_str())) return true;
        DWORD e2 = GetLastError();
        if (IsGone(e2)) return true;
        if (e2 == ERROR_SHARING_VIOLATION || e2 == ERROR_LOCK_VIOLATION) return false;
        if (GrantDaclFullControl(path)) {
            SetFileAttributesW(path.c_str(), FILE_ATTRIBUTE_NORMAL);
            if (DeleteFileW(path.c_str())) return true;
            return IsGone(GetLastError());
        }
    }
    return false;
}

// ==========================================
// Parallel Scanner
// ==========================================
struct ScopedActiveGuard {
    std::atomic<int>* counter;
    explicit ScopedActiveGuard(std::atomic<int>* c) : counter(c) {}
    ~ScopedActiveGuard() { if (counter) (*counter)--; }
};

void ScannerThread() {
    ApplyThreadIoPriorityHigh();
    std::wstring dirPath;
    std::wstring searchPath;
    size_t dirIdx = 0;
    dirPath.reserve(32768);
    searchPath.reserve(32768);
    BatchPtr currentBatch = std::make_unique<std::vector<FileItem>>();
    currentBatch->reserve(BATCH_SIZE_SMALL);
    std::vector<std::wstring> childSegs;
    std::vector<char> childTraverse;
    std::vector<size_t> childIdx;
    childSegs.reserve(256);
    childTraverse.reserve(256);
    childIdx.reserve(256);

    while (g_scanQueue.pop(dirIdx) && !g_stopRequested.load(std::memory_order_relaxed)) {
        ScopedActiveGuard guard(&g_activeScanners);
        ReconstructPathTo(dirIdx, dirPath);
        searchPath.assign(dirPath);
        searchPath.append(L"\\*");
        WIN32_FIND_DATAW fd;
        HANDLE hFind = FindFirstFileExW(
            searchPath.c_str(),
            FindExInfoBasic,
            &fd,
            FindExSearchNameMatch,
            NULL,
            FIND_FIRST_EX_LARGE_FETCH);
        if (hFind == INVALID_HANDLE_VALUE) {
            EmitScanWarn(dirPath, GetLastError());
            continue;
        }

        childSegs.clear();
        childTraverse.clear();
        long long filesInDir = 0;
        do {
            if (g_stopRequested.load(std::memory_order_relaxed)) break;
            if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;

            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                childSegs.emplace_back(fd.cFileName);
                childTraverse.push_back(
                    ((fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0) ? 1 : 0);
            } else {
                filesInDir++;
                long long size = ((long long)fd.nFileSizeHigh << 32) | fd.nFileSizeLow;
                FileItem item;
                item.dirIdx = dirIdx;
                item.name.assign(fd.cFileName);
                item.size = size;
                if (size > LARGE_FILE_THRESHOLD) {
                    g_largeQueue.push(std::move(item));
                } else {
                    currentBatch->push_back(std::move(item));
                    if (currentBatch->size() >= BATCH_SIZE_SMALL) {
                        g_smallQueue.push(std::move(currentBatch));
                        currentBatch = std::make_unique<std::vector<FileItem>>();
                        currentBatch->reserve(BATCH_SIZE_SMALL);
                    }
                }
            }
        } while (FindNextFileW(hFind, &fd));
        FindClose(hFind);

        if (!childSegs.empty()) {
            AddChildDirsBatch(dirIdx, childSegs, childIdx);
            g_scanQueue.pushBatch(childIdx, childTraverse);
        }
        if (filesInDir > 0)
            g_totalFilesFound.fetch_add(filesInDir, std::memory_order_relaxed);
    }

    if (!currentBatch->empty()) g_smallQueue.push(std::move(currentBatch));
}

// ==========================================
// Workers (父目录路径 + 卷键本地缓存)
// ==========================================
struct WorkerPathCache {
    size_t lastDirIdx = DIR_PARENT_NONE;
    std::wstring fullPath;
    size_t dirBaseLen = 0;
    std::wstring volKey;

    WorkerPathCache() {
        fullPath.reserve(32768);
        volKey.reserve(64);
    }

    const std::wstring& resolve(const FileItem& item) {
        if (item.dirIdx == DIR_PARENT_NONE) {
            lastDirIdx = DIR_PARENT_NONE;
            volKey = GetVolumeRootKey(item.name);
            return item.name;
        }
        if (item.dirIdx != lastDirIdx) {
            ReconstructPathTo(item.dirIdx, fullPath, !g_scanComplete.load(std::memory_order_relaxed));
            dirBaseLen = fullPath.size();
            volKey = GetVolumeRootKey(fullPath);
            lastDirIdx = item.dirIdx;
        }
        fullPath.resize(dirBaseLen);
        fullPath.push_back(L'\\');
        fullPath.append(item.name);
        return fullPath;
    }
};

void SmallWorkerThread() {
    ApplyThreadIoPriorityHigh();
    WorkerPathCache cache;
    BatchPtr batch;
    while (g_smallQueue.pop(batch) && !g_stopRequested.load(std::memory_order_relaxed)) {
        long long del = 0, fail = 0, bytes = 0;
        for (const auto& item : *batch) {
            const std::wstring& targetPath = cache.resolve(item);
            if (TryDeleteFile(targetPath, &cache.volKey)) {
                del++;
                bytes += item.size;
            } else {
                fail++;
                std::lock_guard<std::mutex> lk(g_failedPaths_mtx);
                if (g_failedPaths.size() < MAX_FAILED_PATHS) g_failedPaths.push_back(targetPath);
            }
        }
        if (del) {
            g_filesDeleted.fetch_add(del, std::memory_order_relaxed);
            g_totalBytesFreed.fetch_add(bytes, std::memory_order_relaxed);
        }
        if (fail)
            g_filesFailed.fetch_add(fail, std::memory_order_relaxed);
    }
}

void LargeWorkerThread() {
    ApplyThreadIoPriorityHigh();
    WorkerPathCache cache;
    FileItem item;
    long long del = 0, fail = 0, bytes = 0;
    const int FLUSH_N = 32;
    int sinceFlush = 0;
    auto flush = [&]() {
        if (del) {
            g_filesDeleted.fetch_add(del, std::memory_order_relaxed);
            g_totalBytesFreed.fetch_add(bytes, std::memory_order_relaxed);
            del = 0; bytes = 0;
        }
        if (fail) {
            g_filesFailed.fetch_add(fail, std::memory_order_relaxed);
            fail = 0;
        }
        sinceFlush = 0;
    };
    while (g_largeQueue.pop(item) && !g_stopRequested.load(std::memory_order_relaxed)) {
        const std::wstring& targetPath = cache.resolve(item);
        if (TryDeleteFile(targetPath, &cache.volKey)) {
            del++;
            bytes += item.size;
        } else {
            fail++;
            std::lock_guard<std::mutex> lk(g_failedPaths_mtx);
            if (g_failedPaths.size() < MAX_FAILED_PATHS) g_failedPaths.push_back(targetPath);
        }
        if (++sinceFlush >= FLUSH_N) flush();
    }
    flush();
}

bool TryRemoveDir(const std::wstring& path, const std::wstring* volKeyOpt = nullptr) {
    /* 绝对禁止对符号链接/交接点提权（先看属性再删，避免穿透） */
    DWORD attrs = GetFileAttributesW(path.c_str());
    if (attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_REPARSE_POINT)) {
        if (RemoveDirectoryW(path.c_str())) return true;
        return IsGone(GetLastError());
    }

    /* POSIX 删文件后句柄未全部关闭时目录会短暂非空：DIR_NOT_EMPTY 短退避重试 */
    auto removeDirRetry = [&]() -> DWORD {
        DWORD err = 0;
        for (int attempt = 0; attempt < 3; ++attempt) {
            if (RemoveDirectoryW(path.c_str())) return 0;
            err = GetLastError();
            if (err == ERROR_DIR_NOT_EMPTY && attempt < 2) {
                Sleep(20);
                continue;
            }
            return err;
        }
        return err;
    };

    /* FDIX 优先（只读忽略 + POSIX），与 TryDeleteFile 一致 */
    DWORD fdixErr = 0;
    for (int attempt = 0; attempt < 3; ++attempt) {
        if (SetDispositionDeleteEx(path, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, volKeyOpt, &fdixErr))
            return true;
        if (IsGone(fdixErr)) return true;
        if (fdixErr == ERROR_SHARING_VIOLATION || fdixErr == ERROR_LOCK_VIOLATION) return false;
        if (fdixErr == ERROR_DIR_NOT_EMPTY && attempt < 2) {
            Sleep(20);
            continue;
        }
        break;
    }
    if (fdixErr == ERROR_DIR_NOT_EMPTY) {
        /* FDIX 报非空时再试 Win32 RemoveDirectory（可能已空） */
        DWORD err = removeDirRetry();
        if (err == 0 || IsGone(err)) return true;
        if (err == ERROR_DIR_NOT_EMPTY) return false;
    }

    DWORD err = removeDirRetry();
    if (err == 0 || IsGone(err)) return true;
    if (err == ERROR_DIR_NOT_EMPTY) return false;
    if (err == ERROR_SHARING_VIOLATION || err == ERROR_LOCK_VIOLATION) return false;

    SetFileAttributesW(path.c_str(), FILE_ATTRIBUTE_NORMAL);
    err = removeDirRetry();
    if (err == 0 || IsGone(err)) return true;
    if (err == ERROR_DIR_NOT_EMPTY) return false;
    if (err == ERROR_SHARING_VIOLATION || err == ERROR_LOCK_VIOLATION) return false;
    if (err != ERROR_ACCESS_DENIED && fdixErr != ERROR_ACCESS_DENIED) return false;

    if (TakeOwnership(path)) {
        SetFileAttributesW(path.c_str(), FILE_ATTRIBUTE_NORMAL);
        err = removeDirRetry();
        if (err == 0 || IsGone(err)) return true;
        if (err == ERROR_DIR_NOT_EMPTY) return false;
        if (err == ERROR_SHARING_VIOLATION || err == ERROR_LOCK_VIOLATION) return false;
        if (GrantDaclFullControl(path)) {
            SetFileAttributesW(path.c_str(), FILE_ATTRIBUTE_NORMAL);
            err = removeDirRetry();
            return err == 0 || IsGone(err);
        }
    }
    return false;
}

void DirCleanerThread(size_t nodeIdx, bool force = false) {
    /* force=true：用户中止后的尽力清理，忽略 g_stopRequested 早退 */
    if (!force && g_stopRequested.load()) return;
    std::wstring path;
    ReconstructPathTo(nodeIdx, path, false); /* 扫描已结束，树只读 */
    std::wstring vol = GetVolumeRootKey(path);
    if (TryRemoveDir(path, &vol))
        g_dirsDeleted.fetch_add(1, std::memory_order_relaxed);
    else {
        g_dirsFailed.fetch_add(1, std::memory_order_relaxed);
        std::lock_guard<std::mutex> lk(g_failedPaths_mtx);
        if (g_failedPaths.size() < MAX_FAILED_PATHS) g_failedPaths.push_back(std::move(path));
    }
}

/* 目录阶段复用固定 worker，避免每层反复创建/销毁 thread */
struct DirWorkerPool {
    std::mutex mtx;
    std::condition_variable cv;
    std::queue<std::function<void()>> tasks;
    std::vector<std::thread> workers;
    std::atomic<bool> stop{false};
    std::atomic<bool> shut_done{false};

    explicit DirWorkerPool(size_t n) {
        for (size_t i = 0; i < n; ++i) {
            workers.emplace_back([this] {
                ApplyThreadIoPriorityHigh();
                for (;;) {
                    std::function<void()> job;
                    {
                        std::unique_lock<std::mutex> lk(mtx);
                        cv.wait(lk, [this] { return stop.load() || !tasks.empty(); });
                        if (stop.load() && tasks.empty()) return;
                        if (tasks.empty()) continue;
                        job = std::move(tasks.front());
                        tasks.pop();
                    }
                    job();
                }
            });
        }
    }
    void submit(std::function<void()> f) {
        std::lock_guard<std::mutex> lk(mtx);
        tasks.push(std::move(f));
        cv.notify_one();
    }
    void shutdown() {
        bool ex = false;
        if (!shut_done.compare_exchange_strong(ex, true))
            return;
        {
            std::lock_guard<std::mutex> lk(mtx);
            stop = true;
        }
        cv.notify_all();
        for (auto& t : workers)
            if (t.joinable()) t.join();
    }
    ~DirWorkerPool() { shutdown(); }
};

// ==========================================
// Monitors
// ==========================================
/* Interactive console: overwrite same line with \\r; pipe/file redirect: full lines (\\n) for parsers */
static bool g_stdoutIsInteractiveConsole = []() -> bool {
    HANDLE h = GetStdHandle(STD_OUTPUT_HANDLE);
    if (h == INVALID_HANDLE_VALUE || h == NULL) return false;
    return GetFileType(h) == FILE_TYPE_CHAR;
}();

void DashboardThread(steady_clock::time_point start_time) {
    int last_pct = -1;
    long long last_scan_tot = -1;
    for (;;) {
        {
            std::unique_lock<std::mutex> lk(g_dashMtx);
            g_dashCv.wait_for(lk, milliseconds(500), [] {
                return g_dashboardDone.load(std::memory_order_relaxed)
                    || g_stopRequested.load(std::memory_order_relaxed);
            });
        }
        if (g_dashboardDone.load(std::memory_order_relaxed))
            break;

        auto now = steady_clock::now();
        auto sec = duration_cast<seconds>(now - start_time).count();
        if (sec == 0) sec = 1;

        long long f_del = g_filesDeleted.load(std::memory_order_relaxed);
        long long f_fail = g_filesFailed.load(std::memory_order_relaxed);
        long long d_del = g_dirsDeleted.load(std::memory_order_relaxed);
        long long total = f_del + d_del;
        long long total_found = g_totalFilesFound.load(std::memory_order_relaxed);
        bool scan_done = g_scanComplete.load(std::memory_order_relaxed);

        std::lock_guard<std::mutex> lock(g_consoleMtx);
        if (!scan_done) {
            if (total != last_scan_tot) {
                std::cout << "PROGRESS:SCANNING:" << total << std::endl;
                last_scan_tot = total;
            }
        } else {
            long long d_fail = g_dirsFailed.load(std::memory_order_relaxed);
            long long processed = f_del + f_fail + d_del + d_fail;
            long long total_items = total_found + (long long)g_dirCount.load(std::memory_order_relaxed);
            int pct = (total_items > 0) ? (int)((processed * 100) / total_items) : 0;
            if (pct > 100) pct = 100;
            if (pct != last_pct) {
                std::cout << "PROGRESS:" << pct << "%" << std::endl;
                last_pct = pct;
            }
        }
        if (g_stdoutIsInteractiveConsole) {
            std::cout << "\r" << (g_isProcessingDirs ? "[Dirs]" : "[File]")
                      << " Tot: " << total << " | Avg: " << total/sec << "/s"
                      << " | Fail: " << f_fail
                      << " | Q_S: " << g_smallQueue.size_approx() << " | ScanQ: " << g_scanQueue.size_approx()
                      << "      " << std::flush;
        } else {
            std::cout << (g_isProcessingDirs ? "[Dirs]" : "[File]")
                      << " Tot: " << total << " | Avg: " << total/sec << "/s"
                      << " | Fail: " << f_fail
                      << " | Q_S: " << g_smallQueue.size_approx() << " | ScanQ: " << g_scanQueue.size_approx()
                      << "      " << std::endl;
        }

        if (g_isProcessingDirs
            && g_dirsDeleted.load(std::memory_order_relaxed) + g_dirsFailed.load(std::memory_order_relaxed)
                >= (long long)g_dirCount.load(std::memory_order_relaxed))
            break;
    }
}

void StopMonitor() {
    if (g_stopEvent) {
        WaitForSingleObject(g_stopEvent, INFINITE);
        g_stopRequested.store(true);
        g_scanQueue.setDone();
        g_smallQueue.setDone();
        g_largeQueue.setDone();
    }
}

// ==========================================
// Main (wmain for full Unicode argv)
// ==========================================
int wmain(int argc, wchar_t* argv[]) {
    SetConsoleOutputCP(65001);
    /* 大量 PROGRESS 行时减轻 iostream 与 stdio 同步开销（仅 cout） */
    std::ios::sync_with_stdio(false);
    std::wcin.tie(nullptr);
    SetPriorityClass(GetCurrentProcess(), HIGH_PRIORITY_CLASS);
    EnablePrivilege(L"SeTakeOwnershipPrivilege");
    EnablePrivilege(L"SeRestorePrivilege");
    (void)EnsureAdministratorsSid();

    if (argc < 2) {
        std::wcerr << L"pb_xdelete: usage:\n"
                      L"  pb_xdelete.exe [--threads N] --list <utf8_list.txt>\n"
                      L"  pb_xdelete.exe [--threads N] <dir1> [dir2] ...\n"
                      L"  Env: PB_XDELETE_THREADS=N (CLI --threads overrides)\n"
                      L"  Exit: 0=ok 1=bad args 2=aborted 3=partial fail\n";
        return 1;
    }

    CliOptions opt = ParseCli(argc, argv);
    if (!opt.ok) {
        std::wcerr << L"pb_xdelete: " << (opt.err ? opt.err : L"invalid arguments") << L"\n"
                      L"  pb_xdelete.exe [--threads N] --list <utf8_list.txt>\n"
                      L"  pb_xdelete.exe [--threads N] <dir1> [dir2] ...\n"
                      L"  Env: PB_XDELETE_THREADS=N\n"
                      L"  Exit: 0=ok 1=bad args 2=aborted 3=partial fail\n";
        return 1;
    }

    std::cout << "PB_XDELETE_PROTOCOL:1\n" << std::flush;

    /* 创建/打开命名事件供 GUI SetEvent；禁止 ResetEvent，以免抹掉 GUI 在 Popen 前后发出的早期停止信号 */
    g_stopEvent = CreateEventW(NULL, TRUE, FALSE, STOP_EVENT_NAME);
    if (!g_stopEvent)
        g_stopEvent = OpenEventW(SYNCHRONIZE | EVENT_MODIFY_STATE, FALSE, STOP_EVENT_NAME);
    g_scanQueue.setOnPopIncrement(&g_activeScanners);

    BatchPtr initSmallBatch = std::make_unique<std::vector<FileItem>>();
    initSmallBatch->reserve(BATCH_SIZE_SMALL);
    long long targetsAccepted = 0;   /* 成功入队的目录/文件数 */
    long long targetsNotFound = 0;   /* 路径不存在 */
    bool listOpenFailed = false;

    auto LoadPathW = [&initSmallBatch, &targetsAccepted, &targetsNotFound](const std::wstring& root_in) {
        std::wstring root = FixPath(root_in);
        DWORD attrs = GetFileAttributesW(root.c_str());
        if (attrs == INVALID_FILE_ATTRIBUTES) {
            DWORD err = GetLastError();
            if (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND) {
                targetsNotFound++;
                int len = WideCharToMultiByte(CP_UTF8, 0, root.c_str(), -1, NULL, 0, NULL, NULL);
                std::lock_guard<std::mutex> lock(g_consoleMtx);
                if (len > 0) {
                    std::vector<char> buf((size_t)len);
                    WideCharToMultiByte(CP_UTF8, 0, root.c_str(), -1, &buf[0], len, NULL, NULL);
                    std::cout << "WARN:TARGET_NOT_FOUND path=" << &buf[0] << std::endl;
                } else {
                    std::cout << "WARN:TARGET_NOT_FOUND" << std::endl;
                }
            } else {
                g_filesFailed.fetch_add(1, std::memory_order_relaxed);
                {
                    std::lock_guard<std::mutex> lk(g_failedPaths_mtx);
                    if (g_failedPaths.size() < MAX_FAILED_PATHS)
                        g_failedPaths.push_back(root);
                }
                {
                    std::lock_guard<std::mutex> lock(g_consoleMtx);
                    std::cout << "WARN:TARGET_ACCESS_FAIL err=" << err << std::endl;
                }
            }
            return;
        }
        if (attrs & FILE_ATTRIBUTE_DIRECTORY) {
            const size_t rootIdx = AddRootDir(root);
            if ((attrs & FILE_ATTRIBUTE_REPARSE_POINT) == 0)
                g_scanQueue.push(rootIdx);
            targetsAccepted++;
            return;
        }
        /* 单文件：攒批入队，避免 1 文件 1 Batch 撑爆小队列 */
        WIN32_FILE_ATTRIBUTE_DATA fad = {};
        long long size = 0;
        if (GetFileAttributesExW(root.c_str(), GetFileExInfoStandard, &fad))
            size = ((long long)fad.nFileSizeHigh << 32) | fad.nFileSizeLow;
        FileItem item;
        item.dirIdx = DIR_PARENT_NONE;
        item.name = std::move(root);
        item.size = size;
        g_totalFilesFound.fetch_add(1, std::memory_order_relaxed);
        if (size > LARGE_FILE_THRESHOLD) {
            g_largeQueue.push(std::move(item));
        } else {
            initSmallBatch->push_back(std::move(item));
            if (initSmallBatch->size() >= BATCH_SIZE_SMALL) {
                g_smallQueue.push(std::move(initSmallBatch));
                initSmallBatch = std::make_unique<std::vector<FileItem>>();
                initSmallBatch->reserve(BATCH_SIZE_SMALL);
            }
        }
        targetsAccepted++;
    };
    std::thread(StopMonitor).detach();

    unsigned hc = std::thread::hardware_concurrency();
    int cores = (hc == 0) ? 4 : (int)hc;
    /* --threads / 环境变量指定时用该预算；自动模式保留 1 核给 OS/Dashboard */
    const bool threadsForced = (opt.threadOverride > 0);
    int pool = threadsForced ? opt.threadOverride : cores;
    int reserve = threadsForced ? 0 : 1;

    bool hddLike = false;
    {
        std::wstring peek = PeekFirstRoot(opt);
        if (!peek.empty()) {
            std::wstring fp = FixPath(peek);
            if (GetFileAttributesW(fp.c_str()) != INVALID_FILE_ATTRIBUTES)
                hddLike = VolumeHasSeekPenalty(fp);
        }
    }

    int numScanners;
    int numSmall;
    int numLarge;
    if (hddLike) {
        numScanners = 1;
        numLarge = 1;
        numSmall = (pool >= 2) ? 2 : 1;
        if (threadsForced && pool >= 3)
            numSmall = (std::max)(1, pool - numScanners - numLarge);
    } else {
        if (pool >= 8) numScanners = 4;
        else if (pool > 4) numScanners = (std::min)(3, pool / 2);
        else if (pool > 2) numScanners = 2;
        else numScanners = 1;
        numLarge = (pool > 6) ? 2 : 1;
        numSmall = pool - numScanners - numLarge - reserve;
        if (numSmall < 1) numSmall = 1;
    }

    std::cout << "Engine: V107 | Hardware: " << cores << " Cores | Pool: " << pool
              << (threadsForced ? " (forced)" : " (auto)")
              << " | Scanners: " << numScanners
              << " | Small: " << numSmall << " | Large: " << numLarge
              << " | Media: " << (hddLike ? "HDD" : "SSD-like") << std::endl;

    auto start_time = steady_clock::now();
    std::thread dash(DashboardThread, start_time);

    std::vector<std::thread> scanners;
    for (int i = 0; i < numScanners; ++i) scanners.emplace_back(ScannerThread);

    std::vector<std::thread> smallWorkers;
    for (int i = 0; i < numSmall; ++i) smallWorkers.emplace_back(SmallWorkerThread);

    std::vector<std::thread> largeWorkers;
    for (int i = 0; i < numLarge; ++i) largeWorkers.emplace_back(LargeWorkerThread);

    // Initial tasks: --list <file> or direct paths (Unicode-safe)
    // Use std::getline to avoid buffer truncation (Windows paths can exceed 32K chars)
    // std::filesystem::path enables wchar_t* on MinGW (libstdc++ lacks native ifstream wchar_t overload)
    if (opt.listMode) {
        std::ifstream f{std::filesystem::path(opt.listFile)};
        if (!f.is_open()) {
            listOpenFailed = true;
            std::cerr << "WARN: Cannot open list file." << std::endl;
        } else {
            int loaded = 0;
            std::string line;
            while (std::getline(f, line)) {
                size_t s = line.find_first_not_of(" \t\r\n");
                if (s == std::string::npos) continue;
                size_t e = line.find_last_not_of(" \t\r\n");
                line = line.substr(s, e - s + 1);
                if (!line.empty()) {
                    // Strip UTF-8 BOM if present (Python utf-8-sig may write it)
                    if (line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB && (unsigned char)line[2] == 0xBF)
                        line = line.substr(3);
                    int len = MultiByteToWideChar(CP_UTF8, 0, line.c_str(), -1, NULL, 0);
                    if (len > 0) {
                        std::vector<wchar_t> wbuf(len);
                        MultiByteToWideChar(CP_UTF8, 0, line.c_str(), -1, &wbuf[0], len);
                        LoadPathW(std::wstring(&wbuf[0]));
                        loaded++;
                    }
                }
            }
            if (loaded == 0) std::cerr << "WARN: List file is empty or contains no valid paths." << std::endl;
        }
    } else {
        for (const auto& root : opt.roots) LoadPathW(root);
    }
    if (initSmallBatch && !initSmallBatch->empty())
        g_smallQueue.push(std::move(initSmallBatch));

    // Wait: queue empty AND no scanner actively processing (may push new dirs)
    while ((g_scanQueue.size() > 0 || g_activeScanners > 0) && !g_stopRequested.load()) {
        std::this_thread::sleep_for(milliseconds(50));
    }
    g_scanComplete.store(true);
    g_scanQueue.setDone();
    for (auto& t : scanners) if (t.joinable()) t.join();

    g_smallQueue.setDone();
    g_largeQueue.setDone();
    for (auto& t : smallWorkers) if (t.joinable()) t.join();
    for (auto& t : largeWorkers) if (t.joinable()) t.join();

    // Directory Cleanup: depth-level parallel；中止后仍 force 尽力清空目录
    const bool abortedBeforeDirs = g_stopRequested.load(std::memory_order_relaxed);
    if (!g_allDirNodes.empty() && ( !abortedBeforeDirs || targetsAccepted > 0)) {
        if (abortedBeforeDirs) {
            std::lock_guard<std::mutex> lock(g_consoleMtx);
            std::cout << "INFO: Abort — best-effort empty directory cleanup...\n" << std::flush;
        }
        g_isProcessingDirs = true;
        /* O(N) 按 depth 分桶；同层无父子，无需 path_len 排序 */
        std::vector<std::vector<size_t>> byDepth;
        size_t maxDepth = 0;
        for (size_t i = 0; i < g_allDirNodes.size(); ++i) {
            size_t d = g_allDirNodes[i].depth;
            if (d >= byDepth.size()) byDepth.resize(d + 1);
            byDepth[d].push_back(i);
            if (d > maxDepth) maxDepth = d;
        }
        size_t numChunks = (cores > 0) ? (size_t)cores : 4;
        if (hddLike)
            numChunks = (cores >= 2) ? 2 : 1;
        if (abortedBeforeDirs && numChunks > 2)
            numChunks = 2; /* 中止后降低并发，尽快收尾 */
        DirWorkerPool dirPool(numChunks);
        std::mutex dir_level_mtx;
        std::condition_variable dir_level_cv;
        for (size_t k = 0; k <= maxDepth; ++k) {
            if (!abortedBeforeDirs && g_stopRequested.load()) break;
            size_t level = maxDepth - k;
            if (level >= byDepth.size()) continue;
            const std::vector<size_t>& indices = byDepth[level];
            if (indices.empty()) continue;
            /* 无 g_dirQueue：按层原子索引从 g_allDirNodes 下标派发，无队列容量反压 */
            std::atomic<size_t> nextIdx{0};
            std::atomic<int> pending{0};
            const bool forceClean = abortedBeforeDirs;
            for (size_t w = 0; w < numChunks; ++w) {
                pending.fetch_add(1, std::memory_order_acq_rel);
                dirPool.submit([&]() {
                    for (;;) {
                        size_t j = nextIdx.fetch_add(1, std::memory_order_acq_rel);
                        if (j >= indices.size()) break;
                        if (!forceClean && g_stopRequested.load()) break;
                        DirCleanerThread(indices[j], forceClean);
                    }
                    const int prev = pending.fetch_sub(1, std::memory_order_acq_rel);
                    if (prev == 1) {
                        std::lock_guard<std::mutex> lk(dir_level_mtx);
                        dir_level_cv.notify_one();
                    }
                });
            }
            std::unique_lock<std::mutex> lk(dir_level_mtx);
            dir_level_cv.wait(lk, [&] {
                return pending.load(std::memory_order_acquire) == 0 ;
            });
        }
    }

    auto end_time = steady_clock::now();
    auto total_time_ms = duration_cast<milliseconds>(end_time - start_time).count();

    g_dashboardDone.store(true, std::memory_order_relaxed);
    g_dashCv.notify_all();
    if (dash.joinable()) dash.join();

    {
        std::lock_guard<std::mutex> lock(g_consoleMtx);
        std::cout << "\n========================================\n"
                  << (g_stopRequested ? "USER_ABORTED" : "DONE.") << "\n"
                  << "SUMMARY: Files_OK=" << g_filesDeleted.load() 
                  << " | Files_Fail=" << g_filesFailed.load()
                  << " | Dirs=" << g_dirsDeleted.load()
                  << " | Bytes_Freed=" << g_totalBytesFreed.load();
        if (g_dirsFailed.load() > 0) std::cout << " | Dirs_Fail=" << g_dirsFailed.load();
        std::cout << "\n";
        {
            std::lock_guard<std::mutex> lk(g_failedPaths_mtx);
            if (!g_failedPaths.empty()) {
                std::cout << "FAILED_PATHS_BEGIN\n";
                for (const auto& p : g_failedPaths) {
                    int len = WideCharToMultiByte(CP_UTF8, 0, p.c_str(), -1, NULL, 0, NULL, NULL);
                    if (len > 0) {
                        std::vector<char> buf(len);
                        WideCharToMultiByte(CP_UTF8, 0, p.c_str(), -1, &buf[0], len, NULL, NULL);
                        std::cout << "FAILED: " << &buf[0] << "\n";
                    }
                }
                if ((long long)g_failedPaths.size() < g_filesFailed.load() + g_dirsFailed.load())
                    std::cout << "... (truncated)\n";
                std::cout << "FAILED_PATHS_END\n";
            }
        }
        std::cout << "Time: " << total_time_ms / 1000.0 << " s\n";

        /* 0=全成功 1=仅用于参数错误(上方已 return)
         * 2=用户中止 3=部分失败 / 空跑 / 清单打不开 / 零有效目标 */
        int exitCode = 0;
        if (g_stopRequested.load(std::memory_order_relaxed))
            exitCode = 2;
        else if (listOpenFailed
              || targetsAccepted == 0
              || g_filesFailed.load(std::memory_order_relaxed) > 0
              || g_dirsFailed.load(std::memory_order_relaxed) > 0)
            exitCode = 3;
        if (exitCode == 3 && targetsAccepted == 0 && !listOpenFailed) {
            std::cout << "WARN:EMPTY_RUN not_found=" << targetsNotFound << "\n";
        }
        std::cout << "EXIT_CODE:" << exitCode << "\n"
                  << "========================================" << std::endl;

        /* 不 CloseHandle(g_stopEvent)：StopMonitor 可能仍 WaitForSingleObject；进程退出时 OS 回收 */
        g_stopEvent = NULL;
        return exitCode;
    }
}