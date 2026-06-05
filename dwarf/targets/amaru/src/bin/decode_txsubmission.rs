// amaru-mini-protocol-decode-txsubmission shim.
// Decodes one TxSubmission mini-protocol message envelope:
//   RequestTxIds = [0, blocking_bool, ack_u16, req_u16]
//   ReplyTxIds   = [1, [[txid, size_u32], ...]]
//   RequestTxs   = [2, [txid, ...]]
//   ReplyTxs     = [3, [tx, ...]]
//   Done         = [4]
//   Init         = [6]
//
// TxSubmission2 is generic over txid/tx payload codecs. This shim validates
// the protocol envelope and skips each generic payload as one bounded CBOR item.

use std::io::{self, Read};
use std::process::ExitCode;

use minicbor::data::Type;

fn skip_list_items(
    d: &mut minicbor::Decoder<'_>,
    what: &str,
    mut item: impl FnMut(&mut minicbor::Decoder<'_>) -> Result<(), String>,
) -> Result<(), String> {
    match d.array().map_err(|e| format!("{what} array: {e}"))? {
        Some(len) => {
            for _ in 0..len {
                item(d)?;
            }
        }
        None => loop {
            if d.datatype().map_err(|e| format!("{what} item type: {e}"))? == Type::Break {
                d.skip().map_err(|e| format!("{what} break: {e}"))?;
                break;
            }
            item(d)?;
        },
    }
    Ok(())
}

fn skip_item(d: &mut minicbor::Decoder<'_>, what: &str) -> Result<(), String> {
    d.skip().map_err(|e| format!("{what}: {e}"))
}

fn skip_txid_and_size(d: &mut minicbor::Decoder<'_>) -> Result<(), String> {
    let len = d
        .array()
        .map_err(|e| format!("txid-size pair array: {e}"))?
        .ok_or_else(|| "indefinite txid-size pair not allowed".to_string())?;
    if len != 2 {
        return Err(format!("txid-size pair length {len}, expected 2"));
    }
    skip_item(d, "txid")?;
    let _size = d.u32().map_err(|e| format!("tx size: {e}"))?;
    Ok(())
}

fn decode(data: &[u8]) -> Result<(), String> {
    let mut d = minicbor::Decoder::new(data);
    let len = d
        .array()
        .map_err(|e| format!("message array: {e}"))?
        .ok_or_else(|| "indefinite-length txsubmission message not allowed".to_string())?;
    let key = d.u16().map_err(|e| format!("message key: {e}"))?;

    match (len, key) {
        (4, 0) => {
            let _blocking = d.bool().map_err(|e| format!("blocking flag: {e}"))?;
            let _ack = d.u16().map_err(|e| format!("ack count: {e}"))?;
            let _req = d.u16().map_err(|e| format!("request count: {e}"))?;
        }
        (2, 1) => {
            skip_list_items(&mut d, "reply txids", skip_txid_and_size)?;
        }
        (2, 2) => {
            skip_list_items(&mut d, "request txids", |d| skip_item(d, "txid"))?;
        }
        (2, 3) => {
            skip_list_items(&mut d, "reply txs", |d| skip_item(d, "tx"))?;
        }
        (1, 4) | (1, 6) => {}
        _ => {
            return Err(format!(
                "unexpected txsubmission message (len, key) = ({len}, {key})"
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
