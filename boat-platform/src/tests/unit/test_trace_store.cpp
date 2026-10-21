// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

/* FlatFileTraceStore::MapTrace handle lifetimes (issue #63).

   The store used to track one mapping per trace id, so a second mapping of the
   same id displaced the first and the next unmap-by-id tore down a mapping
   another caller was still reading. Handles now own their mapping outright;
   these tests pin that each one stays readable for exactly as long as it is
   held, independent of every other handle and of the store itself. */

#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <span>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <unistd.h>

#include "trace_store/trace_store.h"

using boat::store::FlatFileTraceStore;
using boat::store::MappedTracePtr;
using boat::store::TraceRecord;

namespace {

std::vector<std::uint8_t> Pattern(std::size_t size, std::uint8_t seed) {
  std::vector<std::uint8_t> bytes(size);
  for (std::size_t i = 0; i < size; ++i) {
    bytes[i] = static_cast<std::uint8_t>((i * 7 + seed) & 0xFF);
  }
  return bytes;
}

bool HandleHolds(const MappedTracePtr& handle, const std::vector<std::uint8_t>& expected) {
  return handle != nullptr && handle->bytes.size() == expected.size() &&
         std::memcmp(handle->bytes.data(), expected.data(), expected.size()) == 0;
}

/* Temp index + trace files, removed again on scope exit. */
struct TempTraces {
  std::string index_db;
  std::vector<std::string> files;

  explicit TempTraces(const std::string& prefix)
      : index_db((std::filesystem::temp_directory_path() /
                  (prefix + "_" + std::to_string(::getpid()) + "_traces.db")).string()) {
    std::filesystem::remove(index_db);
  }
  ~TempTraces() {
    std::filesystem::remove(index_db);
    for (const auto& f : files) std::filesystem::remove(f);
  }

  void Write(FlatFileTraceStore& store, const std::string& id,
             const std::vector<std::uint8_t>& bytes) {
    const auto path = (std::filesystem::temp_directory_path() /
                       (id + "_" + std::to_string(::getpid()) + "_trace.bin")).string();
    files.push_back(path);
    TraceRecord meta{};
    meta.id = id;
    meta.storage_path = path;
    meta.format = TraceRecord::Format::BINARY;
    store.WriteTrace(meta, std::span<const std::uint8_t>(bytes));
  }
};

}  // namespace

TEST_CASE("TraceStore MapTrace handles are independent: releasing one leaves the other readable",
          "[unit][trace_store]") {
  TempTraces tmp("boat_ts_indep");
  FlatFileTraceStore store(tmp.index_db);
  const auto bytes = Pattern(256 * 1024, 3);
  tmp.Write(store, "ts_indep", bytes);

  // The pre-#63 failure: the second mapping replaced the first in a per-id
  // table, and releasing "the id" unmapped whichever came last.
  auto first = store.MapTrace("ts_indep");
  auto second = store.MapTrace("ts_indep");
  REQUIRE(first->bytes.data() != second->bytes.data());

  second.reset();
  REQUIRE(HandleHolds(first, bytes));

  auto third = store.MapTrace("ts_indep");
  first.reset();
  REQUIRE(HandleHolds(third, bytes));
}

TEST_CASE("TraceStore MapTrace handle outlives the store", "[unit][trace_store]") {
  TempTraces tmp("boat_ts_outlive");
  const auto bytes = Pattern(4096, 9);
  MappedTracePtr handle;
  {
    FlatFileTraceStore store(tmp.index_db);
    tmp.Write(store, "ts_outlive", bytes);
    handle = store.MapTrace("ts_outlive");
  }
  REQUIRE(HandleHolds(handle, bytes));
}

TEST_CASE("TraceStore rewriting a trace keeps live handles on the old bytes",
          "[unit][trace_store]") {
  TempTraces tmp("boat_ts_rewrite");
  FlatFileTraceStore store(tmp.index_db);
  const auto old_bytes = Pattern(8192, 1);
  const auto new_bytes = Pattern(4096, 2);
  tmp.Write(store, "ts_rewrite", old_bytes);
  auto old_handle = store.MapTrace("ts_rewrite");

  // WriteTrace unlinks before recreating, so the live mapping keeps the old
  // inode instead of seeing a truncated file (which would SIGBUS on read).
  tmp.Write(store, "ts_rewrite", new_bytes);
  auto new_handle = store.MapTrace("ts_rewrite");

  REQUIRE(HandleHolds(old_handle, old_bytes));
  REQUIRE(HandleHolds(new_handle, new_bytes));
}

TEST_CASE("TraceStore MapTrace of an empty trace returns an empty handle",
          "[unit][trace_store]") {
  TempTraces tmp("boat_ts_empty");
  FlatFileTraceStore store(tmp.index_db);
  tmp.Write(store, "ts_empty", {});

  const auto handle = store.MapTrace("ts_empty");
  REQUIRE(handle != nullptr);
  REQUIRE(handle->bytes.empty());
}

TEST_CASE("TraceStore MapTrace throws for an unknown trace id", "[unit][trace_store]") {
  TempTraces tmp("boat_ts_unknown");
  FlatFileTraceStore store(tmp.index_db);
  REQUIRE_THROWS_AS(store.MapTrace("ts_no_such_trace"), std::runtime_error);
}

TEST_CASE("TraceStore concurrent MapTrace and release keep every handle readable",
          "[unit][trace_store][concurrency]") {
  constexpr int kThreads = 8;
  constexpr int kIterations = 500;

  TempTraces tmp("boat_ts_concurrent");
  FlatFileTraceStore store(tmp.index_db);
  const auto bytes = Pattern(64 * 1024, 5);
  tmp.Write(store, "ts_concurrent", bytes);

  std::atomic<int> failures{0};
  std::vector<std::thread> threads;
  for (int t = 0; t < kThreads; ++t) {
    threads.emplace_back([&] {
      for (int i = 0; i < kIterations; ++i) {
        const auto handle = store.MapTrace("ts_concurrent");
        if (!HandleHolds(handle, bytes)) ++failures;
      }
    });
  }
  for (auto& th : threads) th.join();
  REQUIRE(failures.load() == 0);
}
