// amaru-mini-protocol-decode-peersharing shim.
// Decodes one PeerSharing mini-protocol message envelope:
//   ShareRequest = [0, amount_u8]
//   SharePeers   = [1, [peer_address, ...]]
//   Done         = [2]
//
// The checked-out Amaru tree does not currently expose a PeerSharing codec
// under crates/. This shim follows Cardano PeerSharing wire evidence from
// ouroboros-network and the local Pallas PeerSharing codec, constrained to
// the cardano-node remote-address port width for cross-implementation checks.

use std::io::{self, Read};
use std::process::ExitCode;

use minicbor::data::Type;

fn read_list(
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

fn decode_peer_address(d: &mut minicbor::Decoder<'_>) -> Result<(), String> {
    let len = d
        .array()
        .map_err(|e| format!("peer address array: {e}"))?
        .ok_or_else(|| "indefinite peer address not allowed".to_string())?;
    let key = d.u16().map_err(|e| format!("peer address key: {e}"))?;
    match (len, key) {
        (3, 0) => {
            let _ipv4 = d.u32().map_err(|e| format!("ipv4 word: {e}"))?;
            let _port = d.u16().map_err(|e| format!("ipv4 port: {e}"))?;
        }
        (6, 1) => {
            let _word1 = d.u32().map_err(|e| format!("ipv6 word1: {e}"))?;
            let _word2 = d.u32().map_err(|e| format!("ipv6 word2: {e}"))?;
            let _word3 = d.u32().map_err(|e| format!("ipv6 word3: {e}"))?;
            let _word4 = d.u32().map_err(|e| format!("ipv6 word4: {e}"))?;
            let _port = d.u16().map_err(|e| format!("ipv6 port: {e}"))?;
        }
        _ => {
            return Err(format!(
                "unexpected peer address (len, key) = ({len}, {key})"
            ))
        }
    }
    Ok(())
}

fn decode(data: &[u8]) -> Result<(), String> {
    let mut d = minicbor::Decoder::new(data);
    let len = d
        .array()
        .map_err(|e| format!("message array: {e}"))?
        .ok_or_else(|| "indefinite-length peersharing message not allowed".to_string())?;
    let key = d.u16().map_err(|e| format!("message key: {e}"))?;

    match (len, key) {
        (2, 0) => {
            let _amount = d.u8().map_err(|e| format!("share amount: {e}"))?;
        }
        (2, 1) => {
            read_list(&mut d, "share peers", decode_peer_address)?;
        }
        (1, 2) => {}
        _ => {
            return Err(format!(
                "unexpected peersharing message (len, key) = ({len}, {key})"
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
