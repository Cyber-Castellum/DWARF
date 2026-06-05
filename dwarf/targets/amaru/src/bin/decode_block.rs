//! amaru-cbor-decode-block shim.
//! Decodes a full block body (block header + transaction bodies + witnesses + aux).

use amaru_kernel::{Block, from_cbor_no_leftovers};
use std::io::{self, Read};
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut buf = Vec::new();
    if io::stdin().read_to_end(&mut buf).is_err() {
        println!("ERR stdin read failed");
        return ExitCode::from(1);
    }
    match from_cbor_no_leftovers::<Block>(&buf) {
        Ok(_block) => {
            println!("OK");
            ExitCode::SUCCESS
        }
        Err(e) => {
            println!("ERR {}", e);
            ExitCode::from(1)
        }
    }
}
