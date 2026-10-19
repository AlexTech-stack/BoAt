// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* A plugin that crashes on purpose, so plugin crash attribution (issue #19)
   can be tested rather than assumed. Test-only: built by src/tests, never
   installed or packaged.

   Config keys (a flat substring scan, like the other in-tree parsers):
     "mode"        none | publish_on_tick | segv_on_tick | abort_on_tick |
                   throw_on_tick | raise_on_tick | recurse_on_tick |
                   bad_publish_on_tick | segv_on_own_thread | segv_on_frame |
                   abort_in_initialize | fail_initialize
     "after_ticks" crash on the first tick > N (default 0, i.e. tick one)
     "iface"       unused by the plugin; makes PluginManager key two
                   instances of this .so separately */

#include <boat/plugin.h>

#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

enum class Mode {
  kNone,
  kPublishOnTick,
  kSegvOnTick,
  kAbortOnTick,
  kThrowOnTick,
  kRaiseOnTick,
  kRecurseOnTick,
  kBadPublishOnTick,
  kSegvOnOwnThread,
  kSegvOnFrame,
  kAbortInInitialize,
  kFailInitialize,
};

struct Ctx {
  Mode mode = Mode::kNone;
  std::uint64_t after_ticks = 0;
  std::uint64_t ticks = 0;
  BoatFramePublishFn publish = nullptr;
  void* publish_ctx = nullptr;
};

/* Faults through an address read from a volatile, not a literal null: GCC's
   -fisolate-erroneous-paths-dereference turns a provable null dereference
   into a trap instruction, which would raise SIGILL instead of SIGSEGV. */
volatile std::uintptr_t g_bad_address = 8;

[[gnu::noinline]] void Segv() {
  *reinterpret_cast<volatile int*>(g_bad_address) = 1;
}

/* Not a tail call (the result is used after the recursive call returns), and
   the volatile buffer keeps each frame from being optimised away. The bound
   is unreachable in practice; it only keeps -Winfinite-recursion quiet. */
[[gnu::noinline]] int Recurse(int depth) {
  volatile char buf[1024];
  buf[0] = static_cast<char>(depth);
  if (depth > (1 << 30)) return buf[0];
  return Recurse(depth + 1) + buf[0];
}

std::string ConfigString(const char* config, const char* key) {
  if (config == nullptr) return "";
  const std::string s(config);
  const std::string quoted = std::string("\"") + key + "\"";
  auto pos = s.find(quoted);
  if (pos == std::string::npos) return "";
  pos = s.find('"', pos + quoted.size());
  if (pos == std::string::npos) return "";
  const auto end = s.find('"', pos + 1);
  if (end == std::string::npos) return "";
  return s.substr(pos + 1, end - pos - 1);
}

std::uint64_t ConfigUint(const char* config, const char* key) {
  if (config == nullptr) return 0;
  const std::string s(config);
  const std::string quoted = std::string("\"") + key + "\"";
  auto pos = s.find(quoted);
  if (pos == std::string::npos) return 0;
  pos = s.find(':', pos + quoted.size());
  if (pos == std::string::npos) return 0;
  return std::strtoull(s.c_str() + pos + 1, nullptr, 10);
}

Mode ParseMode(const std::string& m) {
  if (m == "publish_on_tick") return Mode::kPublishOnTick;
  if (m == "segv_on_tick") return Mode::kSegvOnTick;
  if (m == "abort_on_tick") return Mode::kAbortOnTick;
  if (m == "throw_on_tick") return Mode::kThrowOnTick;
  if (m == "raise_on_tick") return Mode::kRaiseOnTick;
  if (m == "recurse_on_tick") return Mode::kRecurseOnTick;
  if (m == "bad_publish_on_tick") return Mode::kBadPublishOnTick;
  if (m == "segv_on_own_thread") return Mode::kSegvOnOwnThread;
  if (m == "segv_on_frame") return Mode::kSegvOnFrame;
  if (m == "abort_in_initialize") return Mode::kAbortInInitialize;
  if (m == "fail_initialize") return Mode::kFailInitialize;
  return Mode::kNone;
}

void Publish(Ctx* c, std::uint8_t* payload, std::size_t len) {
  if (c->publish == nullptr) return;
  BoatFrame f{};
  f.bus_type = BOAT_BUS_CAN;
  f.payload = payload;
  f.payload_len = len;
  c->publish(c->publish_ctx, &f);
}

int Initialize(void* ctx, const char* config_json) {
  auto* c = static_cast<Ctx*>(ctx);
  c->mode = ParseMode(ConfigString(config_json, "mode"));
  c->after_ticks = ConfigUint(config_json, "after_ticks");
  if (c->mode == Mode::kAbortInInitialize) std::abort();
  return c->mode == Mode::kFailInitialize ? -1 : 0;
}

void OnTick(void* ctx, std::uint64_t /*tick*/) {
  auto* c = static_cast<Ctx*>(ctx);
  if (++c->ticks <= c->after_ticks) return;
  switch (c->mode) {
    case Mode::kPublishOnTick: {
      std::uint8_t data[4] = {1, 2, 3, 4};
      Publish(c, data, sizeof(data));
      break;
    }
    case Mode::kSegvOnTick:
      Segv();
      break;
    case Mode::kAbortOnTick:
      std::abort();
    case Mode::kThrowOnTick:
      // Escapes on_tick into the host, which has no handler for it:
      // std::terminate -> abort().
      throw std::runtime_error("crash_plugin: throw_on_tick");
    case Mode::kRaiseOnTick:
      std::raise(SIGSEGV);
      break;
    case Mode::kRecurseOnTick:
      (void)Recurse(0);
      break;
    case Mode::kBadPublishOnTick:
      // A garbage payload pointer handed to the host: the fault happens in
      // host code, while this plugin's on_tick is on the stack.
      Publish(c, reinterpret_cast<std::uint8_t*>(g_bad_address), 64);
      break;
    case Mode::kSegvOnOwnThread: {
      // Like the TCP plugin's raw_rx_thread: a thread the host never calls
      // into, so no host callback marker is set on it.
      std::thread t([] { Segv(); });
      t.join();
      break;
    }
    default:
      break;
  }
}

void OnFrame(void* ctx, const BoatFrame* /*frame*/) {
  auto* c = static_cast<Ctx*>(ctx);
  if (c->mode == Mode::kSegvOnFrame) Segv();
}

void Shutdown(void* /*ctx*/) {}

void SetFramePublisher(void* ctx, BoatFramePublishFn fn, void* publisher_ctx) {
  auto* c = static_cast<Ctx*>(ctx);
  c->publish = fn;
  c->publish_ctx = publisher_ctx;
}

BoatPluginVTable gVTable = [] {
  BoatPluginVTable vt{};
  vt.initialize          = &Initialize;
  vt.on_tick             = &OnTick;
  vt.shutdown            = &Shutdown;
  vt.on_frame            = &OnFrame;
  vt.set_frame_publisher = &SetFramePublisher;
  return vt;
}();

}  // namespace

extern "C" BoatPlugin* boat_plugin_create() {
  auto* plugin = new BoatPlugin{};
  plugin->vtable = &gVTable;
  plugin->ctx    = new Ctx{};
  return plugin;
}

extern "C" void boat_plugin_destroy(BoatPlugin* plugin) {
  if (plugin == nullptr) return;
  delete static_cast<Ctx*>(plugin->ctx);
  delete plugin;
}

extern "C" uint32_t boat_plugin_abi_version() { return BOAT_PLUGIN_ABI_VERSION; }
