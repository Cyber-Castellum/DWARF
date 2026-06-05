// amaru-mini-protocol-decode-localtxmonitor shim.
// Decodes one LocalTxMonitor mini-protocol message envelope:
//   Done                    = [0]
//   Acquire                 = [1]
//   Acquired                = [2, slot]
//   Release                 = [3]
//   AwaitAcquire            = [4]
//   RequestNextTx           = [5]
//   ResponseNextTx(None)    = [6]
//   ResponseNextTx(Some tx) = [6, tx]
//   RequestHasTx            = [7, txid]
//   ResponseHasTx           = [8, bool]
//   RequestSizeAndCapacity  = [9]
//   ResponseSizeAndCapacity = [10, [capacity, size, count]]

use std::io::{self, Read};
use std::process::ExitCode;

fn skip_item(d: &mut minicbor::Decoder<'_>, what: &str) -> Result<(), String> {
    d.skip().map_err(|e| format!("{what}: {e}"))
}

fn decode(data: &[u8]) -> Result<(), String> {
    let mut d = minicbor::Decoder::new(data);
    let len = d
        .array()
        .map_err(|e| format!("message array: {e}"))?
        .ok_or_else(|| "indefinite-length localtxmonitor message not allowed".to_string())?;
    let key = d.u16().map_err(|e| format!("message key: {e}"))?;

    match (len, key) {
        (1, 0) | (1, 1) | (1, 3) | (1, 4) | (1, 5) | (1, 6) | (1, 9) => {}
        (2, 2) => {
            let _slot = d.u64().map_err(|e| format!("slot: {e}"))?;
        }
        (2, 6) => {
            skip_item(&mut d, "next tx payload")?;
        }
        (2, 7) => {
            let _txid = d.str().map_err(|e| format!("txid: {e}"))?;
        }
        (2, 8) => {
            let _has_tx = d.bool().map_err(|e| format!("has tx flag: {e}"))?;
        }
        (2, 10) => {
            let nested_len = d
                .array()
                .map_err(|e| format!("size-capacity array: {e}"))?
                .ok_or_else(|| "indefinite size-capacity array not allowed".to_string())?;
            if nested_len != 3 {
                return Err(format!("size-capacity length {nested_len}, expected 3"));
            }
            let _capacity = d.u32().map_err(|e| format!("capacity bytes: {e}"))?;
            let _size = d.u32().map_err(|e| format!("size bytes: {e}"))?;
            let _count = d.u32().map_err(|e| format!("tx count: {e}"))?;
        }
        _ => {
            return Err(format!(
                "unexpected localtxmonitor message (len, key) = ({len}, {key})"
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
