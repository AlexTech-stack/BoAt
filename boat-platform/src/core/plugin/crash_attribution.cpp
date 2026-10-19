// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#include "plugin/crash_attribution.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <mutex>

#include <execinfo.h>
#include <link.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

namespace boat::core::crash {

namespace {

constexpr std::size_t kNameMax = 256;
constexpr int kMaxRanges = 16;
constexpr int kMaxFrames = 64;

enum : std::uint32_t { kFree = 0, kWriting = 1, kLive = 2 };

struct Range {
  std::uintptr_t begin;
  std::uintptr_t end;
};

/* Everything the signal handler reads lives in this fixed array. A writer
   claims a free slot, fills it while it reads kWriting, and publishes it with
   a release store of kLive; the handler only looks at kLive slots. A slot
   being freed concurrently with a crash can be read torn -- acceptable for a
   last-gasp diagnostic, and the strings are always NUL-terminated within
   their arrays, so a torn read cannot run off the end. */
struct Slot {
  std::atomic<std::uint32_t> state{kFree};
  char key[kNameMax];
  char so_path[kNameMax];
  Range ranges[kMaxRanges];
  int range_count;
};
static_assert(std::atomic<std::uint32_t>::is_always_lock_free,
              "the signal handler reads slot state lock-free");

Slot g_slots[kMaxPluginImages];
std::mutex g_register_mutex;  // writers only; the handler never takes it
std::atomic<bool> g_full_warned{false};

/* Per-thread "which plugin callback am I inside". initial-exec so reading it
   from the signal handler is a plain TLS load rather than a __tls_get_addr
   call that may allocate. Safe because boat_core is a static library linked
   only into executables, never into a dlopen()'d object. */
thread_local int t_active_slot __attribute__((tls_model("initial-exec"))) = -1;
thread_local CallbackKind t_active_kind __attribute__((tls_model("initial-exec"))) =
    CallbackKind::kNone;
thread_local bool t_alt_stack_checked = false;

constexpr int kSignals[] = {SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT};
constexpr int kNumSignals = static_cast<int>(sizeof(kSignals) / sizeof(kSignals[0]));
struct sigaction g_previous[kNumSignals];
std::atomic<bool> g_installed{false};
std::mutex g_install_mutex;
std::atomic<long> g_reporting_tid{0};

void CopyBounded(char* dst, const std::string& src) {
  const std::size_t n = std::min(src.size(), kNameMax - 1);
  std::memcpy(dst, src.data(), n);
  dst[n] = '\0';
}

/* ── Image ranges ─────────────────────────────────────────────────────── */

struct FindImage {
  std::uintptr_t target;
  Range* out;
  int count;
  bool found;
};

int FindImageCallback(dl_phdr_info* info, std::size_t /*size*/, void* data) {
  auto* find = static_cast<FindImage*>(data);
  bool contains = false;
  for (int i = 0; i < static_cast<int>(info->dlpi_phnum); ++i) {
    const auto& ph = info->dlpi_phdr[i];
    if (ph.p_type != PT_LOAD) continue;
    const std::uintptr_t begin = info->dlpi_addr + ph.p_vaddr;
    const std::uintptr_t end = begin + ph.p_memsz;
    if (find->target >= begin && find->target < end) {
      contains = true;
      break;
    }
  }
  if (!contains) return 0;  // keep iterating
  find->count = 0;
  for (int i = 0; i < static_cast<int>(info->dlpi_phnum) && find->count < kMaxRanges; ++i) {
    const auto& ph = info->dlpi_phdr[i];
    if (ph.p_type != PT_LOAD) continue;
    const std::uintptr_t begin = info->dlpi_addr + ph.p_vaddr;
    find->out[find->count++] = Range{begin, begin + ph.p_memsz};
  }
  find->found = true;
  return 1;  // stop
}

bool SlotContains(const Slot& slot, std::uintptr_t addr) noexcept {
  const int n = std::clamp(slot.range_count, 0, kMaxRanges);
  for (int i = 0; i < n; ++i) {
    if (addr >= slot.ranges[i].begin && addr < slot.ranges[i].end) return true;
  }
  return false;
}

bool IsLive(const Slot& slot) noexcept {
  return slot.state.load(std::memory_order_acquire) == kLive;
}

bool AnyLiveSlotContains(std::uintptr_t addr) noexcept {
  for (const auto& slot : g_slots) {
    if (IsLive(slot) && SlotContains(slot, addr)) return true;
  }
  return false;
}

const Slot* LiveSlot(int index) noexcept {
  if (index < 0 || index >= kMaxPluginImages) return nullptr;
  return IsLive(g_slots[index]) ? &g_slots[index] : nullptr;
}

/* ── Per-thread alternate signal stack ────────────────────────────────── */

/* Without one, a stack overflow inside a plugin delivers SIGSEGV with no stack
   left to run the handler on, and the process dies unattributed. sigaltstack
   is per thread, so every thread that calls into a plugin gets its own the
   first time it does (see ActiveCallbackScope). Plugin-owned threads never
   pass through a scope and so get none: an overflow there stays unattributed,
   while every other fault on them is still reported. */
class AltStack {
 public:
  AltStack() = default;
  AltStack(const AltStack&) = delete;
  AltStack& operator=(const AltStack&) = delete;

  ~AltStack() {
    if (mem_ == nullptr) return;
    stack_t cur{};
    if (sigaltstack(nullptr, &cur) != 0) return;
    // Only tear down our own, and never one currently in use.
    if (cur.ss_sp != mem_ || (cur.ss_flags & SS_ONSTACK) != 0) return;
    stack_t off{};
    off.ss_flags = SS_DISABLE;
    if (sigaltstack(&off, nullptr) == 0) munmap(mem_, size_);
  }

  void Ensure() noexcept {
    if (mem_ != nullptr) return;
    stack_t cur{};
    // Someone (a sanitizer runtime, typically) already gave this thread one.
    if (sigaltstack(nullptr, &cur) == 0 && (cur.ss_flags & SS_DISABLE) == 0) return;
    const std::size_t size =
        std::max<std::size_t>(static_cast<std::size_t>(SIGSTKSZ), 64 * 1024);
    void* mem = mmap(nullptr, size, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) return;
    stack_t ss{};
    ss.ss_sp = mem;
    ss.ss_size = size;
    ss.ss_flags = 0;
    if (sigaltstack(&ss, nullptr) != 0) {
      munmap(mem, size);
      return;
    }
    mem_ = mem;
    size_ = size;
  }

 private:
  void* mem_ = nullptr;
  std::size_t size_ = 0;
};

thread_local AltStack t_alt_stack;

/* ── Async-signal-safe output ─────────────────────────────────────────── */

void WriteAll(const char* p, std::size_t n) noexcept {
  while (n > 0) {
    const ssize_t w = write(STDERR_FILENO, p, n);
    if (w < 0) {
      if (errno == EINTR) continue;
      return;
    }
    p += w;
    n -= static_cast<std::size_t>(w);
  }
}

/* One line, assembled in a fixed buffer and written with a single write(2) so
   lines from two crashing threads cannot interleave mid-line. No printf: it is
   not async-signal-safe. */
class Line {
 public:
  Line& Str(const char* s) noexcept { return StrN(s, kCap); }
  Line& StrN(const char* s, std::size_t max) noexcept {
    for (std::size_t i = 0; i < max && s[i] != '\0' && len_ < kCap; ++i) buf_[len_++] = s[i];
    return *this;
  }
  Line& Hex(std::uintptr_t v) noexcept {
    Str("0x");
    char tmp[2 * sizeof(v)];
    int n = 0;
    do {
      tmp[n++] = "0123456789abcdef"[v & 0xFu];
      v >>= 4;
    } while (v != 0);
    while (n > 0 && len_ < kCap) buf_[len_++] = tmp[--n];
    return *this;
  }
  Line& Dec(long long v) noexcept {
    unsigned long long u = static_cast<unsigned long long>(v);
    if (v < 0) {
      Str("-");
      u = 0ULL - u;
    }
    char tmp[24];
    int n = 0;
    do {
      tmp[n++] = static_cast<char>('0' + (u % 10));
      u /= 10;
    } while (u != 0);
    while (n > 0 && len_ < kCap) buf_[len_++] = tmp[--n];
    return *this;
  }
  void Emit() noexcept {
    buf_[len_++] = '\n';  // kCap leaves room for it
    WriteAll(buf_, len_);
    len_ = 0;
  }

 private:
  static constexpr std::size_t kCap = 1023;
  char buf_[kCap + 1];
  std::size_t len_ = 0;
};

const char* SignalName(int sig) noexcept {
  switch (sig) {
    case SIGSEGV: return "SIGSEGV";
    case SIGBUS:  return "SIGBUS";
    case SIGILL:  return "SIGILL";
    case SIGFPE:  return "SIGFPE";
    case SIGABRT: return "SIGABRT";
    default:      return "signal";
  }
}

int SignalIndex(int sig) noexcept {
  for (int i = 0; i < kNumSignals; ++i) {
    if (kSignals[i] == sig) return i;
  }
  return -1;
}

std::uintptr_t PcFromContext(void* uctx) noexcept {
  if (uctx == nullptr) return 0;
  const auto* uc = static_cast<const ucontext_t*>(uctx);
#if defined(__x86_64__)
  return static_cast<std::uintptr_t>(uc->uc_mcontext.gregs[REG_RIP]);
#elif defined(__aarch64__)
  return static_cast<std::uintptr_t>(uc->uc_mcontext.pc);
#else
  (void)uc;  // the gateway only targets x86_64 and aarch64
  return 0;
#endif
}

void AppendPlugin(Line& line, const Slot& slot) noexcept {
  line.Str("plugin '").StrN(slot.so_path, kNameMax).Str("' (key '")
      .StrN(slot.key, kNameMax).Str("')");
}

/* One line per registered plugin containing `addr` -- more than one when
   several instances share a .so mapping. */
void EmitPluginsContaining(Line& line, std::uintptr_t addr, const char* via) noexcept {
  for (const auto& slot : g_slots) {
    if (!IsLive(slot) || !SlotContains(slot, addr)) continue;
    line.Str("[Gateway] FATAL: ");
    AppendPlugin(line, slot);
    line.Str(" via=").Str(via);
    line.Emit();
  }
}

void Report(int sig, const siginfo_t* info, void* uctx) noexcept {
  Line line;
  const std::uintptr_t pc = PcFromContext(uctx);
  line.Str("[Gateway] FATAL: signal ").Dec(sig).Str(" (").Str(SignalName(sig))
      .Str(") thread ").Dec(static_cast<long long>(syscall(SYS_gettid)))
      .Str(" pc=").Hex(pc);
  if (info != nullptr) {
    line.Str(" addr=").Hex(reinterpret_cast<std::uintptr_t>(info->si_addr))
        .Str(" code=").Dec(info->si_code);
  }
  line.Emit();

  // Warmed up at install time, so this does not lazily load the unwinder here.
  void* frames[kMaxFrames];
  const int nframes = backtrace(frames, kMaxFrames);

  // 1. The faulting instruction itself is inside a plugin.
  bool attributed = false;
  bool pc_in_plugin = false;
  if (pc != 0 && AnyLiveSlotContains(pc)) {
    EmitPluginsContaining(line, pc, "pc");
    attributed = pc_in_plugin = true;
  }
  // 2. The innermost plugin frame on the stack: abort(), raise() and an
  //    exception escaping a callback all fault inside libc, called from a
  //    plugin. Return addresses point just past the call, so step back one
  //    byte to land inside the calling function.
  if (!attributed) {
    for (int i = 0; i < nframes; ++i) {
      std::uintptr_t addr = reinterpret_cast<std::uintptr_t>(frames[i]);
      if (addr != 0) --addr;
      if (AnyLiveSlotContains(addr)) {
        EmitPluginsContaining(line, addr, "stack");
        attributed = true;
        break;
      }
    }
  }
  // 3. The thread was inside a host -> plugin callback. Distinguishes
  //    instances that share a .so, and still attributes when the unwinder
  //    could not walk through the plugin's frames.
  const Slot* marker = LiveSlot(t_active_slot);
  const CallbackKind marker_kind = t_active_kind;
  if (!attributed && marker != nullptr) {
    line.Str("[Gateway] FATAL: ");
    AppendPlugin(line, *marker);
    line.Str(" via=callback");
    line.Emit();
    attributed = true;
  }
  if (attributed && !pc_in_plugin) {
    line.Str("[Gateway] FATAL: faulting pc is outside the plugin "
             "(host or system library code)").Emit();
  }
  if (marker != nullptr) {
    line.Str("[Gateway] FATAL: thread was inside ").Str(CallbackKindName(marker_kind))
        .Str(" of ");
    AppendPlugin(line, *marker);
    line.Emit();
  }
  if (!attributed) {
    line.Str("[Gateway] FATAL: not attributed to any loaded plugin").Emit();
  }
  line.Str("[Gateway] FATAL: backtrace:").Emit();
  if (nframes > 0) backtrace_symbols_fd(frames, nframes, STDERR_FILENO);
}

/* Hand the signal to whoever had it before us and let the process die by it,
   so exit status, core dumps and sanitizer reports are what they always were.
   A signal sent by kill/raise/tgkill (si_code <= 0, which includes abort()) is
   re-raised: it stays pending while this handler runs and is delivered to the
   restored disposition on return. A genuine hardware fault is not re-raised:
   returning re-executes the faulting instruction, which faults again into the
   restored disposition with its real siginfo and context. */
void ChainToPrevious(int sig, const siginfo_t* info) noexcept {
  const int idx = SignalIndex(sig);
  if (idx >= 0) {
    sigaction(sig, &g_previous[idx], nullptr);
  } else {
    signal(sig, SIG_DFL);
  }
  if (info == nullptr || info->si_code <= 0) raise(sig);
}

void FatalSignalHandler(int sig, siginfo_t* info, void* uctx) {
  const int saved_errno = errno;
  const long tid = syscall(SYS_gettid);
  long expected = 0;
  if (g_reporting_tid.compare_exchange_strong(expected, tid)) {
    Report(sig, info, uctx);
  } else if (expected != tid) {
    // Another thread is already reporting and will take the process down.
    // Give it time to finish writing before this thread does the same.
    timespec ts{0, 50 * 1000 * 1000};
    for (int i = 0; i < 100; ++i) nanosleep(&ts, nullptr);
  }
  // expected == tid: a second fatal signal raised while this thread was
  // reporting. Don't report again; just die.
  ChainToPrevious(sig, info);
  errno = saved_errno;
}

bool IsHandler(const struct sigaction& action) noexcept {
  if ((action.sa_flags & SA_SIGINFO) != 0) return action.sa_sigaction != nullptr;
  return action.sa_handler != SIG_DFL && action.sa_handler != SIG_IGN;
}

}  // namespace

/* ── Registry ─────────────────────────────────────────────────────────── */

int RegisterPluginImage(const std::string& key, const std::string& so_path,
                        const void* symbol_in_image) {
  Range ranges[kMaxRanges]{};
  int range_count = 0;
  if (symbol_in_image != nullptr) {
    FindImage find{reinterpret_cast<std::uintptr_t>(symbol_in_image), ranges, 0, false};
    dl_iterate_phdr(&FindImageCallback, &find);
    if (find.found) range_count = find.count;
  }

  {
    std::lock_guard<std::mutex> lock(g_register_mutex);
    for (int i = 0; i < kMaxPluginImages; ++i) {
      Slot& slot = g_slots[i];
      if (slot.state.load(std::memory_order_relaxed) != kFree) continue;
      slot.state.store(kWriting, std::memory_order_relaxed);
      CopyBounded(slot.key, key);
      CopyBounded(slot.so_path, so_path);
      std::copy(ranges, ranges + range_count, slot.ranges);
      slot.range_count = range_count;
      slot.state.store(kLive, std::memory_order_release);
      return i;
    }
  }
  if (!g_full_warned.exchange(true)) {
    std::fprintf(stderr,
                 "[Gateway] plugin crash attribution: registry full (%d images); "
                 "'%s' and any later plugin will not be attributed if it crashes\n",
                 kMaxPluginImages, key.c_str());
  }
  return -1;
}

void UnregisterPluginImage(int slot) noexcept {
  if (slot < 0 || slot >= kMaxPluginImages) return;
  // A single release store: only the slot's owner frees it, and only after
  // its registration completed, so no writer can be mid-fill here.
  g_slots[slot].state.store(kFree, std::memory_order_release);
}

/* ── Per-thread marker ────────────────────────────────────────────────── */

ActiveCallbackScope::ActiveCallbackScope(int slot, CallbackKind kind) noexcept
    : prev_slot_(t_active_slot), prev_kind_(t_active_kind) {
  if (!t_alt_stack_checked && g_installed.load(std::memory_order_relaxed)) {
    t_alt_stack_checked = true;
    t_alt_stack.Ensure();
  }
  t_active_kind = kind;
  t_active_slot = slot;
  // The handler runs on this thread: keep the compiler from sinking the
  // stores past the call into the plugin.
  std::atomic_signal_fence(std::memory_order_seq_cst);
}

ActiveCallbackScope::~ActiveCallbackScope() {
  std::atomic_signal_fence(std::memory_order_seq_cst);
  t_active_slot = prev_slot_;
  t_active_kind = prev_kind_;
}

/* ── Handler installation ─────────────────────────────────────────────── */

bool InstallFatalSignalHandler() {
  std::lock_guard<std::mutex> lock(g_install_mutex);
  if (g_installed.load(std::memory_order_acquire)) return true;

  // backtrace() loads libgcc's unwinder on first use, which allocates and
  // takes the loader lock. Do that here, not inside the handler.
  {
    void* warm[4];
    (void)backtrace(warm, 4);
  }

  struct sigaction action{};
  action.sa_sigaction = &FatalSignalHandler;
  sigemptyset(&action.sa_mask);
  action.sa_flags = SA_SIGINFO | SA_ONSTACK;
  for (int i = 0; i < kNumSignals; ++i) {
    if (sigaction(kSignals[i], &action, &g_previous[i]) != 0) {
      for (int j = 0; j < i; ++j) sigaction(kSignals[j], &g_previous[j], nullptr);
      return false;
    }
  }
  for (int i = 0; i < kNumSignals; ++i) {
    if (IsHandler(g_previous[i])) {
      std::fprintf(stderr,
                   "[Gateway] plugin crash attribution: chaining to a previously "
                   "installed %s handler\n",
                   SignalName(kSignals[i]));
    }
  }

  t_alt_stack_checked = true;
  t_alt_stack.Ensure();
  g_installed.store(true, std::memory_order_release);
  return true;
}

bool FatalSignalHandlerInstalled() noexcept {
  return g_installed.load(std::memory_order_acquire);
}

/* ── Introspection ────────────────────────────────────────────────────── */

std::vector<std::string> AttributeAddress(std::uintptr_t addr) {
  std::vector<std::string> keys;
  for (const auto& slot : g_slots) {
    if (IsLive(slot) && SlotContains(slot, addr)) keys.emplace_back(slot.key);
  }
  return keys;
}

std::vector<std::string> RegisteredKeys() {
  std::vector<std::string> keys;
  for (const auto& slot : g_slots) {
    if (IsLive(slot)) keys.emplace_back(slot.key);
  }
  return keys;
}

ActiveCallback CurrentCallback() {
  ActiveCallback current;
  current.slot = t_active_slot;
  current.kind = t_active_kind;
  if (const Slot* slot = LiveSlot(current.slot)) current.key = slot->key;
  return current;
}

const char* CallbackKindName(CallbackKind kind) noexcept {
  switch (kind) {
    case CallbackKind::kLoad:    return "load";
    case CallbackKind::kOnTick:  return "on_tick";
    case CallbackKind::kOnFrame: return "on_frame";
    case CallbackKind::kDestroy: return "destroy";
    case CallbackKind::kNone:    break;
  }
  return "none";
}

}  // namespace boat::core::crash
