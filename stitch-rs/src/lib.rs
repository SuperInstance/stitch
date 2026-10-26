//! stitch — Rust skeleton of the substrate primitive.
//!
//! This is a SKELETON demonstrating cross-language byte-exactness with the
//! Python reference implementation. It implements:
//!   - Constants (LOG2_3, FNV_OFFSET, FNV_PRIME, MASK_64)
//!   - FNV-1a 64-bit hash (matching the Python `_fnv1a`)
//!   - Tenant struct with conservation law enforcement
//!   - Cell struct
//!   - Ledger struct with append-only JSONL + hash chain
//!   - The `bind` stitch (the only stitch implemented so far)
//!
//! The remaining stitches (link, effect, view, tick, and the governance
//! stitches attest, delegate, contest, merger, revoke, withdraw) are not
//! yet implemented — they follow the same pattern as bind. See SPEC.md
//! for the canonical wire format.
//!
//! The full Rust implementation is on the v0.2 roadmap. This skeleton proves
//! the byte-exactness contract holds across languages.

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::sync::Mutex;

// ============================================================================
// Constants (canonical — see SPEC.md)
// ============================================================================

/// log₂3 ≈ 1.585 — the per-tick per-tenant conservation budget
pub const LOG2_3: f64 = 1.584962500721156;

/// FNV-1a 64-bit offset basis
pub const FNV_OFFSET: u64 = 0xcbf29ce484222325;

/// FNV-1a 64-bit prime
pub const FNV_PRIME: u64 = 0x100000001b3;

// ============================================================================
// FNV-1a 64-bit hash — byte-exact match to Python's `_fnv1a`
// ============================================================================

/// FNV-1a 64-bit hash of a string, seeded with the previous hash.
///
/// This MUST produce the same output as the Python implementation:
///   h = seed & MASK_64
///   for b in data.encode("utf-8"):
///       h ^= b
///       h = (h * FNV_PRIME) & MASK_64
///   return h
pub fn fnv1a_64(data: &str, seed: u64) -> u64 {
    let mut h = seed;
    for b in data.as_bytes() {
        h ^= *b as u64;
        h = h.wrapping_mul(FNV_PRIME);
    }
    h
}

// ============================================================================
// Tenant — the unit of accountability
// ============================================================================

/// A tenant is the unit of accountability. Each tenant has a γ+η≤log₂3 budget
/// per tick.
#[derive(Debug, Clone)]
pub struct Tenant {
    pub identity: String,
    pub budget: f64,
    pub gamma: f64,
    pub pending: f64,
}

impl Tenant {
    pub fn new(identity: impl Into<String>) -> Self {
        Self {
            identity: identity.into(),
            budget: LOG2_3,
            gamma: 0.0,
            pending: 0.0,
        }
    }

    pub fn with_budget(identity: impl Into<String>, budget: f64) -> Self {
        Self {
            identity: identity.into(),
            budget,
            gamma: 0.0,
            pending: 0.0,
        }
    }

    /// Headroom remaining: η = C - γ - pending
    pub fn eta(&self) -> f64 {
        (self.budget - self.gamma - self.pending).max(0.0)
    }

    /// Check if a write costing `cost` bits of surprise would fit in the budget.
    pub fn can_afford(&self, cost: f64) -> bool {
        self.gamma + self.pending + cost <= self.budget + 1e-12
    }

    /// Charge γ by `cost` bits. Returns false if it would violate the conservation law.
    pub fn charge(&mut self, cost: f64) -> bool {
        if !self.can_afford(cost) {
            return false;
        }
        self.gamma += cost;
        true
    }

    /// At each TICK, γ resets.
    pub fn reset_tick(&mut self) {
        self.gamma = 0.0;
        self.pending = 0.0;
    }
}

// ============================================================================
// Cell — the smallest unit of accountable state
// ============================================================================

/// A cell is the smallest unit of accountable state.
#[derive(Debug, Clone)]
pub struct Cell {
    pub id: String,
    pub tenant: String,
    pub kind: String,
    pub payload: serde_json::Value,
    pub parent_hash: Option<String>,
    pub created_at: f64,
}

// ============================================================================
// Ledger — append-only JSONL with FNV-1a 64-bit hash chain
// ============================================================================

/// Append-only JSONL ledger with FNV-1a 64-bit hash chain.
///
/// Each entry is one JSON object on one line, sorted keys, no whitespace.
/// Each entry's `h` field is the FNV-1a 64-bit hash of this entry's content,
/// seeded with the previous entry's hash (FNV_OFFSET for the first entry).
pub struct Ledger {
    path: std::path::PathBuf,
    last_hash: u64,
    file: Mutex<File>,
}

impl Ledger {
    pub fn open(path: impl AsRef<Path>) -> std::io::Result<Self> {
        let path = path.as_ref().to_path_buf();
        let last_hash = Self::compute_last_hash(&path)?;
        let file = OpenOptions::new().append(true).create(true).open(&path)?;
        Ok(Self {
            path,
            last_hash,
            file: Mutex::new(file),
        })
    }

    fn compute_last_hash(path: &Path) -> std::io::Result<u64> {
        if !path.exists() {
            return Ok(FNV_OFFSET);
        }
        let file = File::open(path)?;
        let reader = BufReader::new(file);
        let mut last = FNV_OFFSET;
        for line in reader.lines() {
            let line = line?;
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            // Parse JSON and look for the `h` field
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(trimmed) {
                if let Some(h_str) = v.get("h").and_then(|x| x.as_str()) {
                    if let Ok(h) = u64::from_str_radix(h_str, 16) {
                        last = h;
                    }
                }
            }
        }
        Ok(last)
    }

    /// Append an entry to the ledger. Returns the entry's hash as a 16-char hex string.
    ///
    /// The entry must NOT have an `h` field — this method computes and adds it.
    pub fn append(&mut self, mut entry: serde_json::Value) -> Result<String, StitchError> {
        // Reject entries that already carry a hash. (Scoped borrow: the
        // mutable borrow must end before the immutable serialize below —
        // as originally shipped this function did not compile, E0502.)
        {
            let obj = entry.as_object_mut().ok_or(StitchError::InvalidEntry)?;
            if obj.contains_key("h") {
                return Err(StitchError::InvalidEntry);
            }
        }
        // Serialize without the hash. serde_json's default Map is a
        // BTreeMap, so keys serialize in sorted order — canonical form.
        let line_without_hash = serde_json::to_string(&entry)
            .map_err(|e| StitchError::SerializationError(e.to_string()))?;
        // Compute hash
        let h = fnv1a_64(&line_without_hash, self.last_hash);
        let h_str = format!("{:016x}", h);
        entry
            .as_object_mut()
            .ok_or(StitchError::InvalidEntry)?
            .insert("h".to_string(), serde_json::Value::String(h_str.clone()));
        // Re-serialize with the hash
        let line_with_hash = serde_json::to_string(&entry)
            .map_err(|e| StitchError::SerializationError(e.to_string()))?;
        // Write
        let mut file = self.file.lock().unwrap();
        writeln!(file, "{}", line_with_hash)?;
        file.flush()?;
        self.last_hash = h;
        Ok(h_str)
    }

    /// Verify the hash chain. Returns true if all hashes are consistent.
    pub fn verify(&self) -> bool {
        let file = match File::open(&self.path) {
            Ok(f) => f,
            Err(_) => return true, // empty ledger is valid
        };
        let reader = BufReader::new(file);
        let mut last = FNV_OFFSET;
        for line in reader.lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => return false,
            };
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            let mut entry: serde_json::Value = match serde_json::from_str(trimmed) {
                Ok(v) => v,
                Err(_) => return false,
            };
            let obj = match entry.as_object_mut() {
                Some(o) => o,
                None => return false,
            };
            let h_str = match obj.remove("h").and_then(|v| v.as_str().map(String::from)) {
                Some(s) => s,
                None => return false,
            };
            let line_without_hash = match serde_json::to_string(&entry) {
                Ok(s) => s,
                Err(_) => return false,
            };
            let expected = fnv1a_64(&line_without_hash, last);
            if format!("{:016x}", expected) != h_str {
                return false;
            }
            last = match u64::from_str_radix(&h_str, 16) {
                Ok(v) => v,
                Err(_) => return false,
            };
        }
        true
    }
}

// ============================================================================
// Errors
// ============================================================================

#[derive(Debug)]
pub enum StitchError {
    ConservationError(String),
    InvalidEntry,
    SerializationError(String),
    IoError(std::io::Error),
}

impl From<std::io::Error> for StitchError {
    fn from(e: std::io::Error) -> Self {
        StitchError::IoError(e)
    }
}

// ============================================================================
// Stitches — data plane (bind and effect demonstrated)
// ============================================================================

/// BIND: create a new cell.
///
/// Charges `cost` bits of γ (default 0). Returns the new Cell.
/// Returns an error if the cost would violate the conservation law.
pub fn bind(
    tenant: &mut Tenant,
    ledger: &mut Ledger,
    kind: &str,
    payload: serde_json::Value,
    parent_hash: Option<&str>,
    cost: f64,
) -> Result<Cell, StitchError> {
    if !tenant.charge(cost) {
        return Err(StitchError::ConservationError(format!(
            "Tenant {} cannot afford to bind (γ={}, cost={}, budget={})",
            tenant.identity, tenant.gamma, cost, tenant.budget
        )));
    }
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as i64)
        .unwrap_or(0);
    let cell_id = compute_cell_id(&tenant.identity, kind, &payload, parent_hash, ts);
    let cell = Cell {
        id: cell_id.clone(),
        tenant: tenant.identity.clone(),
        kind: kind.to_string(),
        payload: payload.clone(),
        parent_hash: parent_hash.map(String::from),
        created_at: ts as f64 / 1e9,
    };
    let mut entry = serde_json::json!({
        "t": ts,
        "ns": tenant.identity,
        "op": "BIND",
        "cell": cell_id,
        "kind": kind,
        "payload": payload,
        "parent": parent_hash,
        "g": tenant.gamma,
    });
    ledger.append(entry.take())?;
    Ok(cell)
}

/// Compute the cell ID — first 16 hex chars of SHA-256 of creation material.
fn compute_cell_id(
    tenant: &str,
    kind: &str,
    payload: &serde_json::Value,
    parent_hash: Option<&str>,
    ts: i64,
) -> String {
    use sha2::{Digest, Sha256};
    let payload_json = serde_json::to_string(payload).unwrap_or_default();
    let material = format!("{}:{}:{}:{}:{}", tenant, kind, payload_json,
        parent_hash.unwrap_or(""), ts);
    let mut hasher = Sha256::new();
    hasher.update(material.as_bytes());
    let result = hasher.finalize();
    let hex: String = result.iter().map(|b| format!("{:02x}", b)).collect();
    hex[..16].to_string()
}

// ============================================================================
// Tests — cross-language byte-exactness with Python
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_fnv1a_known_vectors() {
        // Empty string → FNV_OFFSET
        assert_eq!(fnv1a_64("", FNV_OFFSET), FNV_OFFSET);
        // "a" → 0xaf63dc4c8601ec8c
        assert_eq!(fnv1a_64("a", FNV_OFFSET), 0xaf63dc4c8601ec8c);
        // "foobar" → 0x85944171f73967e8
        assert_eq!(fnv1a_64("foobar", FNV_OFFSET), 0x85944171f73967e8);
    }

    #[test]
    fn test_tenant_budget() {
        let mut t = Tenant::new("alice");
        assert!(t.can_afford(1.0));
        assert!(t.charge(1.0));
        assert_eq!(t.gamma, 1.0);
        assert!(!t.can_afford(LOG2_3));
    }

    #[test]
    fn test_cross_lang_byte_exact_with_python() {
        // Cross-language byte-exactness at the ledger layer.
        // The two fixed entries below were appended by the Python reference
        // implementation (stitch-python, Ledger.append), which produced:
        //   h1 = b513cabe780e2894, h2 = 24c56ec21ef20816
        // and the exact file bytes asserted at the end of this test.
        // If this test passes, the Rust and Python ledgers agree byte-for-byte
        // on identical input — the core of the SPEC.md conformance contract.
        let temp = std::env::temp_dir();
        let path = temp.join(format!("stitch_rs_xlang_{}.jsonl", std::process::id()));
        let _ = std::fs::remove_file(&path);

        let mut ledger = Ledger::open(&path).unwrap();
        let h1 = ledger.append(json!({
            "t": 1000i64, "ns": "alice", "op": "BIND", "cell": "abc", "g": 0.0,
        })).unwrap();
        let h2 = ledger.append(json!({
            "t": 2000i64, "ns": "alice", "op": "TICK", "g": 0.0,
        })).unwrap();
        assert_eq!(h1, "b513cabe780e2894");
        assert_eq!(h2, "24c56ec21ef20816");
        assert!(ledger.verify());
        drop(ledger);

        let bytes = std::fs::read_to_string(&path).unwrap();
        let expected = concat!(
            "{\"cell\":\"abc\",\"g\":0.0,\"h\":\"b513cabe780e2894\",\"ns\":\"alice\",\"op\":\"BIND\",\"t\":1000}\n",
            "{\"g\":0.0,\"h\":\"24c56ec21ef20816\",\"ns\":\"alice\",\"op\":\"TICK\",\"t\":2000}\n",
        );
        assert_eq!(bytes, expected);
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn test_ledger_append_and_verify() {
        let temp = std::env::temp_dir();
        let path = temp.join(format!("stitch_rs_test_{}.jsonl", std::process::id()));
        let _ = std::fs::remove_file(&path);

        let mut ledger = Ledger::open(&path).unwrap();
        ledger.append(json!({
            "t": 1i64,
            "ns": "alice",
            "op": "BIND",
            "cell": "abc",
            "g": 0.0,
        })).unwrap();
        ledger.append(json!({
            "t": 2i64,
            "ns": "alice",
            "op": "TICK",
            "g": 0.0,
        })).unwrap();

        assert!(ledger.verify());
        let _ = std::fs::remove_file(&path);
    }
}
