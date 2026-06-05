// amaru-mini-protocol-decode-chainsync shim.
// Decodes one ChainSync mini-protocol wire-format message:
//   RequestNext       = [0]
//   AwaitReply        = [1]
//   RollForward       = [2, header_content, tip]
//   RollBackward      = [3, point, tip]
//   FindIntersect     = [4, [point...]]
//   IntersectFound    = [5, point, tip]
//   IntersectNotFound = [6, tip]
//   Done              = [7]

use std::io::{self, Read};
use std::process::ExitCode;

fn expect_len(actual: u64, expected: u64, context: &str) -> Result<(), String> {
    if actual == expected {
        Ok(())
    } else {
        Err(format!("{context} length {actual}, expected {expected}"))
    }
}

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

fn decode_tip(d: &mut minicbor::Decoder<'_>) -> Result<(), String> {
    let len = d
        .array()
        .map_err(|e| format!("tip array: {e}"))?
        .ok_or_else(|| "indefinite-length tip not allowed".to_string())?;
    expect_len(len, 2, "tip")?;
    decode_point(d)?;
    let _height = d.u64().map_err(|e| format!("tip block height: {e}"))?;
    Ok(())
}

fn decode_header_content(d: &mut minicbor::Decoder<'_>) -> Result<(), String> {
    let len = d
        .array()
        .map_err(|e| format!("header_content array: {e}"))?
        .ok_or_else(|| "indefinite-length header_content not allowed".to_string())?;
    let era = d.u8().map_err(|e| format!("header_content era: {e}"))?;
    match era {
        0 => {
            expect_len(len, 2, "byron header_content")?;
            let prefix_len = d
                .array()
                .map_err(|e| format!("byron header prefix array: {e}"))?
                .ok_or_else(|| "indefinite-length byron prefix not allowed".to_string())?;
            expect_len(prefix_len, 2, "byron header prefix")?;
            let _prefix_a = d.u8().map_err(|e| format!("byron prefix a: {e}"))?;
            let _prefix_b = d.u64().map_err(|e| format!("byron prefix b: {e}"))?;
            let _tag = d.tag().map_err(|e| format!("byron header tag: {e}"))?;
            let _bytes = d.bytes().map_err(|e| format!("byron header bytes: {e}"))?;
            Ok(())
        }
        1..=7 => {
            expect_len(len, 2, "header_content")?;
            let _tag = d.tag().map_err(|e| format!("header tag: {e}"))?;
            let _bytes = d.bytes().map_err(|e| format!("header bytes: {e}"))?;
            Ok(())
        }
        _ => Err(format!("unknown header_content era variant: {era}")),
    }
}

fn decode(data: &[u8]) -> Result<(), String> {
    let mut d = minicbor::Decoder::new(data);
    let len = d
        .array()
        .map_err(|e| format!("message array: {e}"))?
        .ok_or_else(|| "indefinite-length chainsync message not allowed".to_string())?;
    let key: u32 = d.u32().map_err(|e| format!("message key: {e}"))?;
    match (len, key) {
        (1, 0) | (1, 1) | (1, 7) => {}
        (3, 2) => {
            decode_header_content(&mut d)?;
            decode_tip(&mut d)?;
        }
        (3, 3) | (3, 5) => {
            decode_point(&mut d)?;
            decode_tip(&mut d)?;
        }
        (2, 4) => {
            let points = d
                .array()
                .map_err(|e| format!("find_intersect points array: {e}"))?
                .ok_or_else(|| "indefinite-length point list not allowed".to_string())?;
            for _ in 0..points {
                decode_point(&mut d)?;
            }
        }
        (2, 6) => decode_tip(&mut d)?,
        _ => {
            return Err(format!(
                "unexpected chainsync message (len, key) = ({len}, {key})"
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
