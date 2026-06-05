// amaru-mini-protocol-decode-localstatequery shim.
// Decodes one LocalStateQuery mini-protocol message envelope:
//   Acquire(Some point) = [0, point]
//   Acquired            = [1]
//   Failure             = [2, failure_code]
//   Query               = [3, any_cbor]
//   Result              = [4, any_cbor]
//   Release             = [5]
//   ReAcquire(Some p)   = [6, point]
//   Done                = [7]
//   Acquire(None)       = [8]
//   ReAcquire(None)     = [9]
//
// The point/query/result payloads are treated as bounded CBOR items. This
// keeps the decoder focused on the protocol envelope shape the AFLNet harness
// uses for stateful transition validation.

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
        .ok_or_else(|| "indefinite-length localstatequery message not allowed".to_string())?;
    let key = d.u16().map_err(|e| format!("message key: {e}"))?;

    match (len, key) {
        (2, 0) | (2, 6) => {
            skip_item(&mut d, "point")?;
        }
        (1, 1) | (1, 5) | (1, 7) | (1, 8) | (1, 9) => {}
        (2, 2) => {
            let _failure_code = d.u16().map_err(|e| format!("failure code: {e}"))?;
        }
        (2, 3) => {
            skip_item(&mut d, "query payload")?;
        }
        (2, 4) => {
            skip_item(&mut d, "result payload")?;
        }
        _ => {
            return Err(format!(
                "unexpected localstatequery message (len, key) = ({len}, {key})"
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
