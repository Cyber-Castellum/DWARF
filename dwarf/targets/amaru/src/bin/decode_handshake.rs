// amaru-mini-protocol-decode-handshake shim.
// Decodes one Handshake mini-protocol wire-format message:
//   MsgProposeVersions = [0, version_table]
//   MsgAcceptVersion   = [1, version_number, version_data]
//   MsgRefuse          = [2, refuse_reason]
//   MsgQueryReply      = [3, version_table]
//
// Version table is a definite CBOR map of version_number -> version_data.
// Version data is [network_magic, initiator_only_diffusion_mode] for older
// versions and [network_magic, initiator_only_diffusion_mode, peer_sharing,
// query] for v11+.

use std::io::{self, Read};
use std::process::ExitCode;

fn decode_version_data(d: &mut minicbor::Decoder<'_>, version: u64) -> Result<(), String> {
    let len = d
        .array()
        .map_err(|e| format!("version_data array: {e}"))?
        .ok_or_else(|| "indefinite-length version_data not allowed".to_string())?;
    let expected = if version >= 11 { 4 } else { 2 };
    if len != expected {
        return Err(format!(
            "version_data length {len}, expected {expected} for version {version}"
        ));
    }
    let _network_magic = d.u64().map_err(|e| format!("network_magic: {e}"))?;
    let _initiator_only = d
        .bool()
        .map_err(|e| format!("initiator_only_diffusion_mode: {e}"))?;
    if version >= 11 {
        let peer_sharing = d.u8().map_err(|e| format!("peer_sharing: {e}"))?;
        if peer_sharing > 1 {
            return Err(format!("peer_sharing out of range: {peer_sharing}"));
        }
        let _query = d.bool().map_err(|e| format!("query: {e}"))?;
    }
    Ok(())
}

fn decode_version_table(d: &mut minicbor::Decoder<'_>) -> Result<(), String> {
    let len = d
        .map()
        .map_err(|e| format!("version_table map: {e}"))?
        .ok_or_else(|| "indefinite-length version_table not allowed".to_string())?;
    for _ in 0..len {
        let version = d.u64().map_err(|e| format!("version_number: {e}"))?;
        decode_version_data(d, version)?;
    }
    Ok(())
}

fn decode_refuse_reason(d: &mut minicbor::Decoder<'_>) -> Result<(), String> {
    let len = d
        .array()
        .map_err(|e| format!("refuse_reason array: {e}"))?
        .ok_or_else(|| "indefinite-length refuse_reason not allowed".to_string())?;
    let key: u32 = d.u32().map_err(|e| format!("refuse_reason key: {e}"))?;
    match (len, key) {
        (2, 0) => {
            let versions = d
                .array()
                .map_err(|e| format!("version_mismatch versions: {e}"))?
                .ok_or_else(|| "indefinite-length version list not allowed".to_string())?;
            for _ in 0..versions {
                let _version = d
                    .u64()
                    .map_err(|e| format!("version_mismatch version: {e}"))?;
            }
        }
        (3, 1) | (3, 2) => {
            let _version = d.u64().map_err(|e| format!("refuse version: {e}"))?;
            let _message = d.str().map_err(|e| format!("refuse message: {e}"))?;
        }
        _ => {
            return Err(format!(
                "unexpected refuse_reason (len, key) = ({len}, {key})"
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
        .ok_or_else(|| "indefinite-length handshake message not allowed".to_string())?;
    let key: u32 = d.u32().map_err(|e| format!("message key: {e}"))?;
    match (len, key) {
        (2, 0) | (2, 3) => decode_version_table(&mut d)?,
        (3, 1) => {
            let version = d.u64().map_err(|e| format!("accept version: {e}"))?;
            decode_version_data(&mut d, version)?;
        }
        (2, 2) => decode_refuse_reason(&mut d)?,
        _ => {
            return Err(format!(
                "unexpected handshake message (len, key) = ({len}, {key})"
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
