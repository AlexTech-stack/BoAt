// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* Plugin crash attribution (issue #19), end to end: each case spawns a process
   that loads the crash_plugin fixture with the gateway's fatal-signal handler
   installed, lets it crash, and checks what it said before dying and how it
   died. Attribution, not containment -- every one of these processes still
   dies, which is the point the exit-status assertions make.

   Headless: no vcan, no hardware, no HIL. The boat_gateway cases run the real
   binary with no interfaces configured, on a free port, in a scratch
   directory. */

#include <catch2/catch_test_macros.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <thread>
#include <vector>

extern char** environ;

#ifndef CRASH_PLUGIN_SO
#error "CRASH_PLUGIN_SO must point at the crash_plugin fixture"
#endif
#ifndef CRASH_HOST_BIN
#error "CRASH_HOST_BIN must point at boat_crash_host"
#endif

// Sanitizer runtimes install their own fatal-signal handlers, which ours chains
// to; they may _exit(1) rather than re-raise. What we print is asserted in
// every build, how the process died only in non-sanitizer builds.
#if defined(__SANITIZE_ADDRESS__) || defined(__SANITIZE_THREAD__)
#define BOAT_UNDER_SANITIZER 1
#endif
#if !defined(BOAT_UNDER_SANITIZER) && defined(__has_feature)
#if __has_feature(address_sanitizer) || __has_feature(thread_sanitizer)
#define BOAT_UNDER_SANITIZER 1
#endif
#endif
#ifndef BOAT_UNDER_SANITIZER
#define BOAT_UNDER_SANITIZER 0
#endif

namespace {

const std::string kSo = CRASH_PLUGIN_SO;

/* A child process with stdout+stderr on one pipe. fork/exec rather than
   posix_spawn so the child can chdir (posix_spawn_file_actions_addchdir_np
   needs glibc 2.29; the portable build is 2.28). Everything the child needs
   is built before fork, so it only makes async-signal-safe calls. */
class Child {
 public:
  Child() = default;
  Child(const Child&) = delete;
  Child& operator=(const Child&) = delete;
  ~Child() {
    if (pid_ > 0 && !reaped_) {
      kill(pid_, SIGKILL);
      int status = 0;
      waitpid(pid_, &status, 0);
    }
    if (fd_ >= 0) close(fd_);
  }

  /* env_changes: "KEY=VALUE" sets, a bare "KEY" removes. */
  bool Spawn(const std::vector<std::string>& args,
             const std::vector<std::string>& env_changes = {},
             const std::string& cwd = "") {
    std::vector<std::string> env;
    const auto key_of = [](const std::string& s) { return s.substr(0, s.find('=')); };
    for (char** e = environ; *e != nullptr; ++e) {
      const std::string entry(*e);
      bool changed = false;
      for (const auto& c : env_changes) changed = changed || key_of(c) == key_of(entry);
      if (!changed) env.push_back(entry);
    }
    for (const auto& c : env_changes) {
      if (c.find('=') != std::string::npos) env.push_back(c);
    }
    std::vector<char*> argv;
    for (const auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
    argv.push_back(nullptr);
    std::vector<char*> envp;
    for (auto& e : env) envp.push_back(e.data());
    envp.push_back(nullptr);

    int fds[2];
    if (pipe(fds) != 0) return false;
    pid_ = fork();
    if (pid_ < 0) {
      close(fds[0]);
      close(fds[1]);
      return false;
    }
    if (pid_ == 0) {
      dup2(fds[1], STDOUT_FILENO);
      dup2(fds[1], STDERR_FILENO);
      close(fds[0]);
      close(fds[1]);
      if (!cwd.empty() && chdir(cwd.c_str()) != 0) _exit(126);
      execve(argv[0], argv.data(), envp.data());
      _exit(127);
    }
    close(fds[1]);
    fd_ = fds[0];
    return true;
  }

  /* Reads until `needle` has appeared (true), EOF, or the timeout (false). */
  bool ReadUntil(const std::string& needle, int timeout_ms) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    while (output_.find(needle) == std::string::npos) {
      if (!ReadSome(deadline)) return output_.find(needle) != std::string::npos;
    }
    return true;
  }

  /* Reads until EOF (true) or the timeout (false). */
  bool ReadToEnd(int timeout_ms) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    while (!eof_) {
      if (!ReadSome(deadline) && !eof_) return false;
    }
    return true;
  }

  /* waitpid with a timeout; SIGKILLs and reports timed_out on expiry. */
  int Wait(int timeout_ms, bool* timed_out) {
    *timed_out = false;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    int status = 0;
    while (true) {
      const pid_t r = waitpid(pid_, &status, WNOHANG);
      if (r == pid_) break;
      if (std::chrono::steady_clock::now() >= deadline) {
        *timed_out = true;
        kill(pid_, SIGKILL);
        waitpid(pid_, &status, 0);
        break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    reaped_ = true;
    return status;
  }

  void Signal(int sig) const {
    if (pid_ > 0) kill(pid_, sig);
  }
  [[nodiscard]] const std::string& output() const { return output_; }

 private:
  bool ReadSome(std::chrono::steady_clock::time_point deadline) {
    if (eof_) return false;
    const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
        deadline - std::chrono::steady_clock::now()).count();
    if (remaining <= 0) return false;
    pollfd p{fd_, POLLIN, 0};
    const int pr = poll(&p, 1, static_cast<int>(remaining));
    if (pr < 0) return errno == EINTR;
    if (pr == 0) return false;
    char buf[4096];
    const ssize_t n = read(fd_, buf, sizeof(buf));
    if (n < 0) return errno == EINTR;
    if (n == 0) {
      eof_ = true;
      return false;
    }
    output_.append(buf, static_cast<std::size_t>(n));
    return true;
  }

  pid_t pid_ = -1;
  int fd_ = -1;
  bool eof_ = false;
  bool reaped_ = false;
  std::string output_;
};

struct Outcome {
  std::string output;
  int status = 0;
  bool timed_out = false;
};

Outcome RunHost(const std::string& config, const std::string& action) {
  Child child;
  REQUIRE(child.Spawn({CRASH_HOST_BIN, kSo, config, action}));
  child.ReadToEnd(60000);
  Outcome o;
  o.status = child.Wait(10000, &o.timed_out);
  o.output = child.output();
  return o;
}

void RequireDiedBy(const Outcome& o, int sig) {
  INFO("output:\n" << o.output);
  REQUIRE_FALSE(o.timed_out);
  REQUIRE_FALSE((WIFEXITED(o.status) && WEXITSTATUS(o.status) == 0));
  if (!BOAT_UNDER_SANITIZER) {
    // Chained and re-raised: the process dies by the original signal, so
    // exit status and core dumps are what they were before attribution.
    REQUIRE(WIFSIGNALED(o.status));
    REQUIRE(WTERMSIG(o.status) == sig);
  }
}

bool Has(const Outcome& o, const std::string& s) { return o.output.find(s) != std::string::npos; }

std::string PluginLine(const std::string& key) {
  return "plugin '" + kSo + "' (key '" + key + "')";
}

}  // namespace

TEST_CASE("A segfault in on_tick is attributed to the plugin by its pc",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"segv_on_tick"})", "tick");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "[Gateway] FATAL: signal 11 (SIGSEGV)"));
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via=pc"));
  CHECK(Has(o, "thread was inside on_tick of " + PluginLine(kSo)));
  CHECK_FALSE(Has(o, "not attributed"));
  RequireDiedBy(o, SIGSEGV);
}

TEST_CASE("abort() in on_tick is attributed although its pc is in libc",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"abort_on_tick"})", "tick");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "(SIGABRT)"));
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via="));
  CHECK(Has(o, "faulting pc is outside the plugin"));
  CHECK(Has(o, "thread was inside on_tick of " + PluginLine(kSo)));
  RequireDiedBy(o, SIGABRT);
}

TEST_CASE("An exception escaping on_tick is attributed to the plugin",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"throw_on_tick"})", "tick");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "(SIGABRT)"));
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via="));
  CHECK(Has(o, "thread was inside on_tick of " + PluginLine(kSo)));
  RequireDiedBy(o, SIGABRT);
}

TEST_CASE("A crash inside initialize() is attributed to the plugin being loaded",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"abort_in_initialize"})", "load");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via="));
  CHECK(Has(o, "thread was inside load of " + PluginLine(kSo)));
  RequireDiedBy(o, SIGABRT);
}

TEST_CASE("A segfault in on_frame is attributed to the plugin",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"segv_on_frame"})", "frame");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via=pc"));
  CHECK(Has(o, "thread was inside on_frame of " + PluginLine(kSo)));
  RequireDiedBy(o, SIGSEGV);
}

TEST_CASE("A segfault on a plugin-owned thread is attributed by pc alone",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"segv_on_own_thread"})", "tick");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via=pc"));
  // The host never called into the plugin on that thread.
  CHECK_FALSE(Has(o, "thread was inside"));
  RequireDiedBy(o, SIGSEGV);
}

TEST_CASE("A fault in host code reached from a plugin callback names the plugin",
          "[integration][plugin_crash]") {
  // The plugin hands the host's frame publisher a garbage payload pointer:
  // the fault is in host code, with the plugin's on_tick on the stack.
  const auto o = RunHost(R"({"mode":"bad_publish_on_tick"})", "tick");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via="));
  CHECK_FALSE(Has(o, " via=pc"));
  CHECK(Has(o, "faulting pc is outside the plugin"));
  CHECK(Has(o, "thread was inside on_tick of " + PluginLine(kSo)));
  RequireDiedBy(o, SIGSEGV);
}

TEST_CASE("raise(SIGSEGV) from a plugin is attributed and still kills by SIGSEGV",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"raise_on_tick"})", "tick");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via="));
  CHECK(Has(o, "thread was inside on_tick of " + PluginLine(kSo)));
  RequireDiedBy(o, SIGSEGV);
}

TEST_CASE("A stack overflow in on_tick is attributed via the alternate stack",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"recurse_on_tick"})", "tick");
  INFO("output:\n" << o.output);
  if (!BOAT_UNDER_SANITIZER) {
    CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via=pc"));
  }
  RequireDiedBy(o, SIGSEGV);
}

TEST_CASE("The callback marker distinguishes instances sharing one .so",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"abort_on_tick","iface":"vcan7"})", "tick");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "thread was inside on_tick of " + PluginLine(kSo + "?iface=vcan7")));
  RequireDiedBy(o, SIGABRT);
}

TEST_CASE("A fault with no plugin involved is reported as unattributed",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"none"})", "host_segv");
  INFO("output:\n" << o.output);
  CHECK(Has(o, "[Gateway] FATAL: not attributed to any loaded plugin"));
  CHECK_FALSE(Has(o, " via="));
  CHECK_FALSE(Has(o, "thread was inside"));
  RequireDiedBy(o, SIGSEGV);
}

TEST_CASE("A plugin that does not crash leaves no FATAL output",
          "[integration][plugin_crash]") {
  const auto o = RunHost(R"({"mode":"none"})", "tick");
  INFO("output:\n" << o.output);
  REQUIRE_FALSE(o.timed_out);
  CHECK(Has(o, "[crash_host] completed"));
  CHECK_FALSE(Has(o, "FATAL"));
  REQUIRE(WIFEXITED(o.status));
  REQUIRE(WEXITSTATUS(o.status) == 0);
}

#ifdef BOAT_GATEWAY_BIN

namespace {

int FreeTcpPort() {
  const int fd = socket(AF_INET, SOCK_STREAM, 0);
  REQUIRE(fd >= 0);
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  addr.sin_port = 0;
  REQUIRE(bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0);
  socklen_t len = sizeof(addr);
  REQUIRE(getsockname(fd, reinterpret_cast<sockaddr*>(&addr), &len) == 0);
  close(fd);
  return ntohs(addr.sin_port);
}

/* The gateway writes its SQLite stores into the working directory. */
struct ScratchDir {
  std::string path;
  ScratchDir() {
    char tmpl[] = "/tmp/boat_plugin_crash_XXXXXX";
    const char* p = mkdtemp(tmpl);
    REQUIRE(p != nullptr);
    path = p;
  }
  ~ScratchDir() {
    std::error_code ec;
    std::filesystem::remove_all(path, ec);
  }
};

std::vector<std::string> GatewayEnv(const std::string& plugin_config, int port) {
  return {
      "BOAT_CAN_INTERFACES=",  // empty: no CAN interfaces (unset means vcan0)
      "BOAT_ETH_INTERFACES",
      "BOAT_NODE_PLUGINS=" + kSo + "?" + plugin_config,
      "BOAT_GRPC_PORT=" + std::to_string(port),
      // Inherited settings that would change or break the run.
      "BOAT_TLS_CERT", "BOAT_TLS_KEY", "BOAT_TLS_CLIENT_CA", "BOAT_CONFIG_DUMP",
      "BOAT_NODE_TICK_US", "BOAT_NODE_TICK_MS", "BOAT_TIME_SOURCE",
  };
}

}  // namespace

TEST_CASE("boat_gateway names the plugin that aborted it",
          "[integration][plugin_crash]") {
  ScratchDir dir;
  Child gateway;
  // ~5 s of 1 ms ticks: late enough that the gRPC server is up, so this is
  // the whole gateway going down -- server, clients and all -- not a
  // startup failure.
  REQUIRE(gateway.Spawn({BOAT_GATEWAY_BIN},
                        GatewayEnv(R"({"mode":"abort_on_tick","after_ticks":5000})",
                                   FreeTcpPort()),
                        dir.path));
  gateway.ReadToEnd(60000);  // inside the 120 s debug-preset ctest timeout
  Outcome o;
  o.status = gateway.Wait(10000, &o.timed_out);
  o.output = gateway.output();
  INFO("output:\n" << o.output);
  CHECK(Has(o, "[Gateway] Loaded plugin '" + kSo + "'"));
  CHECK(Has(o, "gRPC server listening"));
  CHECK(Has(o, "[Gateway] FATAL: " + PluginLine(kSo) + " via="));
  CHECK(Has(o, "thread was inside on_tick of " + PluginLine(kSo)));
  RequireDiedBy(o, SIGABRT);
}

TEST_CASE("boat_gateway still shuts down cleanly on SIGTERM",
          "[integration][plugin_crash]") {
  ScratchDir dir;
  Child gateway;
  REQUIRE(gateway.Spawn({BOAT_GATEWAY_BIN},
                        GatewayEnv(R"({"mode":"none"})", FreeTcpPort()), dir.path));
  const bool listening = gateway.ReadUntil("gRPC server listening", 60000);
  INFO("output:\n" << gateway.output());
  REQUIRE(listening);
  gateway.Signal(SIGTERM);
  gateway.ReadToEnd(30000);
  bool timed_out = false;
  const int status = gateway.Wait(10000, &timed_out);
  INFO("output:\n" << gateway.output());
  REQUIRE_FALSE(timed_out);
  CHECK(gateway.output().find("FATAL") == std::string::npos);
  if (!BOAT_UNDER_SANITIZER) {
    REQUIRE(WIFEXITED(status));
    REQUIRE(WEXITSTATUS(status) == 0);
  }
}

#endif  // BOAT_GATEWAY_BIN
