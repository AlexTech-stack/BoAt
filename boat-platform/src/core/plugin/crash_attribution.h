// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <string>
#include <vector>

/* Plugin crash ATTRIBUTION -- not containment (issue #19).
 *
 * Plugins are dlopen()'d into the gateway, so a SIGSEGV or abort() in any of
 * them still takes the whole process down. What this module changes is what
 * the operator is left with: before dying, the gateway writes which plugin's
 * code was executing, instead of just "the gateway died".
 *
 * Three pieces:
 *
 *   - An image registry. PluginManager::Load records each plugin's mapped
 *     PT_LOAD ranges; DestroyHandle clears them before dlclose. Fixed-size and
 *     preallocated, so the signal handler can read it without malloc, locks
 *     or dladdr (which takes the loader lock and is not async-signal-safe).
 *     Address ranges identify a .so, not an instance: two instances of one
 *     .so share a mapping, and an address in it attributes to both.
 *
 *   - A per-thread marker, set by ActiveCallbackScope around every host call
 *     into a plugin. This is what attributes abort(), an exception escaping a
 *     callback, or a fault in host code a plugin called back into -- cases
 *     where the faulting PC is not inside the plugin at all.
 *
 *   - A fatal-signal handler (SIGSEGV/SIGBUS/SIGILL/SIGFPE/SIGABRT) that
 *     reports, then restores the previous disposition and lets the process die
 *     by the original signal, so exit status, core dumps and sanitizer reports
 *     are unchanged. Installed only by the gateway's main(): library code does
 *     not take over process signal dispositions on its own.
 *
 * Everything here is best-effort diagnostics. In-process code can replace the
 * handler or corrupt the registry; this is not a security boundary.
 */
namespace boat::core::crash {

enum class CallbackKind : std::uint8_t {
  kNone = 0,
  kLoad,     // create / initialize / declared_buses / service symbols / set_*
  kOnTick,
  kOnFrame,
  kDestroy,
};

inline constexpr int kMaxPluginImages = 64;

/* Records a loaded plugin image. `symbol_in_image` is any address inside the
   .so (the dlsym'd boat_plugin_create); the PT_LOAD ranges of the object that
   contains it are captured. A null or unresolvable symbol still claims a slot
   (so the per-thread marker can name the plugin) with no address ranges.
   Returns the slot, or -1 when the registry is full -- which degrades
   attribution only and must never fail a load. Not async-signal-safe. */
int RegisterPluginImage(const std::string& key, const std::string& so_path,
                        const void* symbol_in_image);

/* Frees a slot. Call before dlclose, so a later mapping at the same addresses
   cannot be blamed on this plugin. -1 is a no-op. */
void UnregisterPluginImage(int slot) noexcept;

/* Marks the calling thread as inside `kind` of the plugin in `slot` until
   destruction, restoring whatever was marked before (callbacks nest: a
   plugin's on_tick can publish a frame that is dispatched to another plugin's
   on_frame on the same thread). Once the handler is installed, the first scope
   on a thread also gives that thread an alternate signal stack, so a stack
   overflow inside a plugin can still be reported. */
class ActiveCallbackScope {
 public:
  ActiveCallbackScope(int slot, CallbackKind kind) noexcept;
  ~ActiveCallbackScope();
  ActiveCallbackScope(const ActiveCallbackScope&) = delete;
  ActiveCallbackScope& operator=(const ActiveCallbackScope&) = delete;

 private:
  int prev_slot_;
  CallbackKind prev_kind_;
};

/* Installs the fatal-signal handler, chaining to whatever was installed
   before (a sanitizer's, typically). Idempotent. Returns false if sigaction
   failed; the caller should record that rather than run unattributed
   silently. */
bool InstallFatalSignalHandler();
[[nodiscard]] bool FatalSignalHandlerInstalled() noexcept;

/* ── Introspection (normal context only, for tests and diagnostics) ───── */

/* Keys of every registered plugin whose ranges contain `addr`. */
[[nodiscard]] std::vector<std::string> AttributeAddress(std::uintptr_t addr);

/* Keys of every currently registered plugin. */
[[nodiscard]] std::vector<std::string> RegisteredKeys();

struct ActiveCallback {
  int slot = -1;
  CallbackKind kind = CallbackKind::kNone;
  std::string key;  // "" if slot is -1 or no longer registered
};
[[nodiscard]] ActiveCallback CurrentCallback();

[[nodiscard]] const char* CallbackKindName(CallbackKind kind) noexcept;

}  // namespace boat::core::crash
