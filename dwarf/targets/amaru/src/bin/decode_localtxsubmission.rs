// amaru-mini-protocol-decode-localtxsubmission shim.
// Decodes one LocalTxSubmission mini-protocol message envelope:
//   SubmitTx = [0, era_tx]
//   AcceptTx = [1]
//   RejectTx = [2, rejection]
//   Done     = [3]
//
// The transaction and rejection payloads are treated as bounded CBOR items.
// This preserves decoder-safety and envelope validation without coupling the
// harness to full ledger validation semantics.

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
        .ok_or_else(|| "indefinite-length localtxsubmission message not allowed".to_string())?;
    let key = d.u16().map_err(|e| format!("message key: {e}"))?;

    match (len, key) {
        (2, 0) => {
            skip_item(&mut d, "submit tx payload")?;
        }
        (1, 1) | (1, 3) => {}
        (2, 2) => {
            skip_item(&mut d, "reject payload")?;
        }
        _ => {
            return Err(format!(
                "unexpected localtxsubmission message (len, key) = ({len}, {key})"
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
