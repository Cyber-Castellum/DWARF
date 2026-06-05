// amaru-mini-protocol-decode-keep-alive shim.
// Decodes a KeepAlive mini-protocol wire-format message:
//   MsgKeepAlive          = [0, cookie:u16]
//   MsgKeepAliveResponse  = [1, cookie:u16]
//   MsgDone               = [2]
// using minicbor (already a transitive dep through amaru-kernel/pallas).

use std::io::{self, Read};
use std::process::ExitCode;

fn decode(data: &[u8]) -> Result<(), String> {
    let mut d = minicbor::Decoder::new(data);
    let len = d.array().map_err(|e| format!("array: {e}"))?
        .ok_or_else(|| "indefinite-length array not allowed".to_string())?;
    let key: u32 = d.u32().map_err(|e| format!("key: {e}"))?;
    match (len, key) {
        (2, 0) | (2, 1) => {
            let _cookie: u16 = d.u16().map_err(|e| format!("cookie: {e}"))?;
        }
        (1, 2) => {}
        _ => return Err(format!("unexpected (len, key) = ({len}, {key})")),
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
