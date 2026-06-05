// amaru-mini-protocol-decode-blockfetch shim.
// Decodes one BlockFetch mini-protocol wire-format message:
//   RequestRange = [0, from_point, through_point]
//   ClientDone   = [1]
//   StartBatch   = [2]
//   NoBlocks     = [3]
//   Block        = [4, tag(24) block_bytes]
//   BatchDone    = [5]

use std::io::{self, Read};
use std::process::ExitCode;

fn decode_point(d: &mut minicbor::Decoder<'_>) -> Result<(), String> {
    let len = d
        .array()
        .map_err(|e| format!("point array: {e}"))?
        .ok_or_else(|| "indefinite-length point not allowed".to_string())?;
    match len {
        0 => Ok(()),
        2 => {
            let _slot = d.u64().map_err(|e| format!("point slot: {e}"))?;
            let hash = d.bytes().map_err(|e| format!("point hash: {e}"))?;
            if hash.len() != 32 {
                return Err(format!("point hash length {}, expected 32", hash.len()));
            }
            Ok(())
        }
        _ => Err(format!("point length {len}, expected 0 or 2")),
    }
}

fn decode(data: &[u8]) -> Result<(), String> {
    let mut d = minicbor::Decoder::new(data);
    let len = d
        .array()
        .map_err(|e| format!("message array: {e}"))?
        .ok_or_else(|| "indefinite-length blockfetch message not allowed".to_string())?;
    let key: u32 = d.u32().map_err(|e| format!("message key: {e}"))?;
    match (len, key) {
        (3, 0) => {
            decode_point(&mut d)?;
            decode_point(&mut d)?;
        }
        (1, 1) | (1, 2) | (1, 3) | (1, 5) => {}
        (2, 4) => {
            let tag = d.tag().map_err(|e| format!("block tag: {e}"))?;
            if u64::from(tag) != 24 {
                return Err(format!("block tag {}, expected 24", u64::from(tag)));
            }
            let _bytes = d.bytes().map_err(|e| format!("block bytes: {e}"))?;
        }
        _ => {
            return Err(format!(
                "unexpected blockfetch message (len, key) = ({len}, {key})"
            ))
        }
    }
    if d.position() != data.len() {
        return Err(format!("trailing bytes at position {}", d.position()));
    }
    Ok(())
}

fn main() -> ExitCode {
    let mut buf = Vec::new();
    if let Err(e) = io::stdin().read_to_end(&mut buf) {
        println!("ERR stdin read: {e}");
        return ExitCode::from(1);
    }
    match decode(&buf) {
        Ok(()) => {
            println!("OK");
            ExitCode::from(0)
        }
        Err(msg) => {
            println!("ERR {msg}");
            ExitCode::from(1)
        }
    }
}
