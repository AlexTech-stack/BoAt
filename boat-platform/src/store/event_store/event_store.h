// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <vector>

struct sqlite3;

namespace boat::store {

struct EventRecord {
  std::string id;
  std::string simulation_id;
  std::uint64_t tick;
  std::int64_t wall_time_ns;
  std::string signal_id;
  int value_type;
  std::vector<std::uint8_t> value_blob;
  std::string tags;
};

struct EventFilter {
  std::optional<std::string> simulation_id;
  std::optional<std::string> signal_id;
  std::optional<std::uint64_t> tick_min;
  std::optional<std::uint64_t> tick_max;
};

class IEventStore {
 public:
  virtual ~IEventStore() = default;
  virtual void InsertBatch(std::span<const EventRecord> events) = 0;
  virtual std::vector<EventRecord> Query(const EventFilter& filter) = 0;

  /* Wait until every accepted write has reached the backing store. The
   * default is a no-op, which is correct for a synchronous implementation. */
  virtual void Flush() {}

  /* Cumulative queue drops / write failures. Synchronous implementations
   * have neither and keep the default zeros. */
  [[nodiscard]] virtual std::uint64_t DroppedEventCount() const { return 0; }
  [[nodiscard]] virtual std::uint64_t WriteErrorCount() const { return 0; }
};

class SqliteEventStore : public IEventStore {
 public:
  explicit SqliteEventStore(std::string_view db_path);
  ~SqliteEventStore() override;

  void InsertBatch(std::span<const EventRecord> events) override;
  std::vector<EventRecord> Query(const EventFilter& filter) override;

 private:
  sqlite3* db_{nullptr};
};

}  // namespace boat::store
