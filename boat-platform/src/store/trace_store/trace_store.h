// Copyright 2026 Alexander Günther
// SPDX-License-Identifier: Apache-2.0

#pragma once

#include <cctype>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <vector>

struct sqlite3;

namespace boat::store {

// Trace ids become path components under /tmp. Only characters that cannot
// escape the directory are accepted; "." and ".." are rejected explicitly.
inline bool IsValidTraceId(std::string_view id) {
  if (id.empty() || id.size() > 128) return false;
  if (id == "." || id == "..") return false;
  for (const char ch : id) {
    const auto c = static_cast<unsigned char>(ch);
    if (!(std::isalnum(c) || c == '.' || c == '_' || c == '-')) return false;
  }
  return true;
}

struct __attribute__((packed)) TraceRecordHeader {
  std::uint32_t magic;
  std::uint32_t event_type;
  std::uint64_t tick;
  std::int64_t wall_time_ns;
  std::uint32_t payload_size;
};

struct TraceRecord {
  // Only BINARY is implemented. The former MF4/CSV placeholders were removed
  // rather than left as values that no writer or reader supports (issue #29).
  enum class Format : int {
    BINARY = 0,
  };

  std::string id;
  std::string simulation_id;
  std::uint64_t start_tick;
  std::uint64_t end_tick;
  Format format;
  std::string storage_path;
  std::uint64_t size_bytes;
};

/* A read-only view of one stored trace's bytes. */
struct MappedTrace {
  std::span<const std::uint8_t> bytes;
};

/* Owning handle: the bytes stay mapped for as long as any copy lives, and are
   released when the last one goes -- on whichever thread drops it. Handles are
   independent of one another and of the store that produced them (they may
   outlive it). There is deliberately no unmap-by-id: the store used to keep one
   mapping per trace id, so a second mapping of the same id replaced the first
   and the next unmap tore down another caller's live mapping (issue #63). */
using MappedTracePtr = std::shared_ptr<const MappedTrace>;

class ITraceStore {
 public:
  virtual ~ITraceStore() = default;
  virtual void WriteTrace(const TraceRecord& meta, std::span<const std::uint8_t> data) = 0;
  /* Map `trace_id` for reading. Every call returns a fresh, independent handle.
     An empty trace yields a handle with empty bytes. Throws if the id is not
     indexed or the file cannot be mapped. */
  virtual MappedTracePtr MapTrace(const std::string& trace_id) = 0;
  virtual std::vector<TraceRecord> ListTraces(const std::string& simulation_id) = 0;
  virtual std::vector<TraceRecord> ListAllTraces() = 0;
};

class FlatFileTraceStore : public ITraceStore {
 public:
  explicit FlatFileTraceStore(const std::string& index_db_path);
  ~FlatFileTraceStore() override;

  /* Rewriting a trace while handles to it are live is safe: the old file is
     unlinked before the new one is created, so existing handles keep reading
     the old inode. AppendTraceRecord grows a file in place, which leaves an
     existing mapping's (fixed) length intact. */
  void WriteTrace(const TraceRecord& meta, std::span<const std::uint8_t> data) override;
  void AppendTraceRecord(const TraceRecord& meta, std::span<const std::uint8_t> record_data);
  MappedTracePtr MapTrace(const std::string& trace_id) override;
  std::vector<TraceRecord> ListTraces(const std::string& simulation_id) override;
  std::vector<TraceRecord> ListAllTraces() override;

 private:
  // The only shared state; opened SQLITE_OPEN_FULLMUTEX, so concurrent calls
  // are serialised by sqlite. Mappings are owned by their handles, not here.
  sqlite3* db_{nullptr};
};

}  // namespace boat::store
