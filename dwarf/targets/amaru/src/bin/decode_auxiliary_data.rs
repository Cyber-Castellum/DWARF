//! amaru-cbor-decode-auxiliary-data shim.

use amaru_kernel::{AuxiliaryData, from_cbor_no_leftovers};
use std::io::{self, Read};
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut buf = Vec::new();
    if io::stdin().read_to_end(&mut buf).is_err() {
        println!("ERR stdin read failed");
        return ExitCode::from(1);
    }
    match from_cbor_no_leftovers::<AuxiliaryData>(&buf) {
        Ok(_aux) => {
            println!("OK");
            ExitCode::SUCCESS
        }
        Err(e) => {
            println!("ERR {}", e);
            ExitCode::from(1)
        }
    }
}
